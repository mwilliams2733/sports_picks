"""Paper bets are priced by the server; the client cannot set a price (plan 027)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, Odds, PaperPick, Parlay, Team
from backend.paper import pricing
from backend.tests.auth_helpers import ALL_HEADERS, OWNER_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _game(client, i=0):
    s = get_session(client.app.state.engine)
    home = Team(name=f"Home{i}", abbreviation=f"H{i}", sport="nfl")
    away = Team(name=f"Away{i}", abbreviation=f"A{i}", sport="nfl")
    s.add_all([home, away])
    s.flush()
    g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=home.id,
             away_team_id=away.id, status="scheduled")
    s.add(g)
    s.commit()
    gid = g.id
    s.close()
    return gid


def _user(client):
    return client.post("/users/", json={"name": "friend", "pin": TEST_PIN}).json()["id"]


def _finish(client, gid, home, away):
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score = "final", home, away
    s.commit()
    s.close()


# --- anti-tamper ------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"odds": 100000},
    {"pick_value": "HOME +60"},
    {"line": 60},
])
def test_a_client_supplied_price_or_label_is_rejected(extra):
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100, **extra})
    assert r.status_code == 422, r.text
    s = get_session(client.app.state.engine)
    assert s.query(PaperPick).count() == 0
    s.close()


def test_a_parlay_leg_with_a_price_is_rejected():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    for gid in (a, b):
        seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME", "odds": 5000},
        {"game_id": b, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 422, r.text


def test_the_old_request_shape_is_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "pick_value": "HOME ML",
        "odds": -110, "stake": 100})
    assert r.status_code == 422


def test_a_side_that_does_not_fit_the_market_is_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "Over", "stake": 100})
    assert r.status_code == 422


# --- the price charged --------------------------------------------------------

def test_the_bet_is_charged_the_quoted_consensus():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="dk", spread_home_price=-110)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="fd", spread_home_price=-130)
    uid = _user(client)
    quoted = {(q["pick_type"], q["side"]): q
              for q in client.get(f"/paper/quotes?game_id={gid}").json()["quotes"]}
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["odds"] == quoted[("spread", "HOME")]["odds"] == -120
    assert body["pick_value"] == "HOME -3.5" and body["line"] == -3.5
    s = get_session(client.app.state.engine)
    pick = s.query(PaperPick).one()
    assert (pick.pick_value, pick.odds) == ("HOME -3.5", -120)
    s.close()


def test_a_moved_price_is_charged_at_the_new_price():
    """Review Focus 5 (API half)."""
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    shown = client.get(f"/paper/quotes?game_id={gid}").json()["quotes"][0]["odds"]
    s = get_session(client.app.state.engine)
    s.query(Odds).update({Odds.moneyline_home: -150})
    s.commit()
    s.close()
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert shown == -110 and r.json()["odds"] == -150


def test_a_stale_price_is_refused_with_409():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    s = get_session(client.app.state.engine)
    seven_hours_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=7)
    s.query(Odds).update({Odds.timestamp: seven_hours_ago})
    s.commit()
    s.close()
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 409
    assert r.json()["detail"] == "The price is stale — ask Marcus to refresh."


def test_an_unquoted_game_is_refused_with_409():
    client = _client()
    gid = _game(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 409


def test_a_prop_bet_is_priced_from_the_books():
    client = _client()
    gid = _game(client)
    seed_fresh_prop(client.app.state.engine, gid, odds=-125)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "prop", "prop_player": "QB One",
        "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5, "stake": 50})
    assert r.status_code == 200, r.text
    assert (r.json()["odds"], r.json()["pick_value"]) == (-125, "QB One Over 225.5 Pass Yards")


# --- parlays -----------------------------------------------------------------

def test_a_parlay_is_combined_on_the_server_from_leg_quotes():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    for gid in (a, b):
        seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": b, "pick_type": "over_under", "side": "Under"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["combined_odds"] == 264                   # two -110 legs, hand-computed in Task 1
    assert [leg["pick_value"] for leg in body["legs"]] == ["HOME ML", "Under 220.5"]
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).one().combined_odds == 264
    s.close()


def test_a_parlay_with_one_unpriceable_leg_is_refused_whole():
    client = _client()
    a, b = _game(client, 0), _game(client, 1)
    seed_fresh_odds(client.app.state.engine, a)            # b has no quotes
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": a, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": b, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 409
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).count() == 0 and s.query(PaperPick).count() == 0
    s.close()


# --- Fix round 2: same-game parlays are allowed; same-market legs are not --

def test_two_identical_legs_on_one_game_are_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 400
    assert r.json()["detail"] == "A parlay can't have two legs on the same market."
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).count() == 0 and s.query(PaperPick).count() == 0
    s.close()


def test_home_ml_and_away_ml_on_one_game_are_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": gid, "pick_type": "moneyline", "side": "AWAY"}]})
    assert r.status_code == 400
    assert r.json()["detail"] == "A parlay can't have two legs on the same market."
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).count() == 0 and s.query(PaperPick).count() == 0
    s.close()


def test_the_same_player_and_market_over_and_under_are_rejected():
    client = _client()
    gid = _game(client)
    seed_fresh_prop(client.app.state.engine, gid, outcome="Over")
    seed_fresh_prop(client.app.state.engine, gid, outcome="Under")
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
         "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5},
        {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
         "prop_market": "player_pass_yds", "outcome": "Under", "line": 225.5}]})
    assert r.status_code == 400
    assert r.json()["detail"] == "A parlay can't have two legs on the same market."
    s = get_session(client.app.state.engine)
    assert s.query(Parlay).count() == 0 and s.query(PaperPick).count() == 0
    s.close()


def test_home_ml_and_over_total_on_one_game_are_accepted():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    quoted = {(q["pick_type"], q["side"]): q
              for q in client.get(f"/paper/quotes?game_id={gid}").json()["quotes"]}
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": gid, "pick_type": "over_under", "side": "Over"}]})
    assert r.status_code == 200, r.text
    expected_odds, _ = pricing.combine([
        quoted[("moneyline", "HOME")]["odds"], quoted[("over_under", "Over")]["odds"]])
    assert r.json()["combined_odds"] == expected_odds


def test_a_prop_leg_and_a_moneyline_leg_on_one_game_are_accepted():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    seed_fresh_prop(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
         "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5},
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"}]})
    assert r.status_code == 200, r.text


def test_home_ml_and_home_spread_on_one_game_are_accepted():
    """Same-game parlays are allowed even when the legs are correlated
    (owner ruling, fix round 2)."""
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid)
    uid = _user(client)
    r = client.post(f"/users/{uid}/parlay", json={"stake": 100, "legs": [
        {"game_id": gid, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": gid, "pick_type": "spread", "side": "HOME"}]})
    assert r.status_code == 200, r.text


# --- round trip through settlement -------------------------------------------

def test_a_tied_spread_takes_the_worse_quoted_line_through_the_api():
    """Books at -3 and -4 used to price HOME -3.5, a line neither offered
    (owner reversed that 2026-10-04). A tie now goes to the line worse for
    the bettor, HOME -4, and a 27-24 home win loses at it."""
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="dk",
                    spread_home=-3.0, spread_away=3.0)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="fd",
                    spread_home=-4.0, spread_away=4.0)
    uid = _user(client)
    placed = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100}).json()
    assert placed["pick_value"] == "HOME -4"
    _finish(client, gid, 27, 24)
    assert client.post("/users/grade", headers=OWNER_HEADERS).status_code == 200
    s = get_session(client.app.state.engine)
    assert s.query(PaperPick).one().result == "loss"
    s.close()


def test_a_quoted_whole_number_spread_pushes_through_the_api():
    client = _client()
    gid = _game(client)
    seed_fresh_odds(client.app.state.engine, gid, bookmaker="dk",
                    spread_home=-3.0, spread_away=3.0)
    uid = _user(client)
    placed = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 100}).json()
    assert placed["pick_value"] == "HOME -3"
    _finish(client, gid, 27, 24)
    assert client.post("/users/grade", headers=OWNER_HEADERS).status_code == 200
    s = get_session(client.app.state.engine)
    assert s.query(PaperPick).one().result == "push"
    s.close()

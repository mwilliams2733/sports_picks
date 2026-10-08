"""Price-move protection on bet placement (sportsbook spec 2026-10-07 §6).

The slip sends the price and line the player saw. A different fresh quote is
refused with 409 ``price_moved`` and nothing is written; a request without
them is priced exactly as before (the Claude-picks script sends neither).
"""
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import ActivityFeed, Base, Game, PaperPick, Parlay, Team
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    """n scheduled NFL games, each with fresh odds: ML -110/-110, spread
    HOME -3.5 / AWAY +3.5 at -110, total 220.5 at -110."""
    s = get_session(client.app.state.engine)
    ids = []
    for i in range(n):
        h = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nfl")
        a = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nfl")
        s.add_all([h, a])
        s.flush()
        g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=h.id,
                 away_team_id=a.id, status="scheduled")
        s.add(g)
        s.flush()
        ids.append(g.id)
    s.commit()
    s.close()
    for gid in ids:
        seed_fresh_odds(client.app.state.engine, gid)
    return ids


def _user(client):
    r = client.post("/users/", json={"name": "friend", "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _written(client):
    """(straight + leg rows, parlays, pick_placed feed events)."""
    s = get_session(client.app.state.engine)
    try:
        return (s.query(PaperPick).count(), s.query(Parlay).count(),
                s.query(ActivityFeed).filter(ActivityFeed.event_type == "pick_placed").count())
    finally:
        s.close()


def _available(client, uid):
    return client.get(f"/users/{uid}").json()["available_balance"]


def test_a_matching_expected_price_and_line_places_the_bet():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 50,
        "expected_odds": -110, "expected_line": -3.5})
    assert r.status_code == 200, r.text
    assert (r.json()["odds"], r.json()["line"]) == (-110, -3.5)


def test_a_moved_price_is_refused_with_the_new_price_and_nothing_written():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    before = _available(client, uid)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 50,
        "expected_odds": -105, "expected_line": None})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "price_moved", "message": "The price moved to HOME ML -110.",
                                  "odds": -110, "line": None, "pick_value": "HOME ML"}
    assert _written(client) == (0, 0, 0)
    assert _available(client, uid) == before


def test_a_moved_line_is_refused_even_at_the_same_price():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "spread", "side": "HOME", "stake": 50,
        "expected_odds": -110, "expected_line": -3.0})
    assert r.status_code == 409
    assert (r.json()["detail"]["line"], r.json()["detail"]["pick_value"]) == (-3.5, "HOME -3.5")
    assert _written(client) == (0, 0, 0)


def test_a_moneyline_expects_no_line():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    base = {"game_id": gid, "pick_type": "moneyline", "side": "AWAY", "stake": 10, "expected_odds": -110}
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": 1.5}).status_code == 409
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": None}).status_code == 200


def test_without_expected_fields_the_bet_is_priced_as_before():
    """The Claude-picks script's request shape (logs-archive/)."""
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    r = client.post(f"/users/{uid}/picks", json={
        "game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 100})
    assert r.status_code == 200, r.text
    assert r.json()["odds"] == -110


def test_a_prop_compares_odds_only_and_refuses_an_expected_line():
    client = _client()
    [gid] = _games(client)
    seed_fresh_prop(client.app.state.engine, gid)          # QB One Over 225.5 pass yds at -110
    uid = _user(client)
    base = {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
            "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5, "stake": 20}
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_odds": -120}).status_code == 409
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_line": 225.5}).status_code == 422
    assert client.post(f"/users/{uid}/picks", json={**base, "expected_odds": -110}).status_code == 200


def test_a_moved_parlay_leg_is_named_and_nothing_is_written():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    legs = [{"game_id": g1, "pick_type": "moneyline", "side": "HOME",
             "expected_odds": -110, "expected_line": None},
            {"game_id": g2, "pick_type": "over_under", "side": "Over",
             "expected_odds": -110, "expected_line": 219.5}]
    r = client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": 25})
    assert r.status_code == 409
    assert (r.json()["detail"]["leg"], r.json()["detail"]["line"]) == (1, 220.5)
    assert _written(client) == (0, 0, 0)
    legs[1]["expected_line"] = 220.5
    assert client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": 25}).status_code == 200

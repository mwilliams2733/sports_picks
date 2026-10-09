"""The league feed (sportsbook spec 2026-10-07 §10): Tail-ready placed-bet
events, and settlement events shared by both grading paths, once per bet."""
import json

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import ActivityFeed, Base, Game, PaperPick, Team
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds, seed_fresh_prop
from backend.time_utils import et_today


def _client():
    client = TestClient(create_app(":memory:"), headers=ALL_HEADERS)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    s = get_session(client.app.state.engine)
    ids = []
    for i in range(n):
        h = Team(name=f"H{i}", abbreviation=f"H{i}", sport="nfl")
        a = Team(name=f"A{i}", abbreviation=f"A{i}", sport="nfl")
        s.add_all([h, a])
        s.flush()
        g = Game(sport="nfl", season="2026", date=et_today(), home_team_id=h.id, away_team_id=a.id,
                 status="scheduled")
        s.add(g)
        s.flush()
        ids.append(g.id)
    s.commit()
    s.close()
    for gid in ids:
        seed_fresh_odds(client.app.state.engine, gid)
    return ids


def _user(client, name):
    r = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _events(client, event_type=None):
    s = get_session(client.app.state.engine)
    try:
        q = s.query(ActivityFeed).order_by(ActivityFeed.id)
        if event_type:
            q = q.filter(ActivityFeed.event_type == event_type)
        return [json.loads(e.payload) for e in q]
    finally:
        s.close()


DISPLAY = {"label", "game_label", "start_time", "home_team", "away_team", "odds", "quoted_line"}


def test_a_placed_straight_bet_event_is_tail_ready():
    client = _client()
    [gid] = _games(client)
    uid, friend = _user(client, "sam"), _user(client, "jo")
    pid = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "spread",
                                                   "side": "AWAY", "stake": 50}).json()["id"]
    [ev] = _events(client, "pick_placed")
    assert (ev["bet_id"], ev["kind"], len(ev["legs"])) == (pid, "straight", 1)
    leg = ev["legs"][0]
    assert {k: leg[k] for k in DISPLAY} == {"label": "A0 +3.5", "game_label": "A0 @ H0", "start_time": None,
                                            "home_team": "H0", "away_team": "A0", "odds": -110, "quoted_line": 3.5}
    request = {k: v for k, v in leg.items() if k not in DISPLAY}
    assert request == {"game_id": gid, "pick_type": "spread", "side": "AWAY"}
    # A friend's Tail sends exactly that request.
    r = client.post(f"/users/{friend}/picks", json={**request, "stake": 10})
    assert r.status_code == 200, r.text


def test_a_placed_parlay_event_lists_every_leg():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    pl = client.post(f"/users/{uid}/parlay", json={"legs": [
        {"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": g2, "pick_type": "over_under", "side": "Over"}], "stake": 20}).json()
    [ev] = _events(client, "pick_placed")
    assert (ev["bet_id"], ev["kind"], ev["leg_count"]) == (pl["id"], "parlay", 2)
    assert [leg["label"] for leg in ev["legs"]] == ["H0 ML", "Over 220.5"]


def test_a_placed_prop_event_carries_the_prop_request():
    client = _client()
    [gid] = _games(client)
    seed_fresh_prop(client.app.state.engine, gid)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
                                             "prop_market": "player_pass_yds", "outcome": "Over",
                                             "line": 225.5, "stake": 10})
    [ev] = _events(client, "pick_placed")
    request = {k: v for k, v in ev["legs"][0].items() if k not in DISPLAY}
    assert request == {"game_id": gid, "pick_type": "prop", "prop_player": "QB One",
                       "prop_market": "player_pass_yds", "outcome": "Over", "line": 225.5}


from backend.paper import feed as feed_mod                                   # noqa: E402
from backend.pipeline.paper_settlement import grade_paper_picks             # noqa: E402,F401
from backend.models import Parlay                                            # noqa: E402


def _finish(client, gid, home, away):
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score = "final", home, away
    s.commit()
    s.close()


def _settle(client):
    s = get_session(client.app.state.engine)
    try:
        return feed_mod.settle_and_announce(s)
    finally:
        s.close()


def test_a_settled_straight_bet_is_announced_once_with_its_result():
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    pid = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline",
                                                   "side": "HOME", "stake": 110}).json()["id"]
    _finish(client, gid, 24, 17)
    assert _settle(client)["events"] == 1
    [ev] = _events(client, "pick_won")
    assert (ev["bet_key"], ev["bet_id"], ev["kind"], ev["result"], ev["payout"]) == \
        (f"straight-{pid}", pid, "straight", "win", 100.0)
    assert ev["message"] == "sam won H0 ML — +$100.00"
    assert _settle(client)["events"] == 0                    # nothing new, nothing re-announced


def test_a_parlay_is_announced_once_and_its_legs_never():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    pl = client.post(f"/users/{uid}/parlay", json={"legs": [
        {"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}], "stake": 20}).json()
    _finish(client, g1, 24, 17)
    _finish(client, g2, 10, 13)                              # second leg loses
    _settle(client)
    assert _events(client, "pick_won") == []
    [ev] = _events(client, "pick_lost")
    assert (ev["bet_key"], ev["kind"], ev["message"]) == (f"parlay-{pl['id']}", "parlay",
                                                          "sam lost a 2-leg parlay — −$20.00")


def test_a_push_is_announced():
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, gid, 20, 20)
    _settle(client)
    [ev] = _events(client, "pick_pushed")
    assert ev["message"] == "sam pushed H0 ML — $0.00"


def test_the_same_bet_is_never_announced_twice_review_focus_1():
    """The owner's Grade button and the scheduler can both see a bet graded;
    whichever announces second must find the first announcement."""
    client = _client()
    [gid] = _games(client)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, gid, 24, 17)
    s = get_session(client.app.state.engine)
    graded = grade_paper_picks(s)
    assert feed_mod.announce_settlements(s, None, graded, []) == 1
    assert feed_mod.announce_settlements(s, None, graded, []) == 0
    s.close()
    assert len(_events(client, "pick_won")) == 1


def test_the_owner_button_and_the_scheduler_announce_the_same_way():
    """Before this, the scheduler's automatic grading wrote no feed events and
    updated no streaks; only POST /users/grade did."""
    from backend.pipeline.scheduler import grade_pending_picks
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    for gid in (g1, g2):
        client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": "moneyline", "side": "HOME", "stake": 10})
    _finish(client, g1, 24, 17)
    assert client.post("/users/grade").json()["graded"] == 1        # the owner's path
    _finish(client, g2, 30, 3)
    s = get_session(client.app.state.engine)
    grade_pending_picks(s)                                            # the scheduler's path
    s.close()
    assert len(_events(client, "pick_won")) == 2
    s = get_session(client.app.state.engine)
    from backend.models import UserProfile
    u = s.get(UserProfile, uid)
    assert (u.current_streak, u.streak_type) == (2, "win")
    s.close()


def test_feed_times_are_utc_and_placed_messages_name_the_side():
    """Visual check (Phase 5 Task 7): created_at went out naive, so browsers
    read UTC as local time ("now" for a 5-hour-old bet); and placed-bet lines
    said "AWAY ML" where every other surface names the team."""
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client, "sam")
    client.post(f"/users/{uid}/picks", json={"game_id": g1, "pick_type": "spread", "side": "AWAY", "stake": 50})
    client.post(f"/users/{uid}/parlay", json={"legs": [
        {"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
        {"game_id": g2, "pick_type": "over_under", "side": "Over"}], "stake": 20})
    feed = client.get("/users/feed").json()
    assert all(e["created_at"].endswith("+00:00") for e in feed)
    messages = [e["payload"]["message"] for e in feed if e["event_type"] == "pick_placed"]
    assert "sam bet A0 +3.5 -110 — $50" in messages
    assert any(m.startswith("sam placed 2-leg parlay: H0 ML + Over 220.5") for m in messages)

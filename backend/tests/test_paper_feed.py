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

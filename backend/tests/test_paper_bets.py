"""My Bets tickets, GET /users/{id}/bets (sportsbook spec 2026-10-07 §7)."""
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.database import get_session
from backend.models import Base, Game, PaperPick, Team
from backend.pipeline.paper_settlement import parlay_win_payout, settle_parlays
from backend.tests.auth_helpers import ALL_HEADERS, TEST_PIN
from backend.tests.pricing_helpers import seed_fresh_odds
from backend.time_utils import et_today


def _client(headers=ALL_HEADERS):
    client = TestClient(create_app(":memory:"), headers=headers)
    Base.metadata.create_all(client.app.state.engine)
    return client


def _games(client, n=1):
    """n scheduled NFL games dated today with fresh odds (ML -110/-110,
    spread -3.5/+3.5, total 220.5, all -110)."""
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


def _user(client, name="friend"):
    r = client.post("/users/", json={"name": name, "pin": TEST_PIN})
    assert r.status_code == 200
    return r.json()["id"]


def _bet(client, uid, gid, pick_type="moneyline", side="HOME", stake=110):
    r = client.post(f"/users/{uid}/picks", json={"game_id": gid, "pick_type": pick_type,
                                                 "side": side, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _parlay(client, uid, legs, stake=50):
    r = client.post(f"/users/{uid}/parlay", json={"legs": legs, "stake": stake})
    assert r.status_code == 200, r.text
    return r.json()


def _grade(client, pick_id, result, payout):
    s = get_session(client.app.state.engine)
    p = s.get(PaperPick, pick_id)
    p.result, p.payout = result, payout
    s.commit()
    s.close()


def _leg_ids(client, parlay_id):
    s = get_session(client.app.state.engine)
    try:
        return [p.id for p in s.query(PaperPick).filter(PaperPick.parlay_id == parlay_id)
                .order_by(PaperPick.id)]
    finally:
        s.close()


def _bets(client, uid):
    r = client.get(f"/users/{uid}/bets")
    assert r.status_code == 200, r.text
    return r.json()


ML = lambda gid, side="HOME": {"game_id": gid, "pick_type": "moneyline", "side": side}
OVER = lambda gid: {"game_id": gid, "pick_type": "over_under", "side": "Over"}


def test_a_straight_bet_is_one_ticket_with_its_game():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    pid = _bet(client, uid, gid)
    [t] = _bets(client, uid)["tickets"]
    assert {k: t[k] for k in ("kind", "id", "stake", "odds", "to_win", "result", "payout", "sgp")} == \
        {"kind": "straight", "id": pid, "stake": 110, "odds": -110, "to_win": 100.0,
         "result": None, "payout": None, "sgp": False}
    assert t["created_at"].endswith("+00:00")
    [leg] = t["legs"]
    assert (leg["pick_value"], leg["result"]) == ("HOME ML", None)
    assert leg["game"] == {"id": gid, "sport": "nfl", "home_team": "H0", "away_team": "A0",
                           "start_time": None, "status": "scheduled", "home_score": None,
                           "away_score": None, "live_detail": None}


def test_a_parlay_is_one_ticket_not_its_legs():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    [t] = _bets(client, uid)["tickets"]
    assert (t["kind"], t["id"], t["odds"], len(t["legs"]), t["sgp"]) == \
        ("parlay", placed["id"], placed["combined_odds"], 2, False)
    assert t["to_win"] == placed["potential_payout"]


def test_a_same_game_parlay_is_flagged_sgp():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    _parlay(client, uid, [ML(gid), OVER(gid)])
    assert _bets(client, uid)["tickets"][0]["sgp"] is True


def test_newest_first_and_only_this_players_bets():
    client = _client()
    g1, g2 = _games(client, 2)
    me, other = _user(client, "me"), _user(client, "other")
    first = _bet(client, me, g1)
    second = _bet(client, me, g2)
    _bet(client, other, g1)
    assert [t["id"] for t in _bets(client, me)["tickets"]] == [second, first]


def test_a_partly_graded_parlay_stays_open_with_its_leg_results():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    first_leg, _ = _leg_ids(client, placed["id"])
    _grade(client, first_leg, "win", 0.0)
    [t] = _bets(client, uid)["tickets"]
    assert t["result"] is None
    assert [leg["result"] for leg in t["legs"]] == ["win", None]


def test_to_win_is_exactly_what_settlement_pays():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    placed = _parlay(client, uid, [ML(g1), OVER(g2)])
    to_win = _bets(client, uid)["tickets"][0]["to_win"]
    for leg in _leg_ids(client, placed["id"]):
        _grade(client, leg, "win", 0.0)
    s = get_session(client.app.state.engine)
    settle_parlays(s)
    s.close()
    [t] = _bets(client, uid)["tickets"]
    assert (t["result"], round(t["payout"], 2)) == ("win", to_win)
    assert round(parlay_win_payout(50, [-110, -110]), 2) == to_win


def test_summary_matches_the_balance_and_todays_stats():
    client = _client()
    g1, g2 = _games(client, 2)
    uid = _user(client)
    won = _bet(client, uid, g1)                      # 110 at -110
    _grade(client, won, "win", 100.0)
    _bet(client, uid, g2, stake=50)                  # still open
    summary = _bets(client, uid)["summary"]
    [row] = [u for u in client.get("/users/").json() if u["id"] == uid]
    today = client.get(f"/users/{uid}/stats").json()["today"]
    assert summary == {"available": row["available_balance"], "balance": row["current_balance"],
                       "open_stakes": 50.0, "today_pl": today["profit"]}
    assert summary["today_pl"] == 100.0


def test_unknown_player_is_404_and_bets_are_an_open_read():
    client = _client(headers={})
    assert client.get("/users/999/bets").status_code == 404
    uid = client.post("/users/", json={"name": "x", "pin": TEST_PIN}).json()["id"]
    assert _bets(client, uid) == {"summary": {"available": 10000.0, "balance": 10000.0,
                                              "open_stakes": 0.0, "today_pl": 0.0}, "tickets": []}


def test_a_live_leg_carries_the_game_clock():
    client = _client()
    [gid] = _games(client)
    uid = _user(client)
    _bet(client, uid, gid)
    s = get_session(client.app.state.engine)
    g = s.get(Game, gid)
    g.status, g.home_score, g.away_score, g.live_detail = "in_progress", 7, 3, "Q2 1:05"
    s.commit()
    s.close()
    [leg] = _bets(client, uid)["tickets"][0]["legs"]
    assert {k: leg["game"][k] for k in ("status", "home_score", "away_score", "live_detail")} == \
        {"status": "in_progress", "home_score": 7, "away_score": 3, "live_detail": "Q2 1:05"}

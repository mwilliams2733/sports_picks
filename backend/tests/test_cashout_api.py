"""POST /users/{id}/cashout and what a cash out does to money, settlement,
the feed and My Bets (sportsbook spec 2026-10-07 §9)."""
import json

from backend.database import get_session
from backend.models import ActivityFeed, Game, PaperPick, Parlay
from backend.paper import feed
from backend.tests import cashout_helpers as h

AWAY_SPREAD = {"pick_type": "spread", "side": "AWAY"}


def _cash(c, uid, bet_id, expected, kind="straight", **kw):
    return c.post(f"/users/{uid}/cashout", json={"bet_id": bet_id, "kind": kind,
                                                  "expected_offer": expected}, **kw)


def _summary(c, uid):
    return c.get(f"/users/{uid}/bets").json()["summary"]


def _events(c, event_type):
    s = get_session(c.app.state.engine)
    try:
        return [json.loads(e.payload) for e in
                s.query(ActivityFeed).filter(ActivityFeed.event_type == event_type).order_by(ActivityFeed.id)]
    finally:
        s.close()


def _settle(c):
    s = get_session(c.app.state.engine)
    try:
        return feed.settle_and_announce(s)
    finally:
        s.close()


def test_a_cash_out_pays_the_offer_and_frees_the_stake():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    start = _summary(c, uid)["available"]
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    r = _cash(c, uid, pid, 90.68)
    assert r.status_code == 200, r.text
    assert r.json() == {"bet_id": pid, "kind": "straight", "offer": 90.68, "payout": -9.32,
                        "available": round(start - 9.32, 2)}
    s = _summary(c, uid)
    assert (s["available"], s["balance"], s["open_stakes"]) == (round(start - 9.32, 2), round(start - 9.32, 2), 0)
    session = get_session(c.app.state.engine)
    pick = session.get(PaperPick, pid)
    assert (pick.result, pick.payout, pick.graded_at is not None) == ("cashed_out", -9.32, True)
    session.close()


def test_an_offer_below_the_one_shown_is_refused_with_the_new_offer():
    """Review Focus 3: never pay less than the player saw without asking."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    r = _cash(c, uid, pid, 95.00)
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "offer_changed", "offer": 90.68,
                                  "message": "The offer changed to $90.68."}
    assert c.get(f"/users/{uid}/bets").json()["tickets"][0]["result"] is None


def test_an_offer_above_the_one_shown_pays_the_current_offer():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 80.00).json()["offer"] == 90.68


def test_a_bet_cashes_out_once():
    """Review Focus 2: a double tap moves money once."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 90.68).status_code == 200
    again = _cash(c, uid, pid, 90.68)
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "settled")
    assert len(_events(c, "cashed_out")) == 1


def test_an_offer_gone_by_confirm_time_is_refused_with_its_reason():
    """Review Focus 1: kickoff between view and confirm pays nothing."""
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    h.set_game(c, g, status="in_progress")
    r = _cash(c, uid, pid, 90.68)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "game_started"
    assert c.get(f"/users/{uid}/bets").json()["tickets"][0]["result"] is None


def test_cash_out_needs_the_players_pin():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    assert _cash(c, uid, pid, 90.68, headers={"X-Player-Pin": "9999"}).status_code == 401


def test_only_your_own_whole_bet_can_be_cashed_out():
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid, other = h.user(c), h.user(c, "jo")
    pid = h.place(c, uid, g1, **AWAY_SPREAD)
    plid = h.place_parlay(c, uid, [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                   {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    assert _cash(c, other, pid, 1).status_code == 404                     # someone else's
    s = get_session(c.app.state.engine)
    leg_id = s.query(PaperPick.id).filter(PaperPick.parlay_id == plid).first()[0]
    s.close()
    assert _cash(c, uid, leg_id, 1).status_code == 404                    # a parlay's leg alone
    assert _cash(c, uid, plid + 99, 1, kind="parlay").status_code == 404  # no such parlay (ids are per table: straight #1 and parlay #1 both exist)


def test_a_cashed_out_parlay_stays_cashed_out_when_its_legs_grade():
    """Review Focus 4: the legs grade at stake 0; nothing else moves."""
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid = h.user(c)
    plid = h.place_parlay(c, uid, [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                   {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    assert _cash(c, uid, plid, 17.31, kind="parlay").json()["payout"] == -2.69
    balance = _summary(c, uid)["balance"]
    for g in (g1, g2):
        h.set_game(c, g, status="final", home_score=24, away_score=17)
    _settle(c)
    s = get_session(c.app.state.engine)
    parlay = s.get(Parlay, plid)
    assert (parlay.result, parlay.payout) == ("cashed_out", -2.69)
    assert {p.result for p in s.query(PaperPick).filter(PaperPick.parlay_id == plid)} == {"win"}
    s.close()
    assert _summary(c, uid)["balance"] == balance
    assert _events(c, "pick_won") == [] and _events(c, "pick_lost") == []


def test_settlement_skips_a_cashed_out_straight_bet():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    _cash(c, uid, pid, 90.68)
    h.set_game(c, g, status="final", home_score=10, away_score=30)       # AWAY would have won
    _settle(c)
    s = get_session(c.app.state.engine)
    assert (s.get(PaperPick, pid).result, s.get(PaperPick, pid).payout) == ("cashed_out", -9.32)
    s.close()
    assert _events(c, "pick_won") == []


def test_a_cash_out_is_announced_once_by_team_name():
    c = h.client()
    [g] = h.games(c)
    uid = h.user(c)
    pid = h.place(c, uid, g, **AWAY_SPREAD)
    _cash(c, uid, pid, 90.68)
    [ev] = _events(c, "cashed_out")
    assert ev["message"] == "sam cashed out A0 +3.5 for $90.68"
    assert (ev["bet_key"], ev["bet_id"], ev["kind"], ev["payout"]) == (f"straight-{pid}", pid, "straight", -9.32)


def test_my_bets_carries_the_offer_on_open_tickets_only():
    c = h.client()
    g1, g2 = h.games(c, 2)
    uid = h.user(c)
    p1 = h.place(c, uid, g1, **AWAY_SPREAD)
    p2 = h.place(c, uid, g2, **AWAY_SPREAD)
    h.set_row(c, PaperPick, p2, pick_value="AWAY")                         # legacy row: no offer, no crash
    _cash(c, uid, p1, 90.68)
    r = c.get(f"/users/{uid}/bets")
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()["tickets"]}
    assert by_id[p1]["cash_out"] is None and by_id[p1]["result"] == "cashed_out"
    assert by_id[p2]["cash_out"]["available"] is False
    assert by_id[p2]["cash_out"]["reason"] == "not_quoted"

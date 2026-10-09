"""The cash-out offer (sportsbook spec 2026-10-07 §9):
stake x D_placed x p_now x (1 - 0.05), rounded down to the cent."""
import pytest

from backend.database import get_session
from backend.models import Odds, PaperPick, PlayerProp
from backend.paper import cashout
from backend.tests import cashout_helpers as h

AWAY_SPREAD = {"pick_type": "spread", "side": "AWAY"}
HOME_SPREAD = {"pick_type": "spread", "side": "HOME"}
OVER = {"pick_type": "over_under", "side": "Over"}
QB_OVER = {"pick_type": "prop", "prop_player": "QB One", "prop_market": "player_pass_yds",
           "outcome": "Over", "line": 225.5}


def test_the_margin_is_five_percent():
    assert cashout.CASH_OUT_MARGIN == 0.05


def test_an_even_market_offers_stake_times_price_times_half_less_the_margin():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    o = h.offer_of(c, "straight", pid)
    # 100 x 1.90909 x 0.5 x 0.95 = 90.6818... -> rounded DOWN to 90.68
    assert (o.amount, o.p_now) == (90.68, 0.5)


def test_the_offer_rounds_down_to_the_cent_not_to_the_nearest():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, stake=10, **AWAY_SPREAD)
    # 10 x 1.90909 x 0.5 x 0.95 = 9.0682 -> 9.06 (the nearest cent would be 9.07)
    assert h.offer_of(c, "straight", pid).amount == 9.06


def test_the_chance_now_is_the_no_vig_pair_not_the_raw_price():
    c = h.client()
    [g] = h.games(c, moneyline_home=-170, moneyline_away=150)
    pid = h.place(c, h.user(c), g, pick_type="moneyline", side="AWAY")
    h.reprice(c, g, moneyline_home=-200, moneyline_away=170)
    o = h.offer_of(c, "straight", pid)
    # p_now = (100/270) / (100/270 + 200/300) = 0.35714; 100 x 2.5 x 0.35714 x 0.95 = 84.82
    assert o.p_now == pytest.approx(0.357142857)
    assert o.amount == 84.82


def test_a_parlay_offer_multiplies_its_legs():
    c = h.client()
    g1, g2 = h.games(c, 2)
    plid = h.place_parlay(c, h.user(c), [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                         {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    o = h.offer_of(c, "parlay", plid)
    # 20 x (1.90909^2 = 3.64463) x 0.25 x 0.95 = 17.3119... -> 17.31
    assert (o.amount, o.p_now) == (17.31, 0.25)


def test_a_prop_is_offered_against_its_other_side_at_the_same_line():
    c = h.client()
    [g] = h.games(c)
    h.props(c, g)
    pid = h.place(c, h.user(c), g, **QB_OVER)
    assert h.offer_of(c, "straight", pid).amount == 90.68


@pytest.mark.parametrize("change, reason", [
    (lambda c, g: h.set_game(c, g, status="in_progress"), "game_started"),
    (lambda c, g: h.reprice(c, g, spread_home=-4.5, spread_away=4.5), "line_moved"),
    (lambda c, g: h.stale(c, g), "stale"),
    (lambda c, g: _delete_odds(c, g), "not_quoted"),
])
def test_each_refusal_has_its_reason(change, reason):
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    change(c, g)
    assert h.reason_of(c, "straight", pid) == reason


def _delete_odds(c, g):
    s = get_session(c.app.state.engine)
    s.query(Odds).filter(Odds.game_id == g).delete()
    s.commit()
    s.close()


def test_a_moved_total_is_line_moved():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **OVER)
    h.reprice(c, g, over_under=221.5)
    assert h.reason_of(c, "straight", pid) == "line_moved"


def test_the_other_side_must_quote_the_mirror_line():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **HOME_SPREAD)
    h.reprice(c, g, spread_away=4.0)            # HOME still -3.5, AWAY now +4
    assert h.reason_of(c, "straight", pid) == "line_moved"


def test_a_settled_bet_has_no_offer():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    h.set_row(c, PaperPick, pid, result="win", payout=90.91)
    assert h.reason_of(c, "straight", pid) == "settled"


def test_a_parlay_with_one_started_leg_has_no_offer():
    c = h.client()
    g1, g2 = h.games(c, 2)
    plid = h.place_parlay(c, h.user(c), [{"game_id": g1, "pick_type": "moneyline", "side": "HOME"},
                                         {"game_id": g2, "pick_type": "moneyline", "side": "HOME"}])
    h.set_game(c, g2, status="in_progress")
    assert h.reason_of(c, "parlay", plid) == "game_started"


def test_a_prop_whose_line_moved_is_line_moved_and_one_missing_its_other_side_is_not_quoted():
    c = h.client()
    [g] = h.games(c)
    h.props(c, g)
    pid = h.place(c, h.user(c), g, **QB_OVER)
    s = get_session(c.app.state.engine)
    for p in s.query(PlayerProp):
        p.line = 230.5
    s.commit()
    s.close()
    assert h.reason_of(c, "straight", pid) == "line_moved"
    s = get_session(c.app.state.engine)
    for p in s.query(PlayerProp):
        p.line = 225.5
    s.query(PlayerProp).filter(PlayerProp.outcome == "Under").delete()
    s.commit()
    s.close()
    assert h.reason_of(c, "straight", pid) == "not_quoted"


def test_an_unparseable_stored_bet_has_no_offer():
    """Review Focus 5: a legacy row (players typed their own values before
    plan 027) must read as no offer, never raise."""
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    h.set_row(c, PaperPick, pid, pick_value="AWAY")
    assert h.reason_of(c, "straight", pid) == "not_quoted"


def test_offer_view_shapes():
    c = h.client()
    [g] = h.games(c)
    pid = h.place(c, h.user(c), g, **AWAY_SPREAD)
    s = get_session(c.app.state.engine)
    try:
        pick = s.get(PaperPick, pid)
        assert cashout.offer_view(s, "straight", pick) == {"available": True, "offer": 90.68}
        g_row = pick.game
        g_row.status = "in_progress"
        s.commit()
        view = cashout.offer_view(s, "straight", pick)
        assert view == {"available": False, "reason": "game_started", "message": cashout.MESSAGES["game_started"]}
        pick.result = "loss"
        s.commit()
        assert cashout.offer_view(s, "straight", pick) is None
    finally:
        s.close()

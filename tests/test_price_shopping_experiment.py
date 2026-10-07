"""Guards for the price-shopping experiment: which quotes were really on
offer, the leave-one-out benchmark, and settlement."""
from datetime import datetime, timedelta

import pytest

from backend.scripts import price_shopping_experiment as ps

T0 = datetime(2026, 10, 4, 15, 0)


def _row(book, captured, seen, home=-110, away=-110):
    return {"bookmaker": book, "captured_at": captured, "last_seen_at": seen,
            "moneyline_home": home, "moneyline_away": away, "spread_home": None,
            "spread_away": None, "over_under": None, "spread_home_price": None,
            "spread_away_price": None, "over_price": None, "under_price": None}


def test_a_quote_counts_only_while_it_was_showing():
    rows = [_row("a", T0, T0 + timedelta(minutes=30)),              # held for 30 min
            _row("b", T0 + timedelta(hours=1), T0 + timedelta(hours=1))]
    assert set(ps.showing(rows, T0 + timedelta(minutes=20))) == {"a"}
    assert set(ps.showing(rows, T0 + timedelta(hours=1))) == {"b"}   # a gone stale


def test_showing_takes_the_book_latest_quote():
    rows = [_row("a", T0, T0, home=-120), _row("a", T0 + timedelta(seconds=30),
                                               T0 + timedelta(seconds=30), home=-140)]
    assert ps.showing(rows, T0 + timedelta(seconds=30))["a"]["moneyline_home"] == -140


def test_ev_and_no_vig():
    assert ps.ev(0.5, 100) == pytest.approx(0.0)
    assert ps.ev(0.5, -110) == pytest.approx(0.5 * 100 / 110 - 0.5)
    assert ps.no_vig(-110, -110) == pytest.approx(0.5)


@pytest.mark.parametrize("market,side,point,home,away,result", [
    ("moneyline", "home", None, 21, 17, True), ("moneyline", "away", None, 21, 17, False),
    ("spread", "home", -3.5, 21, 17, True), ("spread", "away", -3.5, 21, 17, False),
    ("spread", "home", -4.0, 21, 17, None), ("total", "over", 37.5, 21, 17, True),
    ("total", "under", 38.0, 21, 17, None)])
def test_outcome(market, side, point, home, away, result):
    assert ps.outcome(market, side, point, home, away) is result


def test_an_outlier_is_judged_against_the_others_and_valued_at_the_close():
    """Five books at -110/-110, one at +120 home: only the outlier qualifies,
    and its benchmark excludes itself."""
    kickoff = T0 + timedelta(hours=2)
    rows = [_row(b, T0, kickoff - timedelta(minutes=5)) for b in "abcde"]
    rows.append(_row("x", T0, kickoff - timedelta(minutes=5), home=120, away=-140))
    game = {"id": 1, "sport": "nfl", "start_time": kickoff, "home_score": 24, "away_score": 20}
    flagged, every = ps.bets_for_game(game, rows)
    flagged = [b for b in flagged if b.ev_entry >= ps.PRIMARY_EV]
    assert [(b.book, b.side) for b in flagged] == [("x", "home")]
    assert flagged[0].ev_entry == pytest.approx(ps.ev(0.5, 120))       # fair from a..e only
    assert flagged[0].won is True
    assert len({(b.book, b.side) for b in every}) == len(every)        # one per book and side


def test_quotes_after_kickoff_are_ignored():
    kickoff = T0 + timedelta(hours=2)
    rows = [_row(b, kickoff + timedelta(minutes=1), kickoff + timedelta(minutes=1))
            for b in "abcdef"]
    game = {"id": 1, "sport": "nfl", "start_time": kickoff, "home_score": 24, "away_score": 20}
    assert ps.bets_for_game(game, rows) == ([], [])


def test_the_baseline_records_each_book_first_quote_not_its_last():
    kickoff = T0 + timedelta(hours=2)
    rows = [_row(b, T0, kickoff - timedelta(minutes=5)) for b in "abcde"]
    rows += [_row("x", T0, T0 + timedelta(minutes=10), home=-105, away=-115),
             _row("x", T0 + timedelta(minutes=30), kickoff - timedelta(minutes=5),
                  home=-130, away=110)]
    game = {"id": 1, "sport": "nfl", "start_time": kickoff, "home_score": 24, "away_score": 20}
    _, every = ps.bets_for_game(game, rows)
    (x_home,) = [b for b in every if b.book == "x" and b.side == "home"]
    assert x_home.price == -105

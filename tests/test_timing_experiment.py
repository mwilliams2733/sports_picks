"""Guards for the timing experiment: the open must postdate both teams'
previous games, and the close is each book's last pre-kickoff quote."""
from datetime import datetime, timedelta

import pytest

from backend.scripts import timing_experiment as te

KICK = datetime(2026, 10, 11, 17, 0)


def _row(book, captured, seen=None, home=-110, away=-110):
    return {"bookmaker": book, "captured_at": captured, "last_seen_at": seen or captured,
            "moneyline_home": home, "moneyline_away": away}


def _rows(t, home, away, books="abcde"):
    return [_row(b, t, t, home, away) for b in books]


def test_the_open_is_the_first_fetch_after_both_previous_games():
    prior = KICK - timedelta(days=7)
    rows = (_rows(prior - timedelta(days=1), 150, -170)      # before last week's games: ignored
            + _rows(prior + timedelta(hours=10), 120, -140)   # first clean fetch
            + _rows(KICK - timedelta(hours=1), 100, -120))    # the close
    game = {"start_time": KICK}
    ready = te.prior_end([(1, prior), (2, prior - timedelta(days=1))], (1, 2), KICK)
    assert ready == prior + timedelta(hours=te.PRIOR_GAME_HOURS)
    open_q, opened_at, close_q = te.open_and_close(game, rows, ready)
    assert opened_at == prior + timedelta(hours=10)
    assert open_q["a"]["moneyline_home"] == 120
    assert close_q["a"]["moneyline_home"] == 100


def test_quotes_after_kickoff_are_never_the_close():
    rows = _rows(KICK - timedelta(hours=5), 120, -140) + _rows(KICK + timedelta(minutes=5), 300, -400)
    _, _, close_q = te.open_and_close({"start_time": KICK}, rows, KICK - timedelta(days=1))
    assert close_q["a"]["moneyline_home"] == 120


def test_a_consensus_needs_enough_books():
    four = {b: _row(b, KICK, home=-110, away=-110) for b in "abcd"}
    three = {b: _row(b, KICK) for b in "abc"}
    assert te.home_prob(four) == pytest.approx(0.5)
    assert te.home_prob(three) is None


def test_prior_end_ignores_later_and_other_teams_games():
    early, mine, other = KICK - timedelta(days=7), KICK - timedelta(days=6), KICK - timedelta(days=1)
    sched = [(1, early), (1, mine), (3, other), (1, KICK + timedelta(days=7))]
    assert te.prior_end(sched, (1, 2), KICK) == mine + timedelta(hours=te.PRIOR_GAME_HOURS)


def test_best_price_is_the_highest_payout():
    quotes = {"a": _row("a", KICK, home=120), "b": _row("b", KICK, home=135),
              "c": _row("c", KICK, home=-105)}
    assert te.best_price(quotes, "home") == 135

"""The one definition of every pick-quality metric.

Expected values were computed independently on 2026-09-28; the z = 1.96 case
is the textbook Wilson interval for 8 of 10, (0.490, 0.943).
"""
from datetime import date
import logging

import pytest

from backend.analysis.scorecard import Bet, group, summarize, trend, wilson

D = date(2026, 9, 28)


def _b(result, odds=-110, stake=1.0, profit=None, day=D, stars=None):
    if profit is None:
        profit = {"win": stake * (100 / abs(odds) if odds < 0 else odds / 100),
                  "loss": -stake, "push": 0.0, None: 0.0}[result]
    return Bet(result=result, stake=stake, profit=profit, odds=odds, day=day,
               stars=stars)


def test_wilson_matches_the_published_95_percent_interval():
    low, high = wilson(8, 10, z=1.96)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_defaults_to_90_percent():
    low, high = wilson(8, 10)
    assert low == pytest.approx(0.5408, abs=1e-4)
    assert high == pytest.approx(0.9314, abs=1e-4)


def test_wilson_edges():
    assert wilson(0, 0) is None
    low, high = wilson(5, 5)
    assert low == pytest.approx(0.6488, abs=1e-4) and high == 1.0
    low, high = wilson(0, 5)
    assert low == 0.0 and high == pytest.approx(0.3512, abs=1e-4)


def test_win_rate_excludes_pushes_and_pending():
    s = summarize([_b("win"), _b("loss"), _b("push"), _b(None)])
    assert (s.wins, s.losses, s.pushes, s.pending, s.n) == (1, 1, 1, 1, 3)
    assert s.win_rate == pytest.approx(0.5)


def test_no_decided_bet_means_no_rate_not_zero():
    s = summarize([_b(None), _b("push")])
    assert s.win_rate is None and s.range_low is None and s.break_even is None


def test_break_even_is_the_mean_implied_probability_of_decided_prices():
    s = summarize([_b("win", odds=-110), _b("loss", odds=150)])
    assert s.break_even == pytest.approx(0.461905, abs=1e-6)


def test_roi_counts_a_push_as_staked_and_returned():
    s = summarize([_b("win"), _b("loss"), _b("push")])
    assert s.roi == pytest.approx(-0.030303, abs=1e-6)


def test_verdict_only_when_the_whole_range_clears_break_even():
    thin = summarize([_b("win")] * 3 + [_b("loss")] * 2)       # range spans break-even
    assert thin.verdict is None
    strong = summarize([_b("win")] * 40 + [_b("loss")] * 5)
    assert strong.verdict == "above"
    weak = summarize([_b("win")] * 5 + [_b("loss")] * 40)
    assert weak.verdict == "below"
    thin_below = summarize([_b("win")] * 2 + [_b("loss")] * 3)  # range spans break-even
    assert thin_below.verdict is None


def test_sunday_and_monday_are_different_weeks():
    sun, mon = date(2026, 9, 27), date(2026, 9, 28)
    labels = [s.label for s in group([_b("win", day=sun), _b("win", day=mon)], "week")]
    assert labels == ["2026-09-28", "2026-09-21"]      # newest first


def test_months_newest_first():
    labels = [s.label for s in group(
        [_b("win", day=date(2026, 8, 31)), _b("win", day=date(2026, 9, 1))], "month")]
    assert labels == ["2026-09", "2026-08"]


def test_stars_ordered_high_to_low_with_unrated_last():
    labels = [s.label for s in group(
        [_b("win", stars=3), _b("win", stars=None), _b("win", stars=5)], "stars")]
    assert labels == ["5", "3", "unrated"]


def test_unknown_grouping_is_rejected():
    with pytest.raises(ValueError):
        group([_b("win")], "year")


def test_drawdown_takes_the_deeper_of_two_dips():
    days = [date(2026, 9, d) for d in range(1, 8)]
    profits = [1, 1, -1, 2, -1, -1, -1]
    t = trend([_b("win" if p > 0 else "loss", profit=p, day=d)
               for p, d in zip(profits, days)])
    assert t.max_drawdown == pytest.approx(3.0)


def test_drawdown_computed_on_day_end_values():
    """Drawdown measured on day-end cumulative, not per-bet."""
    t = trend([_b("win", profit=1, day=D), _b("loss", profit=-1, day=D)])
    assert t.max_drawdown == pytest.approx(0.0)


def test_a_push_does_not_break_a_losing_streak_and_a_win_does():
    seq = ["loss", "loss", "push", "loss", "win", "loss"]
    t = trend([_b(r, day=date(2026, 9, i + 1)) for i, r in enumerate(seq)])
    assert t.longest_losing_streak == 3


def test_trend_has_one_point_per_day():
    """Review Focus 5: several picks on one day are one day-end point."""
    t = trend([_b("win", day=D), _b("loss", day=D), _b("win", day=date(2026, 9, 29))])
    assert [d for d, _ in t.points] == [D, date(2026, 9, 29)]
    assert t.points[0][1] == pytest.approx(100 / 110 - 1)


def test_pending_bets_are_not_on_the_trend():
    assert trend([_b(None)]).points == []


def test_to_dict_rounds_and_keeps_nones():
    d = summarize([_b(None)]).to_dict()
    assert d["win_rate"] is None and d["n"] == 0 and d["pending"] == 1


def test_bad_odds_excluded_with_warning(caplog):
    """Decided bets with invalid odds are excluded from break_even, logged once."""
    with caplog.at_level(logging.WARNING):
        s = summarize([_b("win", odds=0), _b("loss", odds=-110)])
    # With one valid bet at -110, break_even is its implied probability
    assert s.break_even == pytest.approx(110 / 210)  # only the -110 bet counts
    assert "1 decided bets excluded from break-even for invalid odds" in caplog.text

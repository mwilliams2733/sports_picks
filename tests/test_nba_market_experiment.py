"""Guards for the NBA market experiment's line sign and game matching."""
from datetime import date

from backend.scripts.nba_market_experiment import join_rows, line_margin, match


def test_line_margin_is_positive_when_home_is_favoured():
    assert line_margin(5.5, "home") == 5.5
    assert line_margin(5.5, "away") == -5.5


def test_match_allows_a_day_either_side_and_nothing_more():
    lines = {(date(2026, 3, 1), "BOS", "NY"): (4.0, 7.0)}
    assert match(lines, date(2026, 3, 2), "BOS", "NY") == (4.0, 7.0)
    assert match(lines, date(2026, 3, 3), "BOS", "NY") is None       # two days after
    assert match(lines, date(2026, 2, 27), "BOS", "NY") is None      # two days before
    assert match(lines, date(2026, 3, 1), "NY", "BOS") is None      # home/away matter


def test_a_score_mismatch_is_not_joined():
    lines = {(date(2026, 3, 1), "BOS", "NY"): (4.0, 7.0)}
    rows = [(1, 0.6, "BOS", "NY", date(2026, 3, 1), 7),
            (2, 0.6, "BOS", "NY", date(2026, 3, 1), -3),      # same key, other score
            (3, 0.6, "MIA", "ORL", date(2026, 3, 1), 2)]      # no line at all
    joined, unmatched = join_rows(rows, lines)
    assert [j[0] for j in joined] == [1]
    assert unmatched == 2
    assert joined[0][3:] == (4.0, 7.0)
    assert joined[0][2] > 0                                    # p 0.6 -> home margin

"""consensus_line: the point-value counterpart to consensus_moneyline.

Also covers a latent average_odds bug the fix-round-1 review of plan 027's
paper pricing module surfaced: a row can carry spread_home without
spread_away (or vice versa -- a book quoting one side's price but not the
other's line, or a partially-populated historical row), and averaging each
side over its OWN count rather than a shared one previously caused a
ZeroDivisionError when one side's list was empty.
"""
from backend.analysis.strategy import average_odds, consensus_line
from backend.data_types import OddsSnapshot


def test_consensus_line_is_the_arithmetic_mean_rounded_to_one_decimal():
    # (-3.0 + -4.0) / 2 = -3.5
    assert consensus_line([-3.0, -4.0]) == -3.5


def test_consensus_line_of_empty_list_is_none():
    assert consensus_line([]) is None


def test_average_odds_does_not_divide_by_zero_when_one_side_is_never_quoted():
    snap = OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=100,
                        spread_home=-3.5, spread_away=None, over_under=44.5)
    result = average_odds([snap])
    assert result["spread_home"] == -3.5
    assert result["spread_away"] is None

"""Kelly must be allowed to say "do not bet this".

`fractional_kelly` clamped its result to [0.5, 3.0] and returned 0.5 when the
Kelly fraction was zero or negative. A negative Kelly fraction means the wager
is -EV at that price: the model's probability is at or below the price's
implied probability. Returning 0.5 units turned "do not take this bet" into
"take this bet at the minimum stake".

That is not a rounding policy, it is a sign error. Rounding a small positive
stake UP to a practical minimum is a defensible choice about bet granularity.
Flipping a negative recommendation into a positive stake asserts the opposite
of what the criterion computed.

How it actually bites
---------------------
A pick is only generated when its edge is positive, so a negative Kelly
should be unreachable -- except that `ensemble` passes a HARDCODED -110 to
`fractional_kelly` for every spread and total, regardless of the real price.
-110 implies 52.38%, so any spread pick whose cover probability sits below
that gets a negative Kelly and was silently staked at 0.5 units.

This matters more given the measured closing-line value: price CLV is
-0.611pp across 96 moneyline picks, so the model already prices worse than
the market on average. A sizer that never declines compounds that.

The backtester's falsy coalesce
-------------------------------
`unit_size = getattr(pick, 'suggested_unit_size', 1.0) or 1.0` treats 0.0 as
missing and substitutes a FULL unit -- so a no-bet would have been backtested
as the largest stake the old floor could produce, exactly inverting the fix.
A zero stake is a real value, not a gap.
"""
import pytest

from backend.analysis.kelly import MAX_UNIT, MIN_UNIT, NO_BET, fractional_kelly


def test_a_negative_kelly_is_no_bet_not_a_minimum_bet():
    """0.40 against -150 (implied 60%) is a -EV wager by 20 points."""
    assert fractional_kelly(0.40, -150, 0.25) == NO_BET


def test_a_probability_below_the_implied_price_is_no_bet():
    """A hair below break-even, which is the testable case.

    EXACT break-even is not observable: at p = 11/21 against -110 the
    arithmetic lands on +6.1e-17 rather than 0.0, so `<= 0` and `< 0` are
    indistinguishable there. That makes flipping the comparison a
    semantically equivalent mutation, not a test gap -- the boundary the
    guard actually defends is the negative side."""
    assert fractional_kelly(0.5238095238, -110, 0.25) == NO_BET


def test_a_spread_priced_at_minus_110_below_breakeven_declines():
    """The path that actually produced these: `ensemble` hands every spread
    and total a hardcoded -110, so a 51% cover probability is -EV at the
    price Kelly is given."""
    assert fractional_kelly(0.51, -110, 0.25) == NO_BET


def test_a_real_edge_still_sizes_and_respects_the_ceiling():
    """0.60 at -150 would NOT qualify: -150 implies exactly 60%, so that is
    break-even, not an edge. 0.70 clears it."""
    result = fractional_kelly(0.70, -150, 0.25)

    assert MIN_UNIT <= result <= MAX_UNIT


def test_an_enormous_edge_is_capped_not_unbounded():
    """Quarter Kelly on a near-certainty is ~25% of bankroll, which is 25
    units. The cap is what stops the sizer recommending a quarter of the
    bankroll on one game.

    This test previously asserted the OPPOSITE -- that 0.25 could never
    reach the cap -- because the bankroll fraction was clamped directly to
    a unit range and the two scales never met. See
    `test_kelly_unit_scale.py`."""
    assert fractional_kelly(0.99, 200, 0.25) == MAX_UNIT


def test_a_small_positive_edge_still_rounds_up_to_the_minimum():
    """The floor keeps its job. A genuine positive edge too small to stake
    meaningfully is a granularity question, and rounding up does not
    misstate the direction of the recommendation."""
    result = fractional_kelly(0.5239, -110, 0.0001)

    assert result == MIN_UNIT
    assert result > NO_BET, "a positive edge is still a bet"


def test_the_floor_never_changes_the_sign_of_the_recommendation():
    """The invariant, stated directly: every probability at or below the
    implied price declines, and every probability above it stakes."""
    implied = 0.52380952  # -110

    for prob in (0.30, 0.45, 0.50, 0.52):
        assert fractional_kelly(prob, -110, 0.25) == NO_BET, prob
    for prob in (0.55, 0.60, 0.80):
        assert fractional_kelly(prob, -110, 0.25) >= MIN_UNIT, prob
    assert implied < 0.55


# --- the backtester must not resurrect a declined bet ---------------------

class _Pick:
    def __init__(self, unit_size):
        self.game_id = 1
        self.pick_type = "moneyline"
        self.pick_value = "HOME ML"
        self.confidence = 3
        self.edge_pct = 4.0
        self.odds_at_pick = -110
        self.suggested_unit_size = unit_size


class _Strategy:
    def __init__(self, picks):
        self._picks = picks

    def predict(self, game):
        return self._picks


def _run(unit_sizes):
    """The strategy stub ignores the game, so a placeholder stands in for
    GameData rather than coupling this test to its constructor."""
    from backend.backtesting.backtester import Backtester

    picks = [_Pick(u) for u in unit_sizes]
    return Backtester(_Strategy(picks)).run([(object(), 110, 100)])


def test_a_zero_stake_pick_is_not_wagered():
    """A bet not placed cannot win or lose. Counting it would move the win
    rate, the headline number, while contributing nothing to profit."""
    summary = _run([NO_BET])

    assert summary["total"] == 0
    assert summary["total_units_risked"] == 0.0
    assert summary["total_profit"] == 0.0


def test_a_zero_stake_pick_is_reported_not_silently_dropped():
    """A silent skip is indistinguishable from a strategy that made no
    picks at all."""
    summary = _run([NO_BET, NO_BET, 1.0])

    assert summary["no_bet"] == 2
    assert summary["total"] == 1


def test_a_zero_stake_is_not_treated_as_a_missing_value(monkeypatch):
    """The falsy-coalesce bug, stated as a test: `or 1.0` turned a declined
    bet into the largest stake the old floor could produce."""
    summary = _run([NO_BET])

    assert summary["total_units_risked"] != 1.0, (
        "0.0 is a real stake of nothing, not an absent value to default")


def test_a_normal_pick_is_unaffected():
    summary = _run([1.5])

    assert summary["total"] == 1
    assert summary["total_units_risked"] == 1.5
    assert summary["no_bet"] == 0

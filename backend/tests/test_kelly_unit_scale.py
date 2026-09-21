"""A unit is 1% of bankroll, so Kelly's bankroll fraction must be scaled.

`fractional_kelly` computed `f* * fraction` -- a fraction OF BANKROLL,
between 0 and 0.25 at the default quarter Kelly -- and then clamped it to
`[0.5, 3.0]` UNITS. The two are different scales, and the comparison made
the function constant:

    f* = p - q/b  cannot exceed 1.0 for a single wager
    f* * 0.25     cannot exceed 0.25
    max(0.5, ...) therefore returns 0.5 for every positive-Kelly input

Measured across the whole plausible input space at the default fraction,
the function returned exactly two values: 0.5 for every positive edge, and
(since the no-bet fix) 0.0 for every non-positive one. Every pick ever
generated carried the same suggested stake, and the advertised 3.0 ceiling
had never been produced.

The fix is the missing conversion. One unit is 1% of bankroll -- the
convention the FAQ already assumes when it calls 1.0 units a standard bet
-- so units = bankroll fraction x 100.

What this does NOT change
-------------------------
The no-bet rule stays: `f* <= 0` still declines, because the scale of a
negative recommendation is irrelevant. The floor still rounds a small
positive stake up to `MIN_UNIT`, which is now a rare granularity case
rather than the only outcome.
"""
from backend.analysis.kelly import (MAX_UNIT, MIN_UNIT, NO_BET,
                                     UNITS_PER_BANKROLL, fractional_kelly)


def test_a_unit_is_one_percent_of_bankroll():
    """The conversion constant, stated once so callers can reason in
    bankroll terms rather than reverse-engineering it from a clamp."""
    assert UNITS_PER_BANKROLL == 100


def test_a_realistic_edge_sizes_in_the_middle_of_the_range():
    """A 5-point edge at -110: f* = 0.105, quarter Kelly = 2.6% of
    bankroll = 2.6 units. Previously this returned 0.5, like everything
    else."""
    result = fractional_kelly(0.5738, -110, 0.25)

    assert 2.0 < result < 3.0, f"expected a mid-range stake, got {result}"


def test_the_sizer_is_not_a_constant_function():
    """The regression guard for the actual bug. Before the scale fix this
    returned 0.5 across the entire input space."""
    values = {fractional_kelly(p / 100, odds, 0.25)
              for odds in (-300, -200, -150, -110, 100, 150, 200, 300)
              for p in range(1, 100)}
    positive = {v for v in values if v > NO_BET}

    assert len(positive) > 5, (
        f"a sizer that answers {sorted(positive)} for every bet is not sizing")


def test_a_bigger_edge_stakes_more_than_a_smaller_one():
    """Monotonicity is the property that makes the output meaningful at
    all, and it held vacuously while the function was constant."""
    small = fractional_kelly(0.55, -110, 0.25)
    large = fractional_kelly(0.62, -110, 0.25)

    assert large > small


def test_the_ceiling_is_now_reachable_at_the_default_fraction():
    """3.0 had never been produced in the system's history."""
    assert fractional_kelly(0.75, -110, 0.25) == MAX_UNIT


def test_a_tiny_positive_edge_still_floors_rather_than_declines():
    """The floor keeps its job: a real but sub-minimum stake is a
    granularity question, not a change of direction."""
    result = fractional_kelly(0.5241, -110, 0.25)

    assert result == MIN_UNIT
    assert result > NO_BET


def test_a_negative_edge_still_declines_regardless_of_scale():
    """Scaling a negative recommendation does not make it positive."""
    assert fractional_kelly(0.40, -150, 0.25) == NO_BET
    assert fractional_kelly(0.51, -110, 0.25) == NO_BET


def test_the_kelly_fraction_still_controls_aggression():
    """Half Kelly must stake more than quarter Kelly on the same bet, or
    the fraction argument has stopped doing anything."""
    quarter = fractional_kelly(0.58, -110, 0.25)
    half = fractional_kelly(0.58, -110, 0.50)

    assert half > quarter

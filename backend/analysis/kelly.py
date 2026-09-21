"""Fractional Kelly criterion with adaptive sizing, drawdown protection, and correlation discount."""

DRAWDOWN_THRESHOLD = 0.15  # 15% drawdown triggers protection
CORRELATION_DISCOUNT = 0.20  # 20% reduction per correlated pick

#: Decline the wager. A real stake of nothing, not a missing value -- callers
#: must not coalesce it away (see ``backtesting/backtester.py``).
NO_BET = 0.0
#: Smallest stake worth placing, and the largest the sizer will suggest.
MIN_UNIT = 0.5
MAX_UNIT = 3.0
#: Units per whole bankroll: one unit is 1% of bankroll, the convention the
#: FAQ already assumes when it calls 1.0 units a standard bet.
#:
#: Kelly returns a fraction OF BANKROLL. Clamping that fraction directly to
#: a UNIT range compared two different scales, and because ``f* = p - q/b``
#: cannot exceed 1.0 for a single wager, ``f* * 0.25`` could never reach
#: ``MIN_UNIT``. Every positive-Kelly pick floored to 0.5 and the 3.0
#: ceiling had never been produced -- the function was constant.
UNITS_PER_BANKROLL = 100


def fractional_kelly(model_prob: float, odds: int, fraction: float = 0.25) -> float:
    """Suggested unit size, or :data:`NO_BET` when the wager is -EV.

    Kelly formula: f* = (bp - q) / b
    where:
        b = decimal odds - 1 (net payout per unit bet)
        p = probability of winning
        q = 1 - p = probability of losing

    Returns ``NO_BET`` when f* <= 0, otherwise a size in
    ``[MIN_UNIT, MAX_UNIT]``, where one unit is 1% of bankroll
    (:data:`UNITS_PER_BANKROLL`). Kelly yields a bankroll FRACTION, so the
    conversion to units is what makes the clamp range meaningful; without
    it the two scales never met and this returned 0.5 for every input.

    **The floor may not change the sign of the recommendation.** Rounding a
    small positive stake up to ``MIN_UNIT`` is a policy about bet
    granularity. Returning ``MIN_UNIT`` for a NEGATIVE Kelly asserts the
    opposite of what the criterion computed, which is what this used to do:
    f* <= 0 means the model's probability is at or below the price's implied
    probability, and the old code staked it at 0.5 units anyway.

    That was reachable in practice, not just in theory. A pick is only
    generated on positive edge, but `ensemble` passes a hardcoded -110 to
    this function for every spread and total regardless of the real price.
    -110 implies 52.38%, so any spread whose cover probability sat below that
    arrived here -EV and was staked at the minimum.
    """
    if odds < 0:
        b = 100 / abs(odds)
    else:
        b = odds / 100

    p = model_prob
    q = 1 - p
    full_kelly = (b * p - q) / b

    if full_kelly <= 0:
        return NO_BET

    bankroll_fraction = full_kelly * fraction
    units = bankroll_fraction * UNITS_PER_BANKROLL
    return max(MIN_UNIT, min(MAX_UNIT, round(units, 2)))


def adaptive_fraction(calibration_deviation: float) -> float:
    """Adjust Kelly fraction based on model calibration accuracy.

    Args:
        calibration_deviation: Absolute difference between predicted and actual
                              win rates over the last 30 days.

    Returns:
        Kelly fraction: 0.35 (accurate), 0.25 (moderate), 0.15 (inaccurate).
    """
    if calibration_deviation <= 0.03:
        return 0.35
    elif calibration_deviation <= 0.05:
        return 0.25
    else:
        return 0.15


def apply_drawdown_protection(
    base_fraction: float, current_balance: float, peak_balance: float,
) -> float:
    """Halve Kelly fraction when bankroll drops 15%+ from peak."""
    if peak_balance <= 0:
        return base_fraction
    drawdown = (peak_balance - current_balance) / peak_balance
    if drawdown >= DRAWDOWN_THRESHOLD:
        return base_fraction / 2
    return base_fraction


def apply_correlation_discount(base_fraction: float, same_game_picks: int) -> float:
    """Reduce Kelly fraction when placing multiple bets on the same game."""
    if same_game_picks <= 1:
        return base_fraction
    return base_fraction * (1 - CORRELATION_DISCOUNT)


def sizing_fraction(base_fraction: float, *,
                    calibration_deviation: float | None = None,
                    current_balance: float | None = None,
                    peak_balance: float | None = None,
                    same_game_picks: int = 1) -> float:
    """The Kelly fraction to size with, after every adjustment that applies.

    The single place :func:`adaptive_fraction`,
    :func:`apply_drawdown_protection` and :func:`apply_correlation_discount`
    compose. They had zero production call sites before this existed, and a
    caller assembling them by hand can apply two of three and silently skip
    the last.

    Order is fixed and load-bearing:

    1. ``adaptive_fraction`` **replaces** the base. It returns an absolute
       fraction, not a multiplier, so running it after a discount would
       throw the discount away.
    2. Drawdown halves what survives -- a statement about the bankroll
       rather than about this bet.
    3. Correlation discounts last, the only per-game term.

    **An absent input is skipped, never defaulted.** ``None`` calibration
    means "not measured", which is not "perfectly calibrated"; reading it as
    a deviation of 0.0 would raise every stake to the most aggressive
    fraction on no evidence. Same reasoning as
    :func:`analysis.confidence.calculate_confidence`, where an absent signal
    is absent evidence rather than a vote.
    """
    fraction = base_fraction

    if calibration_deviation is not None:
        fraction = adaptive_fraction(abs(calibration_deviation))

    if current_balance is not None and peak_balance is not None:
        fraction = apply_drawdown_protection(fraction, current_balance,
                                             peak_balance)

    return apply_correlation_discount(fraction, same_game_picks)

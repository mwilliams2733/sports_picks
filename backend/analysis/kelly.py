"""Fractional Kelly criterion with adaptive sizing, drawdown protection, and correlation discount."""

DRAWDOWN_THRESHOLD = 0.15  # 15% drawdown triggers protection
CORRELATION_DISCOUNT = 0.20  # 20% reduction per correlated pick


def fractional_kelly(model_prob: float, odds: int, fraction: float = 0.25) -> float:
    """Calculate fractional Kelly criterion bet size.

    Kelly formula: f* = (bp - q) / b
    where:
        b = decimal odds - 1 (net payout per unit bet)
        p = probability of winning
        q = 1 - p = probability of losing

    Returns suggested unit size clamped to [0.5, 3.0].
    """
    if odds < 0:
        b = 100 / abs(odds)
    else:
        b = odds / 100

    p = model_prob
    q = 1 - p
    full_kelly = (b * p - q) / b

    if full_kelly <= 0:
        return 0.5

    sized = full_kelly * fraction
    return max(0.5, min(3.0, round(sized, 2)))


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

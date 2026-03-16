def fractional_kelly(model_prob: float, odds: int, fraction: float = 0.25) -> float:
    """Calculate fractional Kelly criterion bet size.

    Kelly formula: f* = (bp - q) / b
    where:
        b = decimal odds - 1 (net payout per unit bet)
        p = probability of winning
        q = 1 - p = probability of losing

    We use fractional Kelly (default 25%) to reduce variance.
    Returns suggested unit size (1.0 = standard bet, 2.0 = double, 0.5 = half).
    Capped between 0.5 and 3.0 units.
    """
    # Convert American odds to decimal
    if odds < 0:
        decimal = 1 + (100 / abs(odds))
    else:
        decimal = 1 + (odds / 100)

    b = decimal - 1  # net profit per unit
    p = model_prob
    q = 1 - p

    full_kelly = (b * p - q) / b

    if full_kelly <= 0:
        return 0.5

    suggested = full_kelly * fraction
    return max(0.5, min(3.0, suggested))

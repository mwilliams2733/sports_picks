DEFAULT_THRESHOLDS = {5: 12.0, 4: 8.0, 3: 5.0, 2: 5.0, 1: 3.0}
DEFAULT_MIN_MODELS = {5: 3, 4: 2, 3: 2, 2: 1, 1: 0}


def get_thresholds(session=None, sport: str = "nba") -> dict:
    """Load latest confidence thresholds from DB, or return defaults."""
    if session is None:
        return DEFAULT_THRESHOLDS
    from backend.models import CalibrationHistory
    rows = (
        session.query(CalibrationHistory)
        .filter(CalibrationHistory.sport == sport)
        .order_by(CalibrationHistory.date.asc())
        .all()
    )
    thresholds = dict(DEFAULT_THRESHOLDS)
    for row in rows:
        thresholds[row.confidence_tier] = row.new_threshold
    return thresholds


#: How many signals `DEFAULT_MIN_MODELS` is expressed out of.
TOTAL_SIGNALS = 3


def calculate_confidence(
    edge_pct: float, models_agreeing: int, thresholds: dict | None = None,
    models_available: int = TOTAL_SIGNALS,
) -> int:
    """Pick a confidence tier from the edge and how much agreed with it.

    ``models_available`` is how many signals carried usable data for this
    game. It matters because a signal that cannot distinguish the two sides
    is *absent* evidence, not evidence against, and the vote count alone
    cannot tell those apart.

    Both counters in the ensemble were reading stats nothing computes.
    `offensive_rating` and `defensive_rating` need possessions, which no
    collector fetches, so both sides read the same 100.0 default: the
    moneyline counter's third comparison was `0 > 0` for every game ever
    played, capping it at 2 of a required 3 and putting tier 5 out of reach.
    The totals counter compared those defaults against constants -- `pace <=
    100`, `combined_off <= 200`, `combined_def <= 200` -- all true by
    construction, so every under scored a perfect 3 and every over a 0. All
    42 ncaab tier-5 picks in the database are unders, and every over across
    boxing, mlb, mma and ncaaf sits at tier 1. Neither number described the
    game.

    The requirement therefore scales to what was actually available: a tier
    needing 2 of 3 needs 2 of 3 when three signals exist, and 2 of 2 when one
    is dead. With nothing available the tier is capped at 1 -- an edge no
    signal supports is an unsupported edge, however large, and the danger is
    never a tier that is too low but a high one asserted from nothing, which
    the recalibrator then grades as a real cohort.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    available = max(0, min(models_available, TOTAL_SIGNALS))

    for tier in (5, 4, 3, 2, 1):
        if edge_pct < thresholds.get(tier, 999):
            continue
        needed = DEFAULT_MIN_MODELS.get(tier, 0)
        if available == 0:
            # No signal spoke. Only the tier that asks for none is honest.
            if needed == 0:
                return tier
            continue
        # Ceiling division: 2-of-3 becomes 2-of-2, not 1-of-2.
        required = -(-needed * available // TOTAL_SIGNALS)
        if models_agreeing >= required:
            return tier
    return 0

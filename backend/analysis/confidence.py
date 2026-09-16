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


def calculate_confidence(
    edge_pct: float, models_agreeing: int, thresholds: dict | None = None,
) -> int:
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    min_models = DEFAULT_MIN_MODELS
    for tier in (5, 4, 3, 2, 1):
        if edge_pct >= thresholds.get(tier, 999) and models_agreeing >= min_models.get(tier, 0):
            return tier
    return 0

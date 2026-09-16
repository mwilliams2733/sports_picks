DEFAULT_PROP_THRESHOLDS = {5: 20.0, 4: 15.0, 3: 10.0, 2: 7.0, 1: 5.0}


def get_prop_thresholds(session=None, sport: str = "nba") -> dict:
    """Load latest prop confidence thresholds from DB, or return defaults."""
    if session is None:
        return DEFAULT_PROP_THRESHOLDS
    from backend.models import CalibrationHistory
    rows = (
        session.query(CalibrationHistory)
        .filter(
            CalibrationHistory.sport == sport,
            CalibrationHistory.confidence_tier >= 100,
        )
        .order_by(CalibrationHistory.date.asc())
        .all()
    )
    thresholds = dict(DEFAULT_PROP_THRESHOLDS)
    for row in rows:
        thresholds[row.confidence_tier - 100] = row.new_threshold
    return thresholds


def calculate_prop_confidence(edge_pct: float, thresholds: dict | None = None) -> int:
    if thresholds is None:
        thresholds = DEFAULT_PROP_THRESHOLDS
    for tier in (5, 4, 3, 2, 1):
        if edge_pct >= thresholds.get(tier, 999):
            return tier
    return 0

from datetime import date

from backend.analysis.confidence import DEFAULT_THRESHOLDS, calculate_confidence, get_thresholds
from backend.analysis.prop_confidence import DEFAULT_PROP_THRESHOLDS, get_prop_thresholds
from backend.database import get_engine, get_session
from backend.models import Base, CalibrationHistory

def test_five_stars(): assert calculate_confidence(edge_pct=15.0, models_agreeing=3) == 5
def test_four_stars(): assert calculate_confidence(edge_pct=9.0, models_agreeing=2) == 4
def test_three_stars(): assert calculate_confidence(edge_pct=6.0, models_agreeing=2) == 3
def test_two_stars(): assert calculate_confidence(edge_pct=5.5, models_agreeing=1) == 2
def test_one_star(): assert calculate_confidence(edge_pct=3.5, models_agreeing=1) == 1
def test_zero_below_threshold(): assert calculate_confidence(edge_pct=2.0, models_agreeing=1) == 0


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def test_get_thresholds_no_session_returns_defaults():
    assert get_thresholds(session=None, sport="nba") == DEFAULT_THRESHOLDS


def test_get_thresholds_newest_row_per_tier_wins():
    session = _session()
    session.add(CalibrationHistory(
        date=date(2026, 1, 1), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=12.0, new_threshold=9.0,
    ))
    session.add(CalibrationHistory(
        date=date(2026, 3, 1), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=12.0, new_threshold=7.0,
    ))
    session.commit()
    thresholds = get_thresholds(session=session, sport="nba")
    assert thresholds[5] == 7.0


def test_get_thresholds_fills_missing_tiers_from_defaults():
    session = _session()
    session.add(CalibrationHistory(
        date=date(2026, 1, 1), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.75,
        sample_size=20, old_threshold=12.0, new_threshold=11.0,
    ))
    session.commit()
    thresholds = get_thresholds(session=session, sport="nba")
    assert thresholds[5] == 11.0
    for tier in (4, 3, 2, 1):
        assert thresholds[tier] == DEFAULT_THRESHOLDS[tier]


def test_get_prop_thresholds_no_session_returns_defaults():
    assert get_prop_thresholds(session=None, sport="nba") == DEFAULT_PROP_THRESHOLDS


def test_get_prop_thresholds_newest_row_wins_and_decodes_offset():
    session = _session()
    session.add(CalibrationHistory(
        date=date(2026, 1, 1), sport="nba", confidence_tier=105,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=20.0, new_threshold=18.0,
    ))
    session.add(CalibrationHistory(
        date=date(2026, 3, 1), sport="nba", confidence_tier=105,
        predicted_win_rate=0.70, actual_win_rate=0.60,
        sample_size=20, old_threshold=20.0, new_threshold=16.0,
    ))
    session.commit()
    thresholds = get_prop_thresholds(session=session, sport="nba")
    assert thresholds[5] == 16.0
    for tier in (4, 3, 2, 1):
        assert thresholds[tier] == DEFAULT_PROP_THRESHOLDS[tier]

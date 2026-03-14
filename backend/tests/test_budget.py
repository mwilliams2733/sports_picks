from datetime import datetime
from backend.collectors.budget import ApiBudgetTracker

def test_can_make_request_under_limit(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=450)
    assert tracker.can_make_request("odds_api") is True

def test_record_request_increments_count(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=450)
    tracker.record_request("odds_api")
    assert tracker.get_count("odds_api") == 1

def test_cannot_make_request_at_pause_limit(db_session, db_engine):
    from backend.models import Base
    Base.metadata.create_all(db_engine)
    tracker = ApiBudgetTracker(db_session, monthly_limit=500, pause_at=5)
    for _ in range(5):
        tracker.record_request("odds_api")
    assert tracker.can_make_request("odds_api") is False

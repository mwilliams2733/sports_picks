from datetime import datetime, date, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from backend.models import Base, ApiUsage
from backend.collectors.budget import check_budget, record_api_call, get_credit_summary, BudgetStatus


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_check_budget_allows_when_under_limit():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    status = check_budget(session, budget)
    assert status == BudgetStatus.OK


def test_check_budget_blocks_at_monthly_limit():
    session = make_session()
    budget = {"monthly_limit": 5, "daily_target": 600, "reserve": 2}
    for i in range(5):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.MONTHLY_EXHAUSTED


def test_check_budget_warns_at_daily_target():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 3, "reserve": 2000}
    for i in range(3):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.DAILY_SOFT_LIMIT


def test_check_budget_blocks_daily_when_in_reserve():
    session = make_session()
    budget = {"monthly_limit": 100, "daily_target": 3, "reserve": 50}
    for i in range(55):
        session.add(ApiUsage(endpoint="odds", sport="nba", credits_used=1,
                             created_at=datetime.now(tz=timezone.utc)))
    session.commit()
    status = check_budget(session, budget)
    assert status == BudgetStatus.RESERVE_EXHAUSTED


def test_record_api_call_creates_row():
    session = make_session()
    record_api_call(session, "events", "nba", requests_remaining=19999)
    rows = session.query(ApiUsage).all()
    assert len(rows) == 1
    assert rows[0].endpoint == "events"
    assert rows[0].sport == "nba"
    assert rows[0].requests_remaining == 19999


def test_created_at_survives_sqlite_roundtrip_and_still_filters_correctly():
    """ApiUsage.created_at is a plain (non-timezone) DateTime column, so it
    comes back naive after a SQLite round trip even though every write is
    UTC-aware. Confirm this doesn't break check_budget's monthly/daily
    filters: SQLAlchemy's SQLite dialect serializes both the stored column
    and the aware Python-side filter bound identically, so the comparison
    stays a consistent UTC-vs-UTC string comparison rather than mismatching."""
    session = make_session()
    record_api_call(session, "odds", "nba", requests_remaining=100)

    row = session.query(ApiUsage).first()
    assert row.created_at.tzinfo is None, "expected SQLite round-trip to strip tzinfo"

    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    summary = get_credit_summary(session, budget)
    assert summary["monthly_used"] == 1
    assert summary["daily_used"] == 1


def test_get_credit_summary():
    session = make_session()
    budget = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}
    record_api_call(session, "events", "nba", requests_remaining=19999)
    record_api_call(session, "player_props", "nba", requests_remaining=19998)
    summary = get_credit_summary(session, budget)
    assert summary["monthly_used"] == 2
    assert summary["monthly_limit"] == 20000
    assert summary["monthly_remaining"] == 19998
    assert summary["daily_used"] == 2
    assert summary["daily_target"] == 600
    assert summary["api_requests_remaining"] == 19998

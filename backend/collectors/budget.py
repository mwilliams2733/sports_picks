import enum
import logging
from datetime import datetime, date, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.models import ApiUsage

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000}


class BudgetStatus(enum.Enum):
    OK = "ok"
    DAILY_SOFT_LIMIT = "daily_soft_limit"
    RESERVE_EXHAUSTED = "reserve_exhausted"
    MONTHLY_EXHAUSTED = "monthly_exhausted"


def check_budget(session: Session, budget: dict) -> BudgetStatus:
    monthly_limit = budget["monthly_limit"]
    daily_target = budget["daily_target"]
    reserve = budget["reserve"]

    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    monthly_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= month_start
    ).scalar() or 0

    if monthly_used >= monthly_limit:
        return BudgetStatus.MONTHLY_EXHAUSTED

    daily_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= today_start
    ).scalar() or 0

    monthly_remaining = monthly_limit - monthly_used
    if daily_used >= daily_target and monthly_remaining <= reserve:
        return BudgetStatus.RESERVE_EXHAUSTED

    if daily_used >= daily_target:
        return BudgetStatus.DAILY_SOFT_LIMIT

    return BudgetStatus.OK


def record_api_call(session: Session, endpoint: str, sport: str,
                    requests_remaining: int | None = None) -> None:
    session.add(ApiUsage(
        endpoint=endpoint, sport=sport, credits_used=1,
        requests_remaining=requests_remaining,
        created_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    logger.info(f"API credit used: {endpoint}/{sport} (remaining: {requests_remaining})")


def get_credit_summary(session: Session, budget: dict) -> dict:
    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    monthly_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= month_start
    ).scalar() or 0

    daily_used = session.query(func.sum(ApiUsage.credits_used)).filter(
        ApiUsage.created_at >= today_start
    ).scalar() or 0

    latest = session.query(ApiUsage).order_by(ApiUsage.created_at.desc()).first()
    api_remaining = latest.requests_remaining if latest else None

    return {
        "monthly_used": monthly_used,
        "monthly_limit": budget["monthly_limit"],
        "monthly_remaining": budget["monthly_limit"] - monthly_used,
        "daily_used": daily_used,
        "daily_target": budget["daily_target"],
        "api_requests_remaining": api_remaining,
    }

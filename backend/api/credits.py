from fastapi import APIRouter, Request
from backend.config import load_config
from backend.database import get_session
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET

router = APIRouter()


@router.get("/")
def get_credits(request: Request):
    """Return current API credit usage for the month and today."""
    session = get_session(request.app.state.engine)
    try:
        config = load_config("config.yaml")
        budget = config.get("odds_budget", DEFAULT_BUDGET)
        return get_credit_summary(session, budget)
    finally:
        session.close()

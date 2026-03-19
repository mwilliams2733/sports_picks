import logging
from datetime import date
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from backend.config import load_config, is_sport_in_season
from backend.database import get_session
from backend.pipeline.full_pipeline import (
    fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET
from backend.exceptions import BudgetExhaustedError
from backend.models import StrategyModel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/run")
async def trigger_pipeline(
    request: Request,
    sport: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
):
    """Run the pipeline: fetch games, odds, props, generate picks.

    Optional filters:
    - sport: only fetch for this sport (e.g., ?sport=nba)
    - window_start/window_end: ISO datetimes to scope a time window
    """
    session = get_session(request.app.state.engine)
    try:
        config = load_config("config.yaml")
        today = date.today()
        budget = config.get("odds_budget", DEFAULT_BUDGET)

        if sport:
            active_sports = [sport]
        else:
            active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"], today)]
        logger.info(f"Pipeline: active sports = {active_sports}")

        # Build window game filter if time window specified
        window_game_ids = None
        if window_start or window_end:
            from datetime import datetime as dt
            from backend.models import Game
            query = session.query(Game.id).filter(Game.date == today)
            if sport:
                query = query.filter(Game.sport == sport)
            if window_start:
                ws = dt.fromisoformat(window_start)
                query = query.filter(Game.start_time >= ws)
            if window_end:
                we = dt.fromisoformat(window_end)
                query = query.filter(Game.start_time <= we)
            window_game_ids = {row[0] for row in query.all()}

        # Step 1: Fetch and store games from ESPN
        games_stored = await fetch_and_store_games(session, active_sports, today)

        # Step 2: Fetch and store odds + props from The Odds API
        odds_stored = 0
        props_stored = 0
        api_key = config.get("odds_api_key")
        if api_key:
            odds_stored = await fetch_and_store_odds(session, active_sports, api_key, budget=budget)
            props_stored = await fetch_and_store_props(
                session, active_sports, api_key, budget=budget,
                window_game_ids=window_game_ids,
            )

        # Step 3: Generate game picks
        game_picks = 0
        game_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "game",
        ).first()
        if game_strategy:
            game_picks = generate_and_store_picks(session, game_strategy.id, today)

        # Step 4: Run prop pipeline
        props_analyzed = 0
        prop_picks = 0
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "prop",
        ).first()
        try:
            prop_result = await run_prop_pipeline(
                session, target_date=today,
                strategy_id=prop_strategy.id if prop_strategy else None,
            )
            props_analyzed = prop_result.get("props_analyzed", 0)
            prop_picks = prop_result.get("picks_generated", 0)
        except Exception as e:
            logger.error(f"Prop pipeline error: {e}")

        summary = get_credit_summary(session, budget)

        return {
            "status": "completed",
            "active_sports": active_sports,
            "games_stored": games_stored,
            "odds_stored": odds_stored,
            "props_stored": props_stored,
            "props_analyzed": props_analyzed,
            "picks_generated": game_picks + prop_picks,
            "credits_used": summary["daily_used"],
            "credits_remaining_today": summary["daily_target"] - summary["daily_used"],
            "credits_remaining_month": summary["monthly_remaining"],
        }
    except BudgetExhaustedError as e:
        summary = get_credit_summary(session, budget)
        return JSONResponse(
            status_code=429,
            content={"status": "budget_exhausted", "message": str(e), **summary},
        )
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )
    finally:
        session.close()

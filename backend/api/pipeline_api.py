import logging
from datetime import date
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from backend.config import load_config, is_sport_in_season
from backend.database import get_session
from backend.pipeline.full_pipeline import fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.models import StrategyModel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/run")
async def trigger_pipeline(request: Request):
    """Run the full pipeline: fetch games, odds, props, generate picks.

    Skips the slow player stats fetch (nba_api) — that runs via the scheduled job.
    """
    session = get_session(request.app.state.engine)
    try:
        config = load_config("config.yaml")
        today = date.today()
        active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"], today)]
        logger.info(f"Pipeline: active sports = {active_sports}")

        # Step 1: Fetch and store games from ESPN
        games_stored = await fetch_and_store_games(session, active_sports, today)

        # Step 2: Fetch and store odds from The Odds API
        odds_stored = 0
        props_stored = 0
        api_key = config.get("odds_api_key")
        if api_key:
            odds_stored = await fetch_and_store_odds(session, active_sports, api_key)
            props_stored = await fetch_and_store_props(session, active_sports, api_key)

        # Step 3: Generate game picks if strategy exists
        game_picks = 0
        game_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "game",
        ).first()
        if game_strategy:
            game_picks = generate_and_store_picks(session, game_strategy.id, today)

        return {
            "status": "completed",
            "active_sports": active_sports,
            "games_stored": games_stored,
            "odds_stored": odds_stored,
            "props_stored": props_stored,
            "picks_generated": game_picks,
        }
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )
    finally:
        session.close()

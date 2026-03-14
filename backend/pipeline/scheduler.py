import asyncio
import logging
import time
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session
from backend.collectors.espn import ESPNCollector
from backend.collectors.odds_api import OddsAPICollector
from backend.collectors.budget import ApiBudgetTracker
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.grader import grade_pick
from backend.models import Base, Game, PickModel, PickResult, StrategyModel

logger = logging.getLogger(__name__)

def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    Base.metadata.create_all(engine)
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: daily_job(config, engine), 'cron', hour=6, minute=0, id='daily_pipeline')
    scheduler.start()
    logger.info("Pipeline scheduler started. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()

def daily_job(config, engine):
    session = get_session(engine)
    try:
        logger.info("Starting daily pipeline run")
        grade_pending_picks(session)
        sports = ["nba", "nfl", "ncaab", "ncaaf"]
        active_sports = [s for s in sports if is_sport_in_season(s, config["seasons"])]
        logger.info(f"Active sports: {active_sports}")
        for sport in active_sports:
            asyncio.run(fetch_sport_data(session, config, sport))
        active_strategy = session.query(StrategyModel).filter(StrategyModel.is_active == True).first()
        if active_strategy:
            count = generate_and_store_picks(session, active_strategy.id)
            logger.info(f"Generated {count} picks")
        try:
            prop_strategy = session.query(StrategyModel).filter(
                StrategyModel.is_active == True,
                StrategyModel.strategy_type == "prop"
            ).first()
            prop_count = asyncio.run(run_prop_pipeline(
                session,
                target_date=date.today(),
                strategy_id=prop_strategy.id if prop_strategy else None
            ))
            logger.info(f"Prop pipeline generated {prop_count} prop picks")
        except Exception as prop_e:
            logger.error(f"Prop pipeline error: {prop_e}")
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
    finally:
        session.close()

async def fetch_sport_data(session, config, sport):
    espn = ESPNCollector()
    try:
        today_str = date.today().strftime("%Y%m%d")
        games = await espn.fetch_scoreboard(sport, today_str)
        logger.info(f"Fetched {len(games)} {sport} games from ESPN")
    finally:
        await espn.close()
    budget = ApiBudgetTracker(session,
        monthly_limit=config["odds_budget"]["monthly_limit"],
        pause_at=config["odds_budget"]["pause_at"])
    if budget.can_make_request("odds_api"):
        odds_collector = OddsAPICollector(config["odds_api_key"])
        try:
            odds = await odds_collector.fetch_odds(sport)
            budget.record_request("odds_api")
            logger.info(f"Fetched odds for {len(odds)} {sport} games")
        finally:
            await odds_collector.close()

def grade_pending_picks(session):
    ungraded = (
        session.query(PickModel, Game)
        .join(Game, PickModel.game_id == Game.id)
        .filter(Game.status == "final")
        .filter(~PickModel.id.in_(session.query(PickResult.pick_id)))
        .all()
    )
    for pick, game in ungraded:
        if game.home_score is not None and game.away_score is not None:
            result, payout = grade_pick(pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds_at_pick or -110)
            session.add(PickResult(pick_id=pick.id, result=result, payout=payout))
    session.commit()
    logger.info(f"Graded {len(ungraded)} picks")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline()

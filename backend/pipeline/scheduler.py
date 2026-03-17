import asyncio
import logging
import time
from datetime import date, datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session
from backend.pipeline.full_pipeline import fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.grader import grade_pick
from backend.models import Base, Game, Odds, PickModel, PickResult, StrategyModel, PaperPick, UserProfile, PlayerStat
from backend.analysis.odds_utils import calculate_payout
from backend.pipeline.grader import grade_prop_pick

logger = logging.getLogger(__name__)

def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    Base.metadata.create_all(engine)
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: daily_job(config, engine), 'cron', hour=6, minute=0, id='daily_pipeline')
    from backend.pipeline.recalibration_job import run_recalibration
    scheduler.add_job(
        lambda: run_recalibration(config["database_path"]),
        'cron', hour=3, minute=0, id='recalibration',
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Pipeline scheduler started (daily at 6 AM, recalibration at 3 AM).")
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

        active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"])]
        logger.info(f"Active sports: {active_sports}")

        # Fetch and store games, odds, props
        asyncio.run(fetch_and_store_games(session, active_sports, date.today()))

        api_key = config.get("odds_api_key")
        if api_key:
            asyncio.run(fetch_and_store_odds(session, active_sports, api_key))
            asyncio.run(fetch_and_store_props(session, active_sports, api_key))

        # Generate game picks
        active_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,
            StrategyModel.strategy_type == "game",
        ).first()
        if active_strategy:
            count = generate_and_store_picks(session, active_strategy.id)
            logger.info(f"Generated {count} game picks")

        # Run prop pipeline
        try:
            prop_strategy = session.query(StrategyModel).filter(
                StrategyModel.is_active == True,
                StrategyModel.strategy_type == "prop"
            ).first()
            prop_result = asyncio.run(run_prop_pipeline(
                session,
                target_date=date.today(),
                strategy_id=prop_strategy.id if prop_strategy else None
            ))
            logger.info(f"Prop pipeline: {prop_result}")
        except Exception as prop_e:
            logger.error(f"Prop pipeline error: {prop_e}")
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
    finally:
        session.close()

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

            # Get closing odds (most recent odds snapshot for this game)
            closing_odds_val = None
            closing_odds_row = (
                session.query(Odds)
                .filter(Odds.game_id == game.id)
                .order_by(Odds.timestamp.desc())
                .first()
            )
            if closing_odds_row:
                if pick.pick_type == "moneyline":
                    if "HOME" in pick.pick_value:
                        closing_odds_val = closing_odds_row.moneyline_home
                    else:
                        closing_odds_val = closing_odds_row.moneyline_away
                elif pick.pick_type == "spread":
                    if "HOME" in pick.pick_value:
                        closing_odds_val = -110  # spreads are typically -110
                    else:
                        closing_odds_val = -110
                elif pick.pick_type == "over_under":
                    closing_odds_val = -110
                elif pick.pick_type == "prop":
                    closing_odds_val = closing_odds_row.moneyline_home  # fallback

            session.add(PickResult(
                pick_id=pick.id, result=result, payout=payout,
                odds_at_close=closing_odds_val
            ))
    session.commit()
    logger.info(f"Graded {len(ungraded)} strategy picks")

    # Also grade pending PaperPicks
    pending_paper = (
        session.query(PaperPick, Game)
        .join(Game, PaperPick.game_id == Game.id)
        .filter(PaperPick.result.is_(None))
        .filter(Game.status == "final")
        .all()
    )
    paper_graded = 0
    for pick, game in pending_paper:
        if game.home_score is None or game.away_score is None:
            continue

        if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
            player_stat = (
                session.query(PlayerStat)
                .filter_by(player_name=pick.prop_player, stat_type="game_log", game_date=game.date)
                .first()
            )
            prop_result = grade_prop_pick(pick.pick_value, pick.prop_market, player_stat)
            if not prop_result:
                continue
            pick.result = prop_result[0]
        else:
            result, _ = grade_pick(
                pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds
            )
            pick.result = result

        if pick.result == "win":
            pick.payout = pick.stake * calculate_payout(pick.odds)
        elif pick.result == "push":
            pick.payout = 0.0
        else:
            pick.payout = -pick.stake

        pick.graded_at = datetime.now(timezone.utc) if hasattr(pick, 'graded_at') else None
        paper_graded += 1

    session.commit()
    logger.info(f"Auto-graded {paper_graded} paper picks")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline()

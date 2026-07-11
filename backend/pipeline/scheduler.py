import asyncio
import logging
import time
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session, migrate_api_usage, migrate_game_start_time
from backend.pipeline.full_pipeline import (
    fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.grader import grade_pick, grade_prop_pick, capture_closing_odds
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET
from backend.models import (
    Base, Game, PickModel, PickResult, StrategyModel,
    PaperPick, PlayerStat,
)
from backend.analysis.odds_utils import calculate_payout

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
LEAD_TIME = timedelta(hours=2)


def _as_utc(dt: datetime) -> datetime:
    """Normalize a possibly-naive datetime to UTC-aware.

    Game.start_time is a plain (non-timezone) DateTime column, so values
    written as UTC-aware come back naive after a round trip through SQLite.
    Every write path in this app uses UTC, so a naive value here is treated
    as UTC rather than left to raise a naive/aware TypeError at compare time.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def cluster_game_windows(games: list[dict], gap_minutes: int = 30) -> list[dict]:
    if not games:
        return []
    sorted_games = sorted(games, key=lambda g: _as_utc(g["start_time"]))
    gap = timedelta(minutes=gap_minutes)
    windows = []
    current = [sorted_games[0]]
    window_anchor = _as_utc(sorted_games[0]["start_time"])
    for g in sorted_games[1:]:
        start = _as_utc(g["start_time"])
        if start - window_anchor > gap:
            windows.append(_build_window(current))
            current = [g]
            window_anchor = start
        else:
            current.append(g)
    windows.append(_build_window(current))
    return windows


def _build_window(games: list[dict]) -> dict:
    starts = [_as_utc(g["start_time"]) for g in games]
    earliest = min(starts)
    return {
        "games": games,
        "run_at": earliest - LEAD_TIME,
        "window_start": earliest,
        "window_end": max(starts),
    }


def configure_scheduler(config: dict, engine) -> BackgroundScheduler:
    """Build a BackgroundScheduler with the standard cron job set, but DO
    NOT start it — the caller is responsible for `.start()` and matching
    `.shutdown()`.

    Used both by the standalone run_pipeline() entry point and by the
    FastAPI lifespan in api/main.py (when ENABLE_SCHEDULER=1).
    """
    scheduler = BackgroundScheduler(timezone=ET)
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler),
        'cron', hour=8, minute=0, id='morning_scout', replace_existing=True,
    )
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler, is_retry=True),
        'cron', hour=9, minute=0, id='scout_retry_9', replace_existing=True,
    )
    scheduler.add_job(
        lambda: morning_scout(config, engine, scheduler, is_retry=True),
        'cron', hour=10, minute=0, id='scout_retry_10', replace_existing=True,
    )
    from backend.pipeline.recalibration_job import run_recalibration
    scheduler.add_job(
        lambda: run_recalibration(config["database_path"]),
        'cron', hour=3, minute=0, id='recalibration', replace_existing=True,
    )
    return scheduler


def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    migrate_api_usage(engine)
    migrate_game_start_time(engine)
    Base.metadata.create_all(engine)

    scheduler = configure_scheduler(config, engine)
    scheduler.start()
    logger.info("Scheduler started (morning scout at 8 AM ET, recalibration at 3 AM ET)")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


def morning_scout(config, engine, scheduler, is_retry=False):
    session = get_session(engine)
    try:
        grade_pending_picks(session)
        active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"])]
        scheduled_sports = [s for s in active_sports if s in ("nba", "nfl")]
        if not scheduled_sports:
            logger.info("No auto-scheduled sports in season today")
            return
        today = date.today()
        try:
            asyncio.run(fetch_and_store_games(session, scheduled_sports, today))
        except Exception as e:
            if is_retry:
                logger.error(f"Scout retry failed fetching games: {e}")
                return
            logger.warning(f"Scout failed fetching games: {e}, will retry at 9/10 AM")
            return

        # Scout succeeded — remove retry jobs
        for retry_id in ("scout_retry_9", "scout_retry_10"):
            try:
                scheduler.remove_job(retry_id)
            except Exception:
                pass

        existing_jobs = {j.id for j in scheduler.get_jobs()}
        for sport in scheduled_sports:
            games = session.query(Game).filter(
                Game.sport == sport, Game.date == today,
                Game.status == "scheduled", Game.start_time.isnot(None),
            ).all()
            if not games:
                logger.info(f"No {sport} games scheduled for today, no windows created")
                continue
            game_dicts = [{"id": g.id, "start_time": g.start_time} for g in games]
            windows = cluster_game_windows(game_dicts)
            for i, window in enumerate(windows):
                job_id = f"window_{sport}_{today}_{i}"
                if job_id in existing_jobs:
                    continue
                run_at = window["run_at"]
                now_utc = datetime.now(tz=timezone.utc)
                if run_at <= now_utc:
                    logger.info(f"Window {job_id} run_at is past, running now")
                    _run_window(config, engine, sport, window)
                else:
                    run_at_et = run_at.astimezone(ET)
                    scheduler.add_job(
                        lambda c=config, e=engine, s=sport, w=window: _run_window(c, e, s, w),
                        'date', run_date=run_at_et, id=job_id, replace_existing=True,
                    )
                    earliest_et = window["window_start"].astimezone(ET)
                    n_games = len(window["games"])
                    logger.info(
                        f"Scheduled {sport} window: {n_games} games tipping off "
                        f"~{earliest_et.strftime('%I:%M %p')} ET, pipeline run at "
                        f"{run_at_et.strftime('%I:%M %p')} ET"
                    )
    except Exception as e:
        logger.error(f"Morning scout error: {e}", exc_info=True)
    finally:
        session.close()


def _run_window(config, engine, sport: str, window: dict):
    session = get_session(engine)
    try:
        today = date.today()
        budget = config.get("odds_budget", DEFAULT_BUDGET)
        api_key = config.get("odds_api_key")
        window_game_ids = {g["id"] for g in window["games"]}
        logger.info(f"Running {sport} window: {len(window_game_ids)} games")
        if api_key:
            asyncio.run(fetch_and_store_odds(session, [sport], api_key, budget=budget))
            asyncio.run(fetch_and_store_props(
                session, [sport], api_key, budget=budget, window_game_ids=window_game_ids,
            ))
        game_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True, StrategyModel.strategy_type == "game",
        ).first()
        if game_strategy:
            pitcher_scores = None
            if sport == "mlb":
                try:
                    scores_by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
                    pitcher_scores = _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
                except Exception as exc:
                    logger.warning(
                        "MLB pitcher fetch failed (%s); proceeding with neutral pitcher scores", exc
                    )
                    pitcher_scores = None
            count = generate_and_store_picks(session, game_strategy.id, today, pitcher_scores=pitcher_scores)
            logger.info(f"Generated {count} game picks")
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True, StrategyModel.strategy_type == "prop",
        ).first()
        try:
            result = asyncio.run(run_prop_pipeline(
                session, target_date=today,
                strategy_id=prop_strategy.id if prop_strategy else None,
            ))
            logger.info(f"Prop pipeline: {result}")
        except Exception as e:
            logger.error(f"Prop pipeline error: {e}")
        summary = get_credit_summary(session, budget)
        logger.info(f"Window complete. Credits today: {summary['daily_used']}, month: {summary['monthly_used']}/{summary['monthly_limit']}")
    except Exception as e:
        logger.error(f"Window run error ({sport}): {e}", exc_info=True)
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
            pick_result = PickResult(pick_id=pick.id, result=result, payout=payout)
            capture_closing_odds(
                session, pick_result, game.id,
                pick.pick_type, pick.pick_value, pick.odds_at_pick,
            )
            session.add(pick_result)
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


async def fetch_pitcher_scores_for_date(target_date) -> dict[tuple[str, str], dict[str, float]]:
    """Return {(home_abbr, away_abbr): {'home': score, 'away': score}} for today's MLB games.

    Keying by team abbreviation tuple keeps the upstream MLB game pk out of our
    internal data model — the caller translates the tuple to Game.id by querying
    the local DB. Missing pitchers (not yet announced) get neutral 0.5 scores.
    """
    from backend.collectors.mlb_stats import MLBStatsCollector
    from backend.analysis.pitcher import pitcher_skill_score
    collector = MLBStatsCollector()
    out: dict[tuple[str, str], dict[str, float]] = {}
    try:
        games = await collector.fetch_schedule(target_date)
        for g in games:
            home_id = g.get("home_probable_pitcher_id")
            away_id = g.get("away_probable_pitcher_id")
            home_stats = await collector.fetch_pitcher_recent(home_id, season=target_date.year) if home_id else None
            away_stats = await collector.fetch_pitcher_recent(away_id, season=target_date.year) if away_id else None
            home_abbr = g.get("home_team")
            away_abbr = g.get("away_team")
            if home_abbr is None or away_abbr is None:
                continue
            out[(home_abbr, away_abbr)] = {
                "home": pitcher_skill_score(
                    era=home_stats["era_recent"] if home_stats else None,
                    k9=home_stats["k9_recent"] if home_stats else None,
                ),
                "away": pitcher_skill_score(
                    era=away_stats["era_recent"] if away_stats else None,
                    k9=away_stats["k9_recent"] if away_stats else None,
                ),
            }
    finally:
        await collector.close()
    return out


async def ingest_recent_ufc_event(event_url: str, event_date,
                                  db_path: str = "sports_picks.db") -> dict:
    """Fetch a UFCStats event page, parse fights, upsert Team/EloRating/Game rows.

    Idempotent: re-running for the same event updates existing Game rows rather
    than inserting duplicates. Fighter pairs are matched as an unordered set —
    {home_team_id, away_team_id} — because UFCStats can flip the corner order
    between the announce page and the result page.

    The grader (separate path) applies Elo updates when these Game rows are
    next picked up — this function does NOT mutate Elo directly.
    """
    from backend.collectors.ufcstats_scraper import fetch_event_html, parse_event_fights
    from backend.models import Team, Game, EloRating
    from sqlalchemy import or_, and_

    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        html = await fetch_event_html(event_url)
        fights = parse_event_fights(html)

        def _upsert_fighter(name: str) -> Team:
            existing = session.query(Team).filter(Team.sport == "mma", Team.name == name).first()
            if existing:
                return existing
            t = Team(name=name, abbreviation=name[:32], sport="mma")
            session.add(t); session.flush()
            session.add(EloRating(team_id=t.id, sport="mma", rating=1500.0))
            session.flush()
            return t

        fights_ingested = 0
        fighters_seen: set[int] = set()
        for f in fights:
            a = _upsert_fighter(f["fighter_a_name"])
            b = _upsert_fighter(f["fighter_b_name"])
            fighters_seen.update([a.id, b.id])
            if f["winner"] == "fighter_a":
                home_score, away_score = 1, 0
            elif f["winner"] == "fighter_b":
                home_score, away_score = 0, 1
            else:  # draw
                home_score, away_score = 1, 1

            # Match unordered fighter pair so re-ingestion with corner flipped
            # still finds the same row.
            existing_game = (
                session.query(Game)
                .filter(
                    Game.sport == "mma",
                    Game.date == event_date,
                    or_(
                        and_(Game.home_team_id == a.id, Game.away_team_id == b.id),
                        and_(Game.home_team_id == b.id, Game.away_team_id == a.id),
                    ),
                )
                .first()
            )
            if existing_game:
                # Preserve the original corner orientation; flip scores if needed.
                if existing_game.home_team_id == a.id:
                    existing_game.home_score = home_score
                    existing_game.away_score = away_score
                else:
                    existing_game.home_score = away_score
                    existing_game.away_score = home_score
                existing_game.status = "final"
            else:
                session.add(Game(
                    sport="mma", season=str(event_date.year), date=event_date,
                    home_team_id=a.id, away_team_id=b.id,
                    home_score=home_score, away_score=away_score, status="final",
                ))
            fights_ingested += 1

        session.commit()
        return {
            "fights_ingested": fights_ingested,
            "fighters_created_or_matched": len(fighters_seen),
        }
    finally:
        session.close()


def _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, target_date) -> dict[int, dict[str, float]]:
    """Translate {(home_abbr, away_abbr): scores} -> {game.id: scores} by joining
    Game rows for sport=mlb on target_date against Team abbreviations.
    """
    from backend.models import Game, Team
    games = session.query(Game).filter(Game.sport == "mlb", Game.date == target_date).all()
    abbr_lookup = {t.id: t.abbreviation for t in session.query(Team).filter(Team.sport == "mlb").all()}
    out: dict[int, dict[str, float]] = {}
    for g in games:
        key = (abbr_lookup.get(g.home_team_id), abbr_lookup.get(g.away_team_id))
        if key in scores_by_abbr:
            out[g.id] = scores_by_abbr[key]
        else:
            logger.warning(
                "MLB pitcher remap: no score for game %s on %s (key=%s, available=%s)",
                g.id, target_date, key, list(scores_by_abbr.keys()),
            )
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_pipeline()

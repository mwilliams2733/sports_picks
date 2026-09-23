import asyncio
import logging
import os
import time
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from backend.config import load_config, is_sport_in_season
from backend.database import get_engine, get_session, run_migrations
from backend.pipeline.full_pipeline import (
    fetch_and_store_games, fetch_and_store_odds, fetch_and_store_props, ALL_SPORTS,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.scripts.finalize_mma import finalize_stuck_bouts
from backend.scripts.finalize_combat import COMBAT_SPORTS, finalize_from_scores
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.grader import grade_pick, grade_prop_pick, payout_for, capture_closing_odds, grade_completed_games
from backend.collectors.espn_box_score import (POSSESSION_SPORTS,
                                               collect_box_scores_for_final_games,
                                               collect_team_box_scores)
from backend.collectors.budget import get_credit_summary, DEFAULT_BUDGET
from backend.models import (
    Base, Game, PickModel, PickResult, StrategyModel,
    PaperPick, PlayerStat,
)
from backend.analysis.odds_utils import calculate_payout

logger = logging.getLogger(__name__)
from backend.time_utils import ET, et_today  # noqa: F401  (ET re-exported)
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


#: How many days back morning_scout re-asks ESPN about. It runs at 8/9/10am
#: ET, before that day's games are played, so without a lookback nothing ever
#: learns a score: the next run asks about the new today. Three days covers a
#: missed weekend. A guess, not a measurement -- revisit once the steady-state
#: gap is visible.
LOOKBACK_DAYS = 3

#: Team sports whose games come from ESPN's scoreboard, which is keyed by
#: team abbreviation. mma is excluded because a bout is keyed by fighter
#: pair instead: `finalize_stuck_bouts` owns its finals, and it runs below.
#: Boxing is excluded because ESPN publishes no boxing scoreboard at all --
#: its rows need a source this repository does not have, and are left
#: `scheduled` rather than guessed at.
ESPN_TEAM_SPORTS = ("nba", "nfl", "ncaab", "ncaaf", "mlb")


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

    digest_cfg = config.get("digest", {}) or {}
    if digest_cfg.get("enabled") or os.environ.get("DIGEST_DRY_RUN") == "1":
        from backend.digest.job import send_daily_digest
        scheduler.add_job(
            lambda: send_daily_digest(config, engine),
            'cron', hour=digest_cfg.get("send_hour_et", 11), minute=0,
            id='daily_digest', replace_existing=True,
        )
    return scheduler


def run_pipeline(config_path: str = "config.yaml"):
    config = load_config(config_path)
    engine = get_engine(config["database_path"])
    # Shares backend.database.run_migrations with create_app so this
    # standalone process can never fall behind the web app's schema.
    run_migrations(engine)

    scheduler = configure_scheduler(config, engine)
    scheduler.start()
    logger.info("Scheduler started (morning scout at 8 AM ET, recalibration at 3 AM ET)")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()


def windowless_sports(active_sports) -> list[str]:
    """In-season sports that get no window, so nothing fetches their odds.

    A window is clustered around ESPN start times, so it only exists for
    sports with an ESPN schedule. boxing and mma have none -- their games
    come from the Odds API itself -- so they fell out of the only code path
    that calls `fetch_and_store_odds`, and went unfetched from 2026-05-24
    until this was noticed.
    """
    return [s for s in active_sports if s not in ESPN_TEAM_SPORTS]


def slate_sports(session, sports, target_date) -> list[str]:
    """Of `sports`, the ones that actually have a game on `target_date`.

    Every odds fetch costs an API credit, and asking about a sport with
    nothing on the card buys nothing. Order follows `sports` rather than the
    database so a budget-exhausted run stops at the same place every day
    instead of starving a different sport each time.
    """
    with_games = {row[0] for row in
                  session.query(Game.sport)
                  .filter(Game.sport.in_(list(sports)),
                          Game.date == target_date,
                          Game.status == "scheduled")
                  .distinct().all()}
    return [s for s in sports if s in with_games]


#: Stat type under which a start's pitcher skill score is stored.
#:
#: Written here rather than by `pipeline.team_stats.compute_team_stats`,
#: which derives its stats from completed games. This one is only knowable
#: BEFORE the game, from the probable-pitcher feed, so it is recorded at the
#: moment a pick is priced on it. Deliberately NOT added to
#: COMPUTED_STAT_TYPES: that tuple is the vocabulary `compute_team_stats`
#: emits, and this is not one of them.
#:
#: Why it exists at all: the pitcher term shipped on 2026-09-23 shifts every
#: MLB probability, and until these rows exist its effect cannot be measured
#: -- the score was fetched, used, and thrown away, so no past pick can be
#: re-scored against the starter it was priced on.
PITCHER_STAT_TYPE = "pitcher_skill_score"


def _persist_pitcher_scores(session, scores: dict[int, dict[str, float]]) -> int:
    """Record each game's two starter scores. Returns rows written.

    Best-effort and never raises: failing to record a measurement input must
    not cost the day's picks, which is what the caller is really doing.
    """
    from backend.models import Game
    from backend.pipeline.team_stats import _upsert_stats

    written = 0
    try:
        for game_id, sides in scores.items():
            game = session.get(Game, game_id)
            if game is None:
                continue
            for side, team_id in (("home", game.home_team_id),
                                  ("away", game.away_team_id)):
                value = sides.get(side)
                if value is None:
                    continue
                written += _upsert_stats(session, game_id, team_id,
                                         {PITCHER_STAT_TYPE: float(value)})
        session.commit()
    except Exception:
        logger.exception("Could not persist pitcher scores; picks continue")
        try:
            session.rollback()
        except Exception:
            # A session that could not commit may also be unable to roll
            # back (e.g. a test double with neither method) -- the contract
            # is "never raises", not "never leaves the session dirty".
            pass
    return written


def mlb_pitcher_scores(session, today) -> dict[int, dict[str, float]] | None:
    """Today's probable-pitcher skill scores keyed by MLB game id, or None.

    The one place the pitcher fetch is wired, so the morning slate and the
    pre-game window price the same starters. Until this existed only the
    window fetched them, and the 11 ET digest -- which reads the morning
    picks -- went out priced on a neutral starter every day.

    None on any failure, logged: a dead MLB Stats API must not cost the
    odds fetch or the picks, only the pitcher term.
    """
    try:
        scores_by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
        scores = _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
        logger.info("MLB pitcher scores: %d game(s), %d stat row(s) recorded",
                    len(scores), _persist_pitcher_scores(session, scores))
        return scores
    except Exception as exc:
        logger.warning(
            "MLB pitcher fetch failed (%s); proceeding with neutral pitcher scores", exc)
        return None


def fetch_odds_and_pick(config, engine, sports) -> None:
    """Fetch odds and generate picks for a list of sports, now.

    Two callers, one body. Nothing here was ever windowless-specific -- the
    old name came from its first caller.

    * **Windowless sports** (boxing, mma) have no ESPN schedule, so there is
      nothing to cluster a window around and this is the only thing that
      fetches their odds at all.
    * **The morning slate.** Window jobs fire `LEAD_TIME` before each game so
      the price is fresh, which for evening baseball means picks appear
      around 22:00 UTC -- hours after the 11:00 ET digest has already run and
      found nothing. Pricing the slate in the morning gives the digest
      something to send; the window runs afterwards refresh each pick as the
      market moves, which `generate_and_store_picks` already does for any
      game that has not started.

    Failures are logged rather than raised. A dead odds API must not stop the
    scout from scheduling windows -- that would cost the whole day rather
    than just the morning.
    """
    if not sports:
        return
    api_key = config.get("odds_api_key")
    if not api_key:
        logger.info("No odds_api_key; skipping %s", ", ".join(sports))
        return
    budget = config.get("odds_budget", DEFAULT_BUDGET)
    session = get_session(engine)
    try:
        asyncio.run(fetch_and_store_odds(session, list(sports), api_key,
                                         budget=budget))
        strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True,           # noqa: E712
            StrategyModel.strategy_type == "game",
        ).first()
        if strategy is not None:
            today = et_today()
            # The slate is what the 11 ET digest reads. Without the starters
            # here, every MLB pick in the email priced a neutral pitcher.
            pitcher_scores = (mlb_pitcher_scores(session, today)
                              if "mlb" in sports else None)
            count = generate_and_store_picks(
                session, strategy.id, today, sports=tuple(sports),
                pitcher_scores=pitcher_scores)
            logger.info("Generated %d picks for %s", count, ", ".join(sports))
    except Exception:
        logger.exception("Odds fetch failed for %s", ", ".join(sports))
    finally:
        session.close()


def morning_scout(config, engine, scheduler, is_retry=False):
    session = get_session(engine)
    try:
        # Box scores first: grade_pending_picks can only grade a prop if the
        # player's game_log row for that game already exists. A collector
        # failure must not stop grading the game-level picks, which do not
        # depend on it.
        try:
            collect_box_scores_for_final_games(session)
        except Exception:
            logger.exception("Box score collection failed; grading with what is present")
        # Team possessions, which offensive_rating / defensive_rating / pace
        # are computed from. Separate from the player box scores above
        # because it is resumable on its own key; a game collected for
        # player lines before this existed still needs its team totals.
        # Logged unconditionally: "nothing to collect" is the normal
        # outcome and a silent path is indistinguishable from an unwired one.
        for possession_sport in POSSESSION_SPORTS:
            try:
                box = collect_team_box_scores(session, possession_sport)
                logger.info("%s team box scores: %d games, %d rows, %d already had them",
                            possession_sport, box["games"], box["rows"], box["skipped"])
            except Exception:
                logger.exception("%s team box score collection failed", possession_sport)
        # Before grading, not after: grade_completed_games applies combat
        # Elo and grades a bout's picks in the same pass, so a bout
        # finalized after it would sit a full day waiting for tomorrow's
        # scout. Bounded by the same LOOKBACK_DAYS the scoreboard windows
        # use -- an unmatched bout is unmatchable forever (non-UFC
        # promotions ESPN does not cover), so re-asking about all of them
        # every morning is a permanently growing bill for an answer that
        # cannot change.
        try:
            mma = finalize_stuck_bouts(session, lookback_days=LOOKBACK_DAYS)
            # Logged unconditionally. "Nothing to do" is the NORMAL outcome
            # here -- cards are weekly, the window is days -- so a silent
            # no-op would be what this prints almost every morning, and it
            # would read exactly like the call having been deleted.
            logger.info("mma ESPN finalize: %d finalized over %d date(s)",
                        mma["finalized"], mma["dates"])
        except Exception:
            logger.exception(
                "mma finalization failed; grading with what is present")
        # The odds feed is the only source for boxing -- ESPN has no boxing
        # sport at all, so 140 rows had never been finalized -- and it
        # covers the regional mma promotions the UFC scoreboard misses,
        # which was 51% of our bouts. It reaches back 3 days only, so it
        # complements the ESPN pass above rather than replacing it.
        for combat_sport in COMBAT_SPORTS:
            try:
                scored = finalize_from_scores(
                    session, combat_sport, config.get("odds_api_key"))
                # Unconditional, for the same reason as above: zero is the
                # expected answer most days and must still be visible.
                logger.info(
                    "%s odds finalize: %d finalized, %d considered, "
                    "%d unmatched%s", combat_sport, scored["finalized"],
                    scored["considered"], scored["unmatched"],
                    " (no api key)" if scored["skipped_no_key"] else "")
            except Exception:
                logger.exception(
                    "%s odds-feed finalization failed; grading with what is "
                    "present", combat_sport)
        grade_pending_picks(session)
        grade_completed_games(session)
        active_sports = [s for s in ALL_SPORTS if is_sport_in_season(s, config["seasons"])]
        scheduled_sports = [s for s in active_sports if s in ESPN_TEAM_SPORTS]
        if not scheduled_sports:
            logger.info("No auto-scheduled sports in season today")
            return
        today = et_today()
        # Today's pass reconciles: a postponed game must drop out of Today's
        # Picks. The lookback days are finalize-only -- their job is to capture
        # scores for games that had not been played when this ran yesterday.
        # Without them nothing ever asks ESPN about a past date and a score
        # never lands; 570 rows were stuck that way on 2026-09-17.
        windows = [(today, True)] + [
            (today - timedelta(days=d), False)
            for d in range(1, LOOKBACK_DAYS + 1)
        ]
        try:
            for day, reconcile in windows:
                # Re-evaluated per day: a lookback can cross a season boundary,
                # and asking about a sport that was out of season that day is a
                # wasted request.
                day_sports = [s for s in scheduled_sports
                              if is_sport_in_season(s, config["seasons"], today=day)]
                if not day_sports:
                    continue
                asyncio.run(fetch_and_store_games(
                    session, day_sports, day, reconcile=reconcile))
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

        # Sports with no ESPN schedule never get a window, so this is the
        # only thing that fetches their odds at all.
        fetch_odds_and_pick(config, engine, windowless_sports(active_sports))

        # The morning slate. Window jobs price each game two hours before it
        # starts, so on an evening card nothing exists when the 11:00 ET
        # digest runs -- it reported "empty; nothing sent" on 2026-09-21 and
        # 2026-09-22 while picks for both days were written that evening.
        # Restricted to sports with a game today so no credit is spent asking
        # about an empty card.
        slate = slate_sports(session, scheduled_sports, today)
        # Logged unconditionally, including the empty case. An empty slate is
        # a normal answer on a quiet Tuesday, and a silent one would read
        # exactly like this call having been deleted -- which is the state
        # the windowed sports were actually in.
        logger.info("Morning slate for %s: %s", today,
                    ", ".join(slate) if slate else "nothing scheduled")
        fetch_odds_and_pick(config, engine, slate)

        existing_jobs = {j.id for j in scheduler.get_jobs()}
        # Windows already due are collected here and run after every sport has
        # been scheduled, not inline. Running one inline blocks the loop for as
        # long as that pipeline takes -- on 2026-09-19 a ncaaf window held it
        # for over 25 minutes fetching player stats, so mlb, later in the list,
        # never had a window created at all and went the day without odds.
        due_now: list[tuple[str, dict, str]] = []
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

            # If an earlier run today (8/9/10 AM retries) clustered a
            # different number/order of windows, drop any window job for
            # this sport/day that the fresh clustering no longer produces —
            # otherwise it's orphaned (never fires, never gets cleaned up).
            window_prefix = f"window_{sport}_{today}_"
            fresh_job_ids = {f"{window_prefix}{i}" for i in range(len(windows))}
            stale_job_ids = {j for j in existing_jobs
                              if j.startswith(window_prefix) and j not in fresh_job_ids}
            for stale_id in stale_job_ids:
                try:
                    scheduler.remove_job(stale_id)
                    logger.info(f"Removed stale window job {stale_id} (recluster shifted windows)")
                except Exception:
                    pass

            for i, window in enumerate(windows):
                job_id = f"window_{sport}_{today}_{i}"
                if job_id in existing_jobs:
                    continue
                run_at = window["run_at"]
                now_utc = datetime.now(tz=timezone.utc)
                if run_at <= now_utc:
                    logger.info(f"Window {job_id} run_at is past, queued to run "
                                "after scheduling completes")
                    due_now.append((sport, window, job_id))
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

        # Still serialised, deliberately: these share a database and hit the
        # same ESPN endpoints, so running them concurrently would trade one
        # problem for another. The point is only that scheduling no longer
        # waits on them.
        for sport, window, job_id in due_now:
            logger.info(f"Running overdue window {job_id}")
            try:
                _run_window(config, engine, sport, window)
            except Exception:
                logger.exception(
                    "Overdue window %s failed; continuing with the rest", job_id)
    except Exception as e:
        logger.error(f"Morning scout error: {e}", exc_info=True)
    finally:
        session.close()


def _run_window(config, engine, sport: str, window: dict):
    session = get_session(engine)
    try:
        today = et_today()
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
            pitcher_scores = mlb_pitcher_scores(session, today) if sport == "mlb" else None
            count = generate_and_store_picks(session, game_strategy.id, today, pitcher_scores=pitcher_scores)
            logger.info(f"Generated {count} game picks")
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.is_active == True, StrategyModel.strategy_type == "prop",
        ).first()
        try:
            # Scoped to this window's sport. Unscoped, each window
            # collected player stats for every sport playing that day:
            # three windows on 2026-09-20 fetched every NFL roster three
            # times, and ESPN throttles with 403 once asked quickly enough.
            result = asyncio.run(run_prop_pipeline(
                session, target_date=today,
                strategy_id=prop_strategy.id if prop_strategy else None,
                sports=(sport,),
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


def grade_pending_picks(session) -> dict:
    """Grade every pick whose game has finished. Returns what it did.

    The counts are returned rather than only logged because the nightly job
    reports them: it used to print "graded: 0" beside a log line saying 122
    picks had just been graded, because this function computed both numbers
    and threw them away.
    """
    ungraded = (
        session.query(PickModel, Game)
        .join(Game, PickModel.game_id == Game.id)
        .filter(Game.status == "final")
        .filter(~PickModel.id.in_(session.query(PickResult.pick_id)))
        .all()
    )
    skipped = 0
    for pick, game in ungraded:
        if game.home_score is not None and game.away_score is not None:
            if pick.pick_type == "prop" and pick.prop_player and pick.prop_market:
                # Props are graded against the player's box score, not the
                # final score. Mirrors the PaperPick loop below, which has
                # always branched this way.
                player_stat = (
                    session.query(PlayerStat)
                    .filter_by(player_name=pick.prop_player,
                               stat_type="game_log", game_date=game.date)
                    .first()
                )
                prop_outcome = grade_prop_pick(pick.pick_value, pick.prop_market,
                                               player_stat)
                if prop_outcome is None:
                    # No box score yet, or a market we cannot map to a stat.
                    skipped += 1
                    continue
                result = prop_outcome[0]
                # grade_prop_pick never sees the odds and returns a flat 1.0
                # for any winner. Price it from the pick's own odds instead,
                # or a -200 winner books +1.00 units instead of +0.50.
                payout = payout_for(result, pick.odds_at_pick or -110)
            else:
                grade_outcome = grade_pick(pick.pick_type, pick.pick_value,
                    game.home_score, game.away_score, pick.odds_at_pick or -110)
                if grade_outcome is None:
                    # grade_pick has no branch for this type. Leave it
                    # ungraded rather than record an invented result.
                    skipped += 1
                    continue
                result, payout = grade_outcome
            pick_result = PickResult(pick_id=pick.id, result=result, payout=payout)
            capture_closing_odds(
                session, pick_result, game.id,
                pick.pick_type, pick.pick_value, pick.odds_at_pick,
            )
            session.add(pick_result)
    session.commit()
    logger.info("Graded %d strategy picks (%d skipped as ungradeable here)",
                len(ungraded) - skipped, skipped)

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
            grade_outcome = grade_pick(
                pick.pick_type, pick.pick_value,
                game.home_score, game.away_score, pick.odds
            )
            if grade_outcome is None:
                continue
            pick.result = grade_outcome[0]

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
    return {"strategy": len(ungraded) - skipped, "skipped": skipped,
            "paper": paper_graded}


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
    skipped = 0
    try:
        games = await collector.fetch_schedule(target_date)
        for g in games:
            # Identity first: a game whose teams do not resolve is skipped,
            # so fetching its two pitchers' season logs first spent two
            # requests to throw the answer away. While the abbreviations were
            # all None that was 30 wasted calls a day.
            home_abbr = g.get("home_team")
            away_abbr = g.get("away_team")
            if home_abbr is None or away_abbr is None:
                skipped += 1
                continue
            home_id = g.get("home_probable_pitcher_id")
            away_id = g.get("away_probable_pitcher_id")
            home_stats = await collector.fetch_pitcher_recent(home_id, season=target_date.year) if home_id else None
            away_stats = await collector.fetch_pitcher_recent(away_id, season=target_date.year) if away_id else None
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
    if skipped:
        # Returning {} here used to be indistinguishable from "no MLB games
        # today". It is not: every pick then prices a neutral starter, which
        # is the dominant MLB feature quietly switched off.
        logger.warning(
            "MLB pitcher scores: %d of %d games had an unidentifiable team "
            "and were skipped", skipped, len(games) if games else 0)
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

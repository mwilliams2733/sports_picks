"""Fetch odds and generate game picks now, without the player-stats pass.

Why this exists
---------------
`_run_window` does two jobs back to back that have nothing in common but a
sport. Storing odds takes about ten seconds and is **perishable** -- prices
move, and a price fetched after the game starts is worth nothing. Collecting
player stats takes 25+ minutes and thousands of ESPN requests, and is not
time-sensitive at all.

Because they are welded together and the scheduler runs due windows
serially, a time-critical mlb odds fetch can sit behind five ncaaf stats
collections. On 2026-09-19 that left 15 mlb games with zero odds while
ncaaf rosters were being downloaded.

This runs only the perishable half. Props are opt-in, off by default --
turning them on gives back exactly the behaviour this script exists to
avoid, and is there only so the script can stand in for a full window when
that is what you want.

Everything is delegated to the same functions `_run_window` calls, so the
two cannot drift: `fetch_and_store_odds`, `generate_and_store_picks`, and
the same pitcher-score remap.

    python -m backend.scripts.fetch_odds_now --db <abs path> --sport mlb
    python -m backend.scripts.fetch_odds_now --db <abs path>   # in-season
"""
import argparse
import asyncio
import logging
import os
from datetime import date

from backend.collectors.budget import DEFAULT_BUDGET, get_credit_summary
from backend.config import is_sport_in_season, load_config
from backend.database import get_engine, get_session, run_migrations
from backend.models import StrategyModel
from backend.pipeline.full_pipeline import (
    fetch_and_store_odds,
    fetch_and_store_props,
)
from backend.pipeline.pick_generator import generate_and_store_picks
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.pipeline.scheduler import (
    ALL_SPORTS,
    _remap_pitcher_scores_to_game_ids,
    fetch_pitcher_scores_for_date,
)

logger = logging.getLogger(__name__)


def in_season_sports(config: dict) -> list[str]:
    return [s for s in ALL_SPORTS
            if is_sport_in_season(s, config.get("seasons", {}))]


def run(db_path: str, *, sports=None, with_picks: bool = True,
        with_props: bool = False, config_path: str = "config.yaml") -> dict:
    """Fetch odds (and optionally picks/props) for today. Returns a summary.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo."
        )

    config = load_config(config_path)
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)

    today = date.today()
    budget = config.get("odds_budget", DEFAULT_BUDGET)
    api_key = config.get("odds_api_key")
    target = list(sports) if sports else in_season_sports(config)

    summary = {"sports": target, "odds": 0, "props": 0, "picks": 0,
               "prop_picks": 0}
    try:
        if not api_key:
            # Not an error: the pipeline is expected to run without odds when
            # no key is configured, and saying so beats a silent zero.
            logger.warning("No odds_api_key configured; skipping the odds fetch")
        else:
            summary["odds"] = asyncio.run(
                fetch_and_store_odds(session, target, api_key, budget=budget)) or 0
            if with_props:
                summary["props"] = asyncio.run(
                    fetch_and_store_props(session, target, api_key,
                                          budget=budget)) or 0

        if with_picks:
            strategy = session.query(StrategyModel).filter(
                StrategyModel.is_active == True,          # noqa: E712
                StrategyModel.strategy_type == "game",
            ).first()
            if strategy is None:
                logger.warning("No active game strategy; no picks generated")
            else:
                pitcher_scores = None
                if "mlb" in target:
                    # Same treatment as _run_window: a failure here must not
                    # cost the picks, it just prices them on a neutral
                    # starter.
                    try:
                        by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
                        pitcher_scores = _remap_pitcher_scores_to_game_ids(
                            session, by_abbr, today)
                    except Exception as exc:
                        logger.warning(
                            "MLB pitcher fetch failed (%s); proceeding with "
                            "neutral pitcher scores", exc)
                summary["picks"] = generate_and_store_picks(
                    session, strategy.id, today,
                    pitcher_scores=pitcher_scores) or 0

        if with_props:
            prop_strategy = session.query(StrategyModel).filter(
                StrategyModel.is_active == True,          # noqa: E712
                StrategyModel.strategy_type == "prop",
            ).first()
            try:
                result = asyncio.run(run_prop_pipeline(
                    session, target_date=today,
                    strategy_id=prop_strategy.id if prop_strategy else None))
                summary["prop_picks"] = (result or {}).get("picks_generated", 0)
            except Exception as exc:
                logger.error("Prop pipeline failed: %s", exc)

        summary["credits"] = get_credit_summary(session, budget)
    finally:
        session.close()
    return summary


def format_summary(s: dict) -> str:
    lines = ["Odds run complete", ""]
    lines.append(f"  sports      : {', '.join(s['sports']) or '(none in season)'}")
    lines.append(f"  odds stored : {s['odds']}")
    lines.append(f"  game picks  : {s['picks']}")
    if s.get("props") or s.get("prop_picks"):
        lines.append(f"  props       : {s['props']} ({s['prop_picks']} picks)")
    else:
        lines.append("  props       : skipped (pass --props to include them)")
    c = s.get("credits")
    if c:
        lines.append(f"  credits     : {c['daily_used']} today, "
                     f"{c['monthly_used']}/{c['monthly_limit']} this month")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Fetch odds and generate game picks now, skipping the "
                    "25-minute player-stats pass a full window would do.")
    ap.add_argument("--db", required=True, help="Path to the database.")
    ap.add_argument("--sport", action="append", dest="sports",
                    help="Limit to a sport. Repeatable. Defaults to in-season.")
    ap.add_argument("--props", action="store_true",
                    help="Also run the slow prop/player-stats pipeline.")
    ap.add_argument("--no-picks", action="store_true",
                    help="Store odds only; generate no picks.")
    args = ap.parse_args(argv)

    print(format_summary(run(
        args.db,
        sports=tuple(args.sports) if args.sports else None,
        with_picks=not args.no_picks,
        with_props=args.props,
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

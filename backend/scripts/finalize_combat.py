"""Finalize combat bouts from The Odds API's `/scores` endpoint.

Why this exists
---------------
`finalize_mma` reads ESPN's UFC scoreboard, which cannot serve either
sport completely:

* **boxing is not in ESPN's API at all.** `sports/boxing` 404s and the API
  answers that the sport and league are invalid. 140 boxing rows had never
  been finalized -- no scores, no `elo_history`, every pick priced on two
  identical 1500 seeds.
* **mma is only partly covered.** 64 of 126 bouts (51%) never matched,
  because the odds feed carries regional promotions the UFC scoreboard
  does not list.

The odds feed covers both, and its fighter names are the strings we
already stored -- our rows were created from these events. On 2026-09-20,
30 of 30 upcoming boxing pairs matched character for character.

This does not replace `finalize_mma`
------------------------------------
`/scores` is capped at 3 days, so it can only ever finalize a *recent*
bout. `finalize_mma` remains the tool for an older ESPN-covered one, and
the 110 stuck boxing bouts from March to July are reachable by neither --
they need an archive this repository does not have.

Matching is exact or nothing, and orientation is by name
--------------------------------------------------------
Our `home_team` is not the API's `home_team`. The winner is resolved by
name, never by corner: writing it backwards grades a real pick against the
wrong fighter and feeds a reversed Elo update, and both look entirely
normal afterwards.

    python -m backend.scripts.finalize_combat --db <abs path> --dry-run
    python -m backend.scripts.finalize_combat --db <abs path> --sport boxing
"""
import argparse
import asyncio
import logging
import os
from datetime import date, timedelta

import httpx

from backend.collectors.odds_scores import MAX_DAYS_FROM, fetch_scores
from backend.collectors.ufc import match_bout, winner_is
from backend.config import load_config
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Team
from backend.time_utils import et_today

logger = logging.getLogger(__name__)

#: The sports this can serve. Both are combat sports whose rows come from
#: the odds feed rather than an ESPN scoreboard.
COMBAT_SPORTS = ("mma", "boxing")


def stuck_combat(session, sport: str, today: date,
                 *, lookback_days: int = MAX_DAYS_FROM) -> list[Game]:
    """Scheduled bouts of one sport inside the window /scores can answer for.

    Bounded by `MAX_DAYS_FROM` because the endpoint physically cannot return
    an older bout: including one would add work that can only ever come back
    unmatched.
    """
    return (session.query(Game)
            .filter(Game.sport == sport, Game.status == "scheduled",
                    Game.date <= today,
                    Game.date >= today - timedelta(days=lookback_days))
            .order_by(Game.date.asc()).all())


async def _finalize(session, sport: str, api_key: str, today: date,
                    *, dry_run: bool) -> dict:
    summary = {"sport": sport, "considered": 0, "finalized": 0,
               "unmatched": 0, "skipped_no_key": False}
    games = stuck_combat(session, sport, today)
    if not games:
        return summary

    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == sport)}
    async with httpx.AsyncClient(timeout=30.0) as client:
        bouts = await fetch_scores(client, api_key, sport)

    for game in games:
        summary["considered"] += 1
        home = names.get(game.home_team_id, "")
        away = names.get(game.away_team_id, "")
        bout = match_bout(bouts, home, away)
        if bout is None:
            summary["unmatched"] += 1
            continue
        # By name, never by corner: our home fighter may be the API's away
        # fighter, and a reversed result is invisible after the fact.
        home_won = winner_is(bout, home)
        if not dry_run:
            game.home_score = 1 if home_won else 0
            game.away_score = 0 if home_won else 1
            game.status = "final"
        summary["finalized"] += 1

    if not dry_run and summary["finalized"]:
        session.commit()
    return summary


def finalize_from_scores(session, sport: str, api_key: str | None,
                         today: date | None = None, *,
                         dry_run: bool = False) -> dict:
    """Finalize recent stuck bouts of one sport. Returns a summary.

    A missing key is a clean no-op, not a 401: the pipeline is expected to
    run without one, and a traceback there would be noise rather than news.
    """
    today = today or et_today()
    if not api_key:
        logger.warning("No odds_api_key configured; %s finalization skipped",
                       sport)
        return {"sport": sport, "considered": 0, "finalized": 0,
                "unmatched": 0, "skipped_no_key": True}
    return asyncio.run(_finalize(session, sport, api_key, today,
                                 dry_run=dry_run))


def run(db_path: str, *, sports=COMBAT_SPORTS, dry_run: bool = False,
        today: date | None = None, config_path: str = "config.yaml") -> dict:
    """CLI entry point: finalize every combat sport against the odds feed.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")

    api_key = load_config(config_path).get("odds_api_key")
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return {"sports": [
            finalize_from_scores(session, sport, api_key, today,
                                 dry_run=dry_run)
            for sport in sports]}
    finally:
        session.close()


def format_summary(s: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Combat finalized", ""]
    for row in s["sports"]:
        lines.append(f"  {row['sport']}")
        if row["skipped_no_key"]:
            lines.append("    skipped: no odds_api_key configured")
            continue
        lines.append(f"    considered : {row['considered']}")
        lines.append(f"    {'would finalize' if dry_run else 'finalized':<10} : "
                     f"{row['finalized']}")
        lines.append(f"    unmatched  : {row['unmatched']}")
    lines.append("")
    lines.append("  /scores reaches back 3 days only. An older bout needs")
    lines.append("  finalize_mma (mma, ESPN) or an archive this repo lacks")
    lines.append("  (boxing).")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Finalize recent combat bouts from The Odds API scores.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport", action="append", dest="sports",
                    choices=COMBAT_SPORTS,
                    help="Limit to a sport. Repeatable. Defaults to both.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(format_summary(
        run(args.db, sports=tuple(args.sports) if args.sports else COMBAT_SPORTS,
            dry_run=args.dry_run), args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

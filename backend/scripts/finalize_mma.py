"""Finalize mma bouts from ESPN's UFC scoreboard.

Why this exists
---------------
Nothing ever finalized a combat game. `fetch_ufc_events` was named by two
comments as the mechanism and never written, and those same comments
justified excluding mma and boxing from every other finalization path. On
2026-09-20 that left 293 combat games all `scheduled`, no scores, no
elo_history, no fighter ratings, and 85 picks that could never be graded.

This closes the loop for mma: fetch each date that has a stuck bout, match
our rows to ESPN's bouts by fighter pair, and write the binary result the
grader expects (home_score=1/away_score=0, or reversed).

Boxing is not served. ESPN has no boxing scoreboard, so its 110 stuck rows
need a source this repository does not have. They are reported, not guessed.

Matching is exact or nothing
----------------------------
A bout is finalized only when both fighters match, in either corner order,
after normalising case, accents and punctuation. A wrong winner is the one
failure nothing downstream can notice: it grades a real pick backwards and
feeds a reversed Elo update, and both look entirely normal afterwards.

Dates come from the database, so only dates with a stuck bout are requested.

    python -m backend.scripts.finalize_mma --db <abs path> --dry-run
    python -m backend.scripts.finalize_mma --db <abs path>
"""
import argparse
import asyncio
import logging
import os
from collections import defaultdict
from datetime import date, timedelta

import httpx

from backend.collectors.ufc import fetch_ufc_events, match_bout, winner_is
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Team
from backend.time_utils import et_today

logger = logging.getLogger(__name__)


def stuck_bouts(session, today: date, *,
                since: date | None = None) -> dict[date, list[Game]]:
    """Scheduled mma games whose date has passed, grouped by date.

    ``since`` is a date floor. It exists because an unmatched bout stays
    stuck forever: the odds feed carries promotions ESPN's UFC scoreboard
    does not cover, and on 2026-09-20 that was 64 bouts across 18 distinct
    dates reaching back to 2026-03-16. Without a floor, a daily caller pays
    one request per such date every morning for an answer that cannot
    change, and the set only grows. Unbounded by default, because the
    manual catch-up run wants exactly that.
    """
    q = (session.query(Game)
         .filter(Game.sport == "mma", Game.status == "scheduled",
                 Game.date < today))
    if since is not None:
        q = q.filter(Game.date >= since)
    rows = q.order_by(Game.date.asc()).all()
    out: dict[date, list[Game]] = defaultdict(list)
    for g in rows:
        out[g.date].append(g)
    return dict(out)


async def _finalize(session, games_by_date, *, dry_run: bool) -> dict:
    summary = {"dates": len(games_by_date), "considered": 0, "finalized": 0,
               "unmatched": 0, "requests": 0, "failed_dates": []}
    names = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.sport == "mma")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for day, games in sorted(games_by_date.items()):
            try:
                bouts = await fetch_ufc_events(client, day)
                summary["requests"] += 1
            except Exception as exc:
                summary["failed_dates"].append((day.isoformat(),
                                                f"{type(exc).__name__}: {exc}"))
                logger.warning("mma %s: fetch failed: %s", day, exc)
                continue
            for game in games:
                summary["considered"] += 1
                home = names.get(game.home_team_id, "")
                away = names.get(game.away_team_id, "")
                bout = match_bout(bouts, home, away)
                if bout is None:
                    summary["unmatched"] += 1
                    continue
                home_won = winner_is(bout, home)
                if not dry_run:
                    game.home_score = 1 if home_won else 0
                    game.away_score = 0 if home_won else 1
                    game.status = "final"
                summary["finalized"] += 1
    if not dry_run:
        session.commit()
    return summary


def finalize_stuck_bouts(session, today: date | None = None, *,
                         lookback_days: int | None = None,
                         dry_run: bool = False) -> dict:
    """Finalize matchable stuck bouts on an open session. Returns a summary.

    The session-level entry point, so a caller that already holds one (the
    morning scout) shares its transaction instead of opening a second
    connection to the same database. ``run`` is this plus the engine setup,
    so the scheduled path and the CLI cannot drift.

    ``lookback_days`` bounds how far back dates are requested; ``None``
    means all history. See `stuck_bouts`.
    """
    today = today or et_today()
    since = today - timedelta(days=lookback_days) if lookback_days else None
    return asyncio.run(_finalize(
        session, stuck_bouts(session, today, since=since), dry_run=dry_run))


def run(db_path: str, *, dry_run: bool = False, today: date | None = None,
        lookback_days: int | None = None) -> dict:
    """Finalize every matchable stuck mma bout. Returns a summary.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return finalize_stuck_bouts(session, today, dry_run=dry_run,
                                    lookback_days=lookback_days)
    finally:
        session.close()


def format_summary(s: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "mma finalized", ""]
    lines.append(f"  dates with a stuck bout : {s['dates']}")
    lines.append(f"  requests made           : {s['requests']}")
    lines.append(f"  bouts considered        : {s['considered']}")
    lines.append(f"  {'would finalize' if dry_run else 'finalized':<23} : {s['finalized']}")
    lines.append(f"  unmatched (left alone)  : {s['unmatched']}")
    if s["failed_dates"]:
        lines.append("")
        lines.append(f"  {len(s['failed_dates'])} date(s) FAILED and were skipped:")
        for day, err in s["failed_dates"][:10]:
            lines.append(f"    {day}  {err}")
    lines.append("")
    lines.append("  Unmatched bouts are not errors: the odds feed carries")
    lines.append("  promotions ESPN's UFC scoreboard does not cover. They stay")
    lines.append("  scheduled rather than being guessed at.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Finalize stuck mma bouts from ESPN's UFC scoreboard.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(format_summary(run(args.db, dry_run=args.dry_run), args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

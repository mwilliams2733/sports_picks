"""One-off catch-up for games that were played but never marked final.

Why this exists
---------------
``morning_scout`` asked ESPN about ``today`` only, at 8/9/10am ET -- before
that day's games had been played -- and nothing ever revisited a past date. So
a score never landed, and 570 games with past dates sat ``scheduled``
indefinitely. Plan 013 gave the daily path a 3-day lookback, which fixes the
steady state but cannot reach back months.

This script does that one pass, **through the same code**
(``full_pipeline.fetch_and_store_games``) rather than a parallel
implementation with its own semantics.

Always finalize-only
--------------------
Every call passes ``reconcile=False``. ``_reconcile_against_espn`` matches on
an unordered team-id pair, so abbreviation drift or a duplicate team row makes
a real game look absent -- and on a months-old date the row would be marked
``canceled`` instead of ``final``. That converts a matching failure into data
loss on exactly the rows this script exists to rescue.

Cheap by construction
---------------------
The date list is derived from the database, not from a calendar: only dates
that actually have a stuck game are requested. That is tens of requests rather
than (months x sports).

Out of scope
------------
``mma`` and ``boxing``. They have 236 stuck rows between them, but their finals
come from ``fetch_ufc_events`` and their Elo from ``grade_completed_games``
with a post-game convention. Routing them through the ESPN scoreboard would
create a second writer for the same rows.

Safety
------
``--db`` is **required**. There is no default, so this cannot hit
``sports_picks.db`` by accident. Use a copy first.

Usage::

    python -m backend.scripts.catch_up_finals --db /path/to/copy.db --dry-run
    python -m backend.scripts.catch_up_finals --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func

from backend.database import get_engine, get_session
from backend.models import Game
from backend.pipeline.full_pipeline import fetch_and_store_games
from backend.pipeline.scheduler import ESPN_TEAM_SPORTS


def stuck_dates(session, today: date, sports: tuple[str, ...] = ESPN_TEAM_SPORTS,
                since: date | None = None) -> list[tuple[str, date]]:
    """``(sport, date)`` pairs that have at least one non-final past game.

    Derived from the data so only dates that need a request get one.
    """
    query = (
        session.query(Game.sport, Game.date)
        .filter(Game.status != "final",
                Game.date < today,
                Game.sport.in_(sports))
    )
    if since is not None:
        query = query.filter(Game.date >= since)
    return sorted({(sport, d) for sport, d in query.distinct().all()},
                  key=lambda pair: (pair[1], pair[0]))


def _counts_by_sport(session, today: date, sports) -> dict[str, int]:
    rows = (session.query(Game.sport, func.count(Game.id))
            .filter(Game.status != "final", Game.date < today,
                    Game.sport.in_(sports))
            .group_by(Game.sport).all())
    return {sport: n for sport, n in rows}


def run(db_path: str, *, dry_run: bool = False, sports=ESPN_TEAM_SPORTS,
        since: date | None = None, today: date | None = None) -> dict:
    """Catch up ``db_path``. Returns a summary dict.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist. Without this
    check the engine would create an empty database at a typo'd path and the
    run would report a successful zero-game catch-up against it -- the most
    misleading possible outcome for a script whose whole job is to repair an
    existing database.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-game 'successful' catch-up would hide the typo."
        )

    today = today or date.today()
    session = get_session(get_engine(db_path))
    try:
        before = _counts_by_sport(session, today, sports)
        pairs = stuck_dates(session, today, sports, since)

        requested = 0
        if not dry_run:
            for sport, day in pairs:
                # Ask about the day and its neighbours. A row stored under the
                # UTC date of an evening game (plan 014) sits a day after the
                # date ESPN files the event under, so requesting only the
                # stored date never returns it -- that is how game 1603 stayed
                # non-final with 48 props attached. _store_games matches on
                # espn_id, so the neighbouring day's response still finds the
                # right row and corrects its date.
                for delta in (0, -1, 1):
                    asyncio.run(fetch_and_store_games(
                        session, [sport], day + timedelta(days=delta),
                        reconcile=False))
                    requested += 1

        after = before if dry_run else _counts_by_sport(session, today, sports)
        canceled = dict(Counter(
            sport for (sport,) in session.query(Game.sport)
            .filter(Game.status == "canceled", Game.date < today).all()))
        session.commit()
    finally:
        session.close()

    return {"dates": len(pairs), "requests": requested,
            "stuck_before": before, "stuck_after": after,
            "canceled_rows": canceled,
            "sports": list(sports)}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Catch-up complete", ""]
    lines.append(f"  (sport, date) pairs needing a request: {summary['dates']}")
    lines.append(f"  requests made                        : {summary['requests']}")
    lines.append("")
    lines.append("  sport    stuck before   stuck after   finalized")
    lines.append("  " + "-" * 48)
    for sport in summary["sports"]:
        before = summary["stuck_before"].get(sport, 0)
        after = summary["stuck_after"].get(sport, 0)
        if before == 0:
            continue
        lines.append(f"  {sport:<8} {before:12d} {after:13d} {before - after:11d}")
    if summary["canceled_rows"]:
        lines.append("")
        lines.append("  !! rows now 'canceled' (this script never sets that):")
        for sport, n in sorted(summary["canceled_rows"].items()):
            lines.append(f"     {sport}: {n}")
        lines.append("     Pre-existing, or a reconcile ran. Investigate before")
        lines.append("     trusting this run.")
    lines.append("")
    lines.append("  mma / boxing are out of scope: their finals come from")
    lines.append("  fetch_ufc_events, not the ESPN scoreboard.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Finalize past games that were played but never marked final.")
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default, so this "
             "cannot hit production by accident. Use a copy first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be requested, write nothing.")
    parser.add_argument("--sport", action="append", dest="sports",
                        help="Limit to a sport (repeatable).")
    parser.add_argument("--since", default=None,
                        help="Only dates on or after this YYYY-MM-DD.")
    args = parser.parse_args(argv)

    sports = tuple(args.sports) if args.sports else ESPN_TEAM_SPORTS
    since = date.fromisoformat(args.since) if args.since else None

    summary = run(args.db, dry_run=args.dry_run, sports=sports, since=since)
    print(format_summary(summary, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

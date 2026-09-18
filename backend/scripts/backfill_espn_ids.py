"""One-off backfill of ``games.espn_id``.

Every row predates the column, so the daily path can only fill it in as games
are seen again. This asks ESPN once per date that has a row missing an id.

What it writes
--------------
``games.espn_id``, and nothing else.

What it must never do
---------------------
Change a date, change a status, change a score, or create a row. It is a pure
identity backfill. Plan 014 Task 2 changes dates, and it depends on this having
run first -- flipping the date convention while rows have no stable id creates
a twin for every evening game.

Matching
--------
A row is matched to an ESPN event by ``(home_team_id, away_team_id)`` within
the requested date, which is the same key the pre-``espn_id`` upsert used. Two
rows for the same pair on the same date are therefore **ambiguous** and are
left alone rather than guessed at -- production has two such groups.

Safety
------
``--db`` is **required**. There is no default, so this cannot hit
``sports_picks.db`` by accident. Use a copy first.

Usage::

    python -m backend.scripts.backfill_espn_ids --db /path/to/copy.db --dry-run
    python -m backend.scripts.backfill_espn_ids --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date, timedelta

from backend.collectors.espn import ESPNCollector
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Team
from backend.pipeline.scheduler import ESPN_TEAM_SPORTS


def dates_missing_ids(session, sports=ESPN_TEAM_SPORTS) -> list[tuple[str, date]]:
    """``(sport, date)`` pairs that have at least one row without an espn_id."""
    rows = (session.query(Game.sport, Game.date)
            .filter(Game.espn_id.is_(None), Game.sport.in_(sports))
            .distinct().all())
    return sorted({(sport, d) for sport, d in rows}, key=lambda p: (p[1], p[0]))


async def _fill_one_date(session, collector, sport: str, day: date) -> dict:
    """Fill espn_id for `sport` rows on `day`. Returns per-outcome counts.

    ESPN is asked about the day *and* its neighbours, because the stored date
    may be on either side of the UTC/Eastern boundary -- which is the whole
    reason these rows cannot be identified in the first place.
    """
    counts = Counter()
    events: dict[frozenset, str] = {}
    abbr_to_id: dict[str, int] = {
        t.abbreviation: t.id
        for t in session.query(Team).filter(Team.sport == sport).all()
    }

    for delta in (0, -1, 1):
        stamp = (day + timedelta(days=delta)).strftime("%Y%m%d")
        try:
            fetched = await collector.fetch_scoreboard(sport, stamp)
        except Exception:
            counts["fetch_failed"] += 1
            continue
        for ev in fetched:
            h = abbr_to_id.get(ev["home_team"])
            a = abbr_to_id.get(ev["away_team"])
            if h is None or a is None:
                counts["unknown_team"] += 1
                continue
            events.setdefault(frozenset({h, a}), ev["espn_id"])

    rows = (session.query(Game)
            .filter(Game.sport == sport, Game.date == day,
                    Game.espn_id.is_(None)).all())
    by_pair = Counter(frozenset({r.home_team_id, r.away_team_id}) for r in rows)

    for row in rows:
        pair = frozenset({row.home_team_id, row.away_team_id})
        if by_pair[pair] > 1:
            # Two rows, same pair, same date: the pre-espn_id key cannot tell
            # them apart. Guessing would assign one game's identity to another.
            counts["ambiguous"] += 1
            continue
        espn_id = events.get(pair)
        if espn_id is None:
            counts["no_match"] += 1
            continue
        row.espn_id = espn_id
        counts["matched"] += 1
    return counts


def run(db_path: str, *, dry_run: bool = False, sports=ESPN_TEAM_SPORTS) -> dict:
    """Backfill ``db_path``. Returns a summary dict.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    successful zero-row backfill against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' backfill would hide the typo."
        )

    engine = get_engine(db_path)
    # Every target database predates this column, so the migration has to run
    # before the column can be queried. Without this the script dies with
    # "no such column: games.espn_id" against any real copy.
    run_migrations(engine)
    session = get_session(engine)
    per_sport: dict[str, Counter] = {}
    pairs = dates_missing_ids(session, sports)
    try:
        if not dry_run:
            collector = ESPNCollector()

            async def _all():
                for sport, day in pairs:
                    got = await _fill_one_date(session, collector, sport, day)
                    per_sport.setdefault(sport, Counter()).update(got)
                await collector.close()

            asyncio.run(_all())
            session.commit()
    finally:
        session.close()

    return {"dates": len(pairs), "per_sport": {s: dict(c) for s, c in per_sport.items()}}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Backfill complete", ""]
    lines.append(f"  (sport, date) pairs with a missing id: {summary['dates']}")
    if not summary["per_sport"]:
        lines.append("  (no requests made)")
        return "\n".join(lines)
    lines.append("")
    lines.append("  sport    matched  no_match  ambiguous  unknown_team  fetch_failed")
    lines.append("  " + "-" * 68)
    for sport, c in sorted(summary["per_sport"].items()):
        lines.append("  %-8s %7d %9d %10d %13d %13d" % (
            sport, c.get("matched", 0), c.get("no_match", 0),
            c.get("ambiguous", 0), c.get("unknown_team", 0),
            c.get("fetch_failed", 0)))
    lines.append("")
    lines.append("  'ambiguous' = two rows, same teams, same date: the old key")
    lines.append("  cannot tell them apart, so neither was guessed at.")
    lines.append("  'unknown_team' = an ESPN abbreviation we have no team for")
    lines.append("  (expected for ncaab, whose rows hold display names).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill games.espn_id from ESPN's scoreboard. Writes only that column.")
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default, so this "
             "cannot hit production by accident. Use a copy first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the work without making requests or writing.")
    parser.add_argument("--sport", action="append", dest="sports",
                        help="Limit to a sport (repeatable).")
    args = parser.parse_args(argv)

    sports = tuple(args.sports) if args.sports else ESPN_TEAM_SPORTS
    print(format_summary(run(args.db, dry_run=args.dry_run, sports=sports),
                         args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

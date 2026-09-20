"""Backfill the ESPN-sourced game flags: ``neutral_site`` and ``season_type``.

Why this exists
---------------
Both are facts ESPN reports per event and neither is derivable from anything
already stored, so their migrations defaulted every existing row -- a
migration that guessed would be indistinguishable from one that measured.

A game at a neutral venue has no host, so ``home_team_id`` is a bracket or
seed designation rather than a team playing at home.

``season_type`` separates regular season from postseason, preseason and
all-star. It matters because postseason basketball scores far less: the
totals model, fitted on regular-season rates, misses those games by -17.09
points on average.

That default is wrong for a known set of rows. Every ncaab final game in
production falls between 2026-03-14 and 2026-03-22: the First Four, Round 1
and Round 2 of the NCAA tournament, all at neutral sites. Their 0.708 "home
win rate" is higher seeds beating lower seeds, and until this runs the
calibrated model reads it as home-court advantage.

How it matches
--------------
By ``espn_id`` only. Every final game in production carries one, and the id
is exact -- unlike the team/date matching that ``backfill_espn_ids`` needs,
which is why that script has neighbour-date logic and this one does not.
A row ESPN does not list is left alone and counted, never guessed at.

Unlike ``backfill_espn_ids``, ``--dry-run`` here *does* make requests. It
writes nothing, but a dry run that skipped the fetch could only report how
many rows it would look at, which tells you nothing about what would change.
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date, timedelta

from backend.collectors.espn import ESPNCollector
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game
from backend.pipeline.scheduler import ESPN_TEAM_SPORTS


def dates_to_check(session, sports=ESPN_TEAM_SPORTS) -> list[tuple[str, date]]:
    """``(sport, date)`` pairs holding at least one row with an espn_id.

    Every row is checked, not just the ones currently ``False``: ``False`` is
    the column's default as well as a real value, so there is no way to tell
    "not yet known" from "known to be hosted" by looking at the row.
    """
    rows = (session.query(Game.sport, Game.date)
            .filter(Game.espn_id.isnot(None), Game.sport.in_(sports))
            .distinct().all())
    return sorted({(sport, d) for sport, d in rows}, key=lambda p: (p[1], p[0]))


async def _check_one_date(session, collector, sport: str, day: date,
                          *, dry_run: bool) -> Counter:
    counts: Counter = Counter()

    async def scoreboard(d: date) -> dict[str, dict]:
        events = await collector.fetch_scoreboard(sport, d.strftime("%Y%m%d"))
        return {e["espn_id"]: {
            "neutral_site": bool(e.get("neutral_site", False)),
            "season_type": e.get("season_type", "unknown"),
        } for e in events}

    by_id = await scoreboard(day)

    rows = (session.query(Game)
            .filter(Game.sport == sport, Game.date == day,
                    Game.espn_id.isnot(None))
            .all())

    # ESPN files a game under its Eastern date, but a naive parse of the UTC
    # timestamp lands a late game on the following day, so a row can be
    # stored a day off from where ESPN lists it. Neighbours are consulted
    # only for rows the exact date missed, which keeps the common case at one
    # request per date instead of three.
    if any(row.espn_id not in by_id for row in rows):
        for delta in (-1, 1):
            by_id.update(await scoreboard(day + timedelta(days=delta)))
            if all(row.espn_id in by_id for row in rows):
                break

    for row in rows:
        if row.espn_id not in by_id:
            counts["no_match"] += 1
            continue
        want = by_id[row.espn_id]
        changed = False
        if bool(row.neutral_site) != want["neutral_site"]:
            counts["neutral" if want["neutral_site"] else "hosted"] += 1
            changed = True
            if not dry_run:
                row.neutral_site = want["neutral_site"]
        # "unknown" from ESPN is not an answer, so it never overwrites one.
        if (want["season_type"] != "unknown"
                and row.season_type != want["season_type"]):
            counts[f"season:{want['season_type']}"] += 1
            changed = True
            if not dry_run:
                row.season_type = want["season_type"]
        if changed:
            counts["changed"] += 1
        else:
            counts["already_correct"] += 1
            continue
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
    # Target databases predate this column, so the migration has to run before
    # the column can be queried at all.
    run_migrations(engine)
    session = get_session(engine)
    per_sport: dict[str, Counter] = {}
    pairs = dates_to_check(session, sports)
    try:
        collector = ESPNCollector()

        async def _all():
            for sport, day in pairs:
                got = await _check_one_date(session, collector, sport, day,
                                            dry_run=dry_run)
                per_sport.setdefault(sport, Counter()).update(got)
            await collector.close()

        asyncio.run(_all())
        if not dry_run:
            session.commit()
    finally:
        session.close()

    return {"dates": len(pairs),
            "per_sport": {s: dict(c) for s, c in per_sport.items()}}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Backfill complete", ""]
    lines.append(f"  dates checked   : {summary['dates']}")
    if not summary["per_sport"]:
        lines.append("  nothing to check.")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"  {'sport':<8} {'changed':>8} {'->neutral':>10} "
                 f"{'->hosted':>9} {'correct':>8} {'no_match':>9}")
    for sport, c in sorted(summary["per_sport"].items()):
        lines.append(
            f"  {sport:<8} {c.get('changed', 0):>8} {c.get('neutral', 0):>10} "
            f"{c.get('hosted', 0):>9} {c.get('already_correct', 0):>8} "
            f"{c.get('no_match', 0):>9}"
        )
        for key in sorted(k for k in c if k.startswith("season:")):
            lines.append(f"      {key.split(':')[1]:<14} {c[key]:>6}")
    lines.append("")
    lines.append("  no_match rows are left as they are, never guessed at.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill games.neutral_site from ESPN. Matches by espn_id."
    )
    parser.add_argument("--db", required=True,
                        help="Path to the database. Back it up first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and report, but write nothing.")
    parser.add_argument("--sport", action="append", dest="sports",
                        help="Limit to a sport. Repeatable.")
    args = parser.parse_args(argv)
    sports = tuple(args.sports) if args.sports else ESPN_TEAM_SPORTS
    print(format_summary(run(args.db, dry_run=args.dry_run, sports=sports),
                         args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

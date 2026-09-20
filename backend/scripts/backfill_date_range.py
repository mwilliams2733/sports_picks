"""Fetch a past range of dates from ESPN and store the games.

Why this exists
---------------
Game collection began on 2026-09-17, and nothing ever fetched what came
before it. On 2026-09-20 that left nfl without week 1 and ncaaf without
08-24 through 09-16 -- about four weeks of college football. The scheduler
only ever looks at today and a short lookback, so the gap was permanent:
no amount of running the pipeline forward would close it.

Everything is delegated to `fetch_and_store_games`, the same function the
scheduler calls. That is deliberate rather than convenient. The older
`backtesting.historical.store_games` also stores games, but it drops ESPN's
event id and writes neither `neutral_site` nor `season_type` -- so rows it
creates cannot be found by the odds matcher, cannot be recognised when a
game moves date, and read as hosted regular-season games to the model
whatever they actually were. It also never calls `_refresh_team_stats`,
which is the only production producer of TeamStat rows, so its games arrive
with no features at all.

Reconciliation is off
---------------------
`fetch_and_store_games` normally marks any pending row ESPN did not list for
the target date as `canceled`. That is right for today and wrong for a past
date, where a thin or empty scoreboard response is ordinary. Reconciling a
backfill date would turn real stored games into canceled ones -- the script
would destroy history while claiming to add it. `reconcile=False` is the
mode the pipeline already documents for lookback days.

One bad date does not abort the run
-----------------------------------
A single 404 or a transient ESPN failure partway through 24 days must not
discard the 23 that worked. Failures are collected and reported by date, so
a partial run is visibly partial rather than quietly short.

    python -m backend.scripts.backfill_date_range --db <abs path> \
        --sport nfl --start 2026-09-10 --end 2026-09-16
"""
import argparse
import asyncio
import logging
import os
from datetime import date, timedelta

from backend.database import get_engine, get_session, run_migrations
from backend.pipeline.full_pipeline import fetch_and_store_games

logger = logging.getLogger(__name__)


def daterange(start: date, end: date) -> list[date]:
    """Every date from ``start`` to ``end``, both ends included."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run(db_path: str, *, sport: str, start: date, end: date) -> dict:
    """Fetch and store every game ESPN lists between ``start`` and ``end``.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo."
        )

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)

    days = daterange(start, end)
    stored = 0
    failed: list[tuple[str, str]] = []
    try:
        for day in days:
            # `fetch_and_store_games` swallows a per-sport fetch failure and
            # returns 0, which is also what an off-day returns. The sink is
            # the only way to tell those apart.
            day_errors: list = []
            try:
                n = asyncio.run(fetch_and_store_games(
                    session, [sport], day, reconcile=False,
                    errors=day_errors)) or 0
            except Exception as exc:
                session.rollback()
                failed.append((day.isoformat(), f"{type(exc).__name__}: {exc}"))
                logger.warning("%s %s: fetch failed: %s", sport, day, exc)
                continue
            for _sport, exc in day_errors:
                failed.append((day.isoformat(), f"{type(exc).__name__}: {exc}"))
            if n:
                logger.info("%s %s: stored %d", sport, day, n)
            stored += n
    finally:
        session.close()

    return {"sport": sport, "dates": len(days), "stored": stored,
            "failed": failed}


def format_summary(s: dict) -> str:
    lines = [f"Backfilled {s['sport']}", ""]
    lines.append(f"  dates fetched : {s['dates']}")
    lines.append(f"  games stored  : {s['stored']}")
    if s["failed"]:
        lines.append("")
        lines.append(f"  {len(s['failed'])} date(s) FAILED and were skipped. The range is")
        lines.append("  incomplete; re-run it for these dates.")
        for day, err in s["failed"][:20]:
            lines.append(f"    {day}  {err}")
        if len(s["failed"]) > 20:
            lines.append(f"    ... and {len(s['failed']) - 20} more")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Fetch a past range of dates from ESPN and store the games.")
    ap.add_argument("--db", required=True, help="Path to the database.")
    ap.add_argument("--sport", required=True)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive.")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive.")
    args = ap.parse_args(argv)

    print(format_summary(run(
        args.db, sport=args.sport,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

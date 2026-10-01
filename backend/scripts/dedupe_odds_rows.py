"""Delete the older of any two `Odds` rows for one (game, bookmaker), then
add the unique index that keeps it that way.

Why this exists
----------------
`Odds` is meant to hold one current price per book per game, but nothing
enforced it, and the game-merge scripts could leave a book twice on a
merged game (see `backend.pipeline.odds_rows`). Measured against the live
db 2026-09-30: 6 pairs, 12 rows, all on game 1018 (nba, 2026-03-15, final),
each pair one write at 18:15 and one at 19:28. The newer row is the price
the collector would have written last, so it is kept.

`backend.database.migrate_odds_one_row_per_book` will not build the index
while any duplicate remains -- it warns instead, so the app still starts --
and it never deletes rows itself. This script is that deletion, done once,
on purpose. After `--apply` it runs the migration so the index exists
straight away. Idempotent: a second `--apply` deletes nothing.

Stop the scheduler and back the database up first; a dry run is the
default.

    python -m backend.scripts.dedupe_odds_rows --db <abs path>
    python -m backend.scripts.dedupe_odds_rows --db <abs path> --apply
"""
import argparse
import logging
import os
from collections import Counter

from sqlalchemy import inspect as sa_inspect

from backend.database import (ODDS_UNIQUE_INDEX, get_engine, get_session,
                              migrate_odds_one_row_per_book, run_migrations)
from backend.pipeline.odds_rows import stale_duplicates

logger = logging.getLogger(__name__)


def run_on_session(session, apply: bool = False) -> dict:
    doomed = stale_duplicates(session)
    summary = {"deleted": len(doomed),
               "by_game": dict(Counter(r.game_id for r in doomed)),
               "row_ids": sorted(r.id for r in doomed)}
    if apply and doomed:
        for row in doomed:
            session.delete(row)
        session.commit()
    return summary


def run(db_path: str, *, apply: bool = False) -> dict:
    """Refuses a missing ``db_path`` rather than creating an empty database
    whose zero-row 'success' would hide the typo."""
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(f"--db {db_path!r} does not exist")
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        summary = run_on_session(session, apply=apply)
    finally:
        session.close()
    if apply:
        migrate_odds_one_row_per_book(engine)
    summary["index_present"] = any(
        ix["name"] == ODDS_UNIQUE_INDEX
        for ix in sa_inspect(engine).get_indexes("odds"))
    return summary


def format_summary(s: dict, apply: bool) -> str:
    verb = "deleted" if apply else "would delete"
    lines = [
        "Deduplicated odds rows" if apply else "DRY RUN -- nothing written",
        "",
        f"  older duplicate rows {verb}: {s['deleted']}",
    ]
    for game_id, n in sorted(s["by_game"].items()):
        lines.append(f"    game {game_id}: {n}")
    lines += ["", f"  {ODDS_UNIQUE_INDEX} present: {s['index_present']}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Delete older duplicate odds rows. Dry run by default.")
    ap.add_argument("--db", required=True, help="absolute path to the sqlite db")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    args = ap.parse_args(argv)
    print(format_summary(run(args.db, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

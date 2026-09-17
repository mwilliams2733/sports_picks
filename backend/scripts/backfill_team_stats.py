"""One-off backfill of point-in-time team_stats and elo_history from final games.

Before this, ``team_stats`` held 330 rows spanning a single game out of 1058
finals and ``elo_history`` was empty, so ``CalibratedModel`` trained on a
feature matrix that was almost entirely zeros.  This script replays every
sport's final games in chronological order and writes, for each game, the
features a predictor could legitimately have known *going into* it.

What it writes
--------------
``team_stats``   point_diff, rest_days, home/away/last_n win-loss splits
``elo_history``  each team's **pre-game** Elo rating

What it deliberately does not write
-----------------------------------
``offensive_rating``, ``defensive_rating`` and ``pace``.  All three need
possessions; nothing in this repo collects them.  They are left absent rather
than defaulted, so the consumers' existing ``or 100.0`` fallbacks apply and
nobody downstream mistakes a made-up number for a measured one.

Safety
------
``--db`` is **required**.  There is no default, so the script cannot be run
against ``sports_picks.db`` by accident.  Use a copy first.

Resumability
------------
Each game is asked individually whether it already has rows, so a run that
stopped halfway -- or a hole punched in the middle of the history -- is filled
correctly.  Nothing tracks "how far it got".

Usage::

    python -m backend.scripts.backfill_team_stats --db /path/to/copy.db --dry-run
    python -m backend.scripts.backfill_team_stats --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import sys

from backend.database import get_engine, get_session
from backend.models import Base, Game
from backend.pipeline.team_stats import (
    COMBAT_SPORTS,
    backfill_elo_history,
    backfill_team_stats,
)


def _sports_with_final_games(session) -> list[str]:
    rows = (session.query(Game.sport)
            .filter(Game.status == "final",
                    Game.home_score.isnot(None), Game.away_score.isnot(None))
            .distinct().all())
    return sorted(s for (s,) in rows)


def run(db_path: str, sports: list[str] | None = None,
        dry_run: bool = False, force: bool = False) -> dict:
    """Backfill ``db_path``. Returns a per-sport summary dict."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    summary: dict[str, dict] = {}
    try:
        targets = sports or _sports_with_final_games(session)
        for sport in targets:
            stats = backfill_team_stats(session, sport, dry_run=dry_run,
                                        force=force)
            if sport in COMBAT_SPORTS:
                # grader._apply_combat_elo_update owns combat Elo history and
                # writes it post-game; replaying it here with pre-game
                # semantics would produce two incompatible meanings in one
                # column. Team stats are still backfilled.
                elo = {"sport": sport, "games_total": stats["games_total"],
                       "rows_written": 0, "games_skipped": 0,
                       "note": "elo skipped: owned by grader (post-game)"}
            else:
                elo = backfill_elo_history(session, sport, dry_run=dry_run)
            if not dry_run:
                session.commit()
            summary[sport] = {"team_stats": stats, "elo_history": elo}
        if dry_run:
            session.rollback()
    finally:
        session.close()
        engine.dispose()
    return summary


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Backfill complete", ""]
    lines.append(f"{'sport':8} {'games':>7} {'ts_done':>8} {'ts_skip':>8} "
                 f"{'ts_rows':>8} {'elo_rows':>9} {'elo_skip':>9}")
    lines.append("-" * 62)
    for sport, res in sorted(summary.items()):
        t, e = res["team_stats"], res["elo_history"]
        lines.append(
            f"{sport:8} {t['games_total']:7d} {t['games_processed']:8d} "
            f"{t['games_skipped']:8d} {t['rows_written']:8d} "
            f"{e['rows_written']:9d} {e['games_skipped']:9d}")
        if e.get("note"):
            lines.append(f"{'':8} {e['note']}")
    lines.append("")
    lines.append("NOT written: offensive_rating, defensive_rating, pace "
                 "(need possessions; no collector supplies them).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Backfill point-in-time team_stats and pre-game "
                     "elo_history from final games."))
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default, so this "
             "cannot hit production by accident. Use a copy first.")
    parser.add_argument(
        "--sport", action="append", dest="sports",
        help="Limit to a sport (repeatable). Default: every sport with "
             "final games.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts without writing anything.")
    parser.add_argument(
        "--force", action="store_true",
        help="Recompute team_stats even for games that already have rows. "
             "Use once to overwrite legacy rows of unknown provenance; the "
             "write is an upsert. Does not affect elo_history, which is "
             "always skipped per game that already has rows.")
    args = parser.parse_args(argv)

    summary = run(args.db, sports=args.sports, dry_run=args.dry_run,
                  force=args.force)
    print(format_summary(summary, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

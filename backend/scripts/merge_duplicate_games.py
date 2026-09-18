"""Merge games stored twice, once per date convention.

Why they exist
--------------
``_parse_date`` takes ``.date()`` off ESPN's UTC timestamp, but ESPN files its
scoreboard by Eastern date, so a game after 8pm ET lands a day late. When the
same game also arrived from another source with its Eastern date, two rows were
created and the old ``(sport, date, teams)`` key could not connect them. Plan
014 Task 1 gave rows ESPN's event id, which turns "probably duplicates" into
"provably the same game": 13 pairs share an id and all 13 span exactly one day.

Why it matters beyond tidiness
------------------------------
Nine of those pairs are final on **both** sides, so
``team_stats.backfill_elo_history`` replayed nine real results twice. It is
visible in the ratings -- team 6 sat at 1586.23 after game 469 and 1599.43
after game 474, the same game and the same points again -- and ``elo_history``
is what the calibrated model trains on.

The survivor rule
-----------------
1. The row with ``picks`` attached wins. Picks are user-facing and
   irreplaceable; ``team_stats`` and ``elo_history`` are derived and
   regenerable.
2. Otherwise the ``final`` row wins -- it carries the scores.
3. Otherwise the earlier date wins, which is the Eastern one.

**Picks on both sides is refused, not resolved.** Merging would move a pick
from one game to another, and no rule here can say which is right.

Derived rows are deleted, not repointed
---------------------------------------
Repointing the loser's ``team_stats`` and ``elo_history`` would preserve the
double-count, so they are deleted. ``odds`` and ``player_props`` are real
observations and are repointed.

**Deleting them is not sufficient on its own.** The damage is in every
*downstream* rating, not in the deleted rows, and ``backfill_elo_history``
skips any game that already has rows -- so a plain re-run adds nothing and
leaves the corruption in place. Undoing it needs a full replay:

    DELETE FROM elo_history WHERE game_id IN
        (SELECT id FROM games WHERE sport = '<sport>');
    python -m backend.scripts.backfill_team_stats --db <path>

Measured on a copy of production after merging 13 pairs: **957 of 2098
pre-game ratings moved**, median 0.32, p90 6.81, max 20.64 Elo points.

Safety
------
``--db`` is **required**. There is no default, so this cannot hit
``sports_picks.db`` by accident. Use a copy first.

Usage::

    python -m backend.scripts.merge_duplicate_games --db /path/to/copy.db --dry-run
    python -m backend.scripts.merge_duplicate_games --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from sqlalchemy import func

from backend.database import get_engine, get_session, run_migrations
from backend.models import (
    EloHistory, Game, Odds, PickModel, PlayerProp, TeamStat,
)

#: Real observations: they move to the survivor.
REPOINTED = (Odds, PlayerProp)
#: Derived from games and regenerable: deleting them is what lets the replay
#: recompute, which is how the double-count is undone.
DELETED = (TeamStat, EloHistory)


def twin_groups(session) -> list[list[Game]]:
    """Games grouped by a shared ``(sport, espn_id)``, only where >1 exists."""
    dupes = (session.query(Game.sport, Game.espn_id)
             .filter(Game.espn_id.isnot(None))
             .group_by(Game.sport, Game.espn_id)
             .having(func.count(Game.id) > 1).all())
    groups = []
    for sport, espn_id in dupes:
        rows = (session.query(Game)
                .filter(Game.sport == sport, Game.espn_id == espn_id)
                .order_by(Game.date, Game.id).all())
        groups.append(rows)
    return groups


def _pick_count(session, game_id: int) -> int:
    return session.query(func.count(PickModel.id)).filter(
        PickModel.game_id == game_id).scalar() or 0


def choose_survivor(session, rows: list[Game]) -> Game | None:
    """The row to keep, or ``None`` when the group is not decidable.

    ``None`` means more than one side carries picks: merging would move a pick
    between games, and nothing here can say which game it belongs to.
    """
    with_picks = [r for r in rows if _pick_count(session, r.id) > 0]
    if len(with_picks) > 1:
        return None
    if with_picks:
        return with_picks[0]
    final = [r for r in rows if r.status == "final"]
    if final:
        return final[0]
    return rows[0]          # ordered by date: the earlier, Eastern-dated row


def _absorb(session, survivor: Game, loser: Game) -> None:
    """Move what must be kept, delete what must be recomputed, drop the loser."""
    # A survivor that never finalized inherits the result rather than losing it.
    if survivor.status != "final" and loser.status == "final":
        survivor.status = loser.status
        survivor.home_score = loser.home_score
        survivor.away_score = loser.away_score
    if survivor.start_time is None:
        survivor.start_time = loser.start_time

    for model in REPOINTED:
        for row in session.query(model).filter(model.game_id == loser.id).all():
            row.game_id = survivor.id
    for model in DELETED:
        for row in session.query(model).filter(model.game_id == loser.id).all():
            session.delete(row)
    session.delete(loser)


def run(db_path: str, *, dry_run: bool = False) -> dict:
    """Merge twin games in ``db_path``. Returns a summary dict.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    successful zero-pair merge against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-pair 'successful' merge would hide the typo."
        )

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    counts: Counter = Counter()
    refusals: list[str] = []
    try:
        groups = twin_groups(session)
        counts["pairs"] = len(groups)
        for rows in groups:
            survivor = choose_survivor(session, rows)
            if survivor is None:
                counts["refused"] += 1
                refusals.append(str(rows[0].espn_id))
                continue
            if dry_run:
                continue
            for loser in rows:
                if loser.id != survivor.id:
                    _absorb(session, survivor, loser)
                    counts["deleted_rows"] += 1
            counts["merged"] += 1
        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        session.close()

    return {"pairs": counts["pairs"], "merged": counts["merged"],
            "refused": counts["refused"], "deleted_rows": counts["deleted_rows"],
            "refused_ids": refusals}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Merge complete", ""]
    lines.append(f"  twin groups found : {summary['pairs']}")
    lines.append(f"  merged            : {summary['merged']}")
    lines.append(f"  rows deleted      : {summary['deleted_rows']}")
    lines.append(f"  refused           : {summary['refused']}")
    if summary["refused_ids"]:
        lines.append("")
        lines.append("  refused espn_ids (picks on more than one side):")
        for e in summary["refused_ids"]:
            lines.append(f"     {e}")
        lines.append("  Merging these would move a pick between games.")
    lines.append("")
    lines.append("  The losers' team_stats and elo_history were DELETED, not")
    lines.append("  repointed -- but that alone does NOT undo the double-count:")
    lines.append("  backfill_elo_history skips games that already have rows, so a")
    lines.append("  plain re-run adds nothing. Replay the sport from scratch:")
    lines.append("    DELETE FROM elo_history WHERE game_id IN")
    lines.append("      (SELECT id FROM games WHERE sport='<sport>');")
    lines.append("    python -m backend.scripts.backfill_team_stats --db <path>")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge games that share an ESPN event id.")
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default, so this "
             "cannot hit production by accident. Use a copy first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would merge, write nothing.")
    args = parser.parse_args(argv)

    print(format_summary(run(args.db, dry_run=args.dry_run), args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

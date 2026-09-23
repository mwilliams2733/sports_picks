"""Recompute odds_at_close and line_at_close on existing pick_results rows.

Delegates to `capture_closing_odds` rather than mirroring it. The previous
version kept its own copy of that logic under a comment saying "mirrors
capture_closing_odds", which is two code paths that must agree -- and they
stopped agreeing the moment the grader learned to read the snapshot series.

What re-running this changes
----------------------------
Both the grader and this script used to take the most recent `Odds` row for
a game. `Odds` is one row per (game, BOOKMAKER), so "most recent" meant
whichever book was written last in the final upsert pass, and before
``7463de7`` that write was frequently an IN-PLAY price. The close is now
every book's last pre-kickoff snapshot, consensused by `average_odds` -- the
same function that produced `odds_at_pick`, which is what CLV subtracts it
from.

**Expect the usable sample to SHRINK.** Historical snapshots are backfill
anchors stamped with `Odds.timestamp`, the last write, so for a game that
was re-priced after kickoff every surviving row is post-kickoff and there is
no valid pre-game close to recover. Those rows are CLEARED rather than left
holding the old number: a stale close is not a conservative default, it is a
wrong measurement that enters the CLV average silently. Fewer honest closes
beat more dishonest ones.

Safe to re-run; only rows whose value actually changes are counted.

    python -m backend.backfill_closing_lines [db_path] [--dry-run]
"""
from __future__ import annotations

import sys

from backend.database import get_engine, get_session
from backend.models import PickModel, PickResult
from backend.pipeline.grader import capture_closing_odds


def backfill(db_path: str = "sports_picks.db", dry_run: bool = False) -> dict:
    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        rows = (
            session.query(PickResult, PickModel)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .all()
        )

        changed_odds = 0
        changed_line = 0
        cleared = 0
        for pr, pm in rows:
            before = (pr.odds_at_close, pr.line_at_close)

            # Cleared first so a row the new rules cannot price ends up NULL
            # instead of keeping a value the old rules invented.
            pr.odds_at_close = None
            pr.line_at_close = None
            capture_closing_odds(session, pr, pm.game_id, pm.pick_type,
                                 pm.pick_value, pm.odds_at_pick)
            after = (pr.odds_at_close, pr.line_at_close)

            if before[0] != after[0]:
                changed_odds += 1
            if before[1] != after[1]:
                changed_line += 1
            if any(b is not None for b in before) and all(a is None for a in after):
                cleared += 1

            if dry_run:
                pr.odds_at_close, pr.line_at_close = before

        if dry_run:
            session.rollback()
        else:
            session.commit()

        return {
            "scanned": len(rows),
            "changed_odds_at_close": changed_odds,
            "changed_line_at_close": changed_line,
            "cleared_no_valid_close": cleared,
            "dry_run": dry_run,
        }
    finally:
        session.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    db_path = args[0] if args else "sports_picks.db"
    result = backfill(db_path, dry_run="--dry-run" in flags)
    print(result)

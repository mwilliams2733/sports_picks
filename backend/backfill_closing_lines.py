"""Backfill odds_at_close and line_at_close on existing pick_results rows.

Reads the most recent Odds snapshot per game and rewrites closing-line fields
using the same logic as backend.pipeline.grader.capture_closing_odds. Safe to
re-run; only rows that produce a different value are updated.

Usage:
    python -m backend.backfill_closing_lines [db_path] [--dry-run]

Defaults to sports_picks.db if no path given. --dry-run prints the diff and
does not commit.
"""
from __future__ import annotations
import sys

from backend.database import get_engine, get_session
from backend.models import Odds, PickModel, PickResult


def _closing_for(odds: Odds, pick_type: str, pick_value: str) -> tuple[int | None, float | None]:
    """Return (odds_at_close, line_at_close) for a single pick + closing snapshot.

    Mirrors capture_closing_odds. odds_at_close for spread/total is intentionally
    left to the caller (set to odds_at_pick) because juice is not stored.
    """
    if pick_type == "moneyline":
        if "HOME" in pick_value:
            return odds.moneyline_home, None
        return odds.moneyline_away, None
    if pick_type == "spread":
        if "HOME" in pick_value:
            return None, odds.spread_home
        return None, odds.spread_away
    if pick_type == "over_under":
        return None, odds.over_under
    return None, None


def backfill(db_path: str = "sports_picks.db", dry_run: bool = False) -> dict:
    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        rows = (
            session.query(PickResult, PickModel)
            .join(PickModel, PickResult.pick_id == PickModel.id)
            .all()
        )
        # Group closing snapshots by game so we hit the DB once per game.
        game_ids = {pm.game_id for _, pm in rows}
        closing_by_game: dict[int, Odds] = {}
        for gid in game_ids:
            o = (
                session.query(Odds)
                .filter(Odds.game_id == gid)
                .order_by(Odds.timestamp.desc())
                .first()
            )
            if o:
                closing_by_game[gid] = o

        changed_odds = 0
        changed_line = 0
        skipped_no_close = 0
        for pr, pm in rows:
            closing = closing_by_game.get(pm.game_id)
            if closing is None:
                skipped_no_close += 1
                continue
            new_odds_at_close, new_line_at_close = _closing_for(closing, pm.pick_type, pm.pick_value)
            # For spread/total, odds_at_close should reflect odds_at_pick (juice fallback).
            if pm.pick_type in ("spread", "over_under"):
                new_odds_at_close = pm.odds_at_pick

            if pr.odds_at_close != new_odds_at_close:
                if not dry_run:
                    pr.odds_at_close = new_odds_at_close
                changed_odds += 1
            if pr.line_at_close != new_line_at_close:
                if not dry_run:
                    pr.line_at_close = new_line_at_close
                changed_line += 1

        if not dry_run:
            session.commit()

        summary = {
            "scanned": len(rows),
            "changed_odds_at_close": changed_odds,
            "changed_line_at_close": changed_line,
            "skipped_no_closing_snapshot": skipped_no_close,
            "dry_run": dry_run,
        }
        return summary
    finally:
        session.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    db_path = args[0] if args else "sports_picks.db"
    dry_run = "--dry-run" in flags
    result = backfill(db_path, dry_run=dry_run)
    print(result)

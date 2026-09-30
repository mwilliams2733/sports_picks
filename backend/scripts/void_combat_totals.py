"""Void every spread and total ever stored on a combat sport.

Why this exists
---------------
A combat bout's stored "score" is a 0/1 pair -- winner 1, loser 0 -- so the
bout has no total and no margin. On 2026-03-21 the team-sport ``ensemble``
nonetheless emitted 13 mma totals, every one "Over 0", and ``grade_pick``
settled them against ``home_score + away_score == 1``: 13 wins, +11.82u,
none of it about the fight (see ``audit_combat_grading``). The generator and
the grader now refuse combat spreads and totals; this settles the ones
already stored.

What voiding means here
-----------------------
The same thing it means everywhere in this project -- ``push``, payout 0.0,
imported from ``void_stuck_bouts`` so the two cannot drift. A push returns
the stake and leaves the win-rate denominator, so the book reads as though
the bet was never made. Unlike ``void_stuck_bouts`` the GAME is left alone:
these bouts happened and their moneylines were graded correctly.

A pick already settled as a push is left as it is. An ungraded one gets a
push result, so no grader can ever settle it as a win.

Like the other repair scripts, a dry run is the default and ``--apply``
must be given. Back the database up first.

    python -m backend.scripts.void_combat_totals --db <abs path>
    python -m backend.scripts.void_combat_totals --db <abs path> --apply
"""
import argparse
import logging
import os

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, PickModel, PickResult
from backend.pipeline.team_stats import COMBAT_SPORTS
from backend.scripts.void_stuck_bouts import VOID_PAYOUT, VOID_RESULT

logger = logging.getLogger(__name__)

VOIDABLE_TYPES = ("spread", "over_under")


def voidable(session):
    """``(pick, result_or_None)`` for every combat spread or total not
    already settled as a void."""
    rows = (session.query(PickModel, PickResult)
            .join(Game, Game.id == PickModel.game_id)
            .outerjoin(PickResult, PickResult.pick_id == PickModel.id)
            .filter(Game.sport.in_(list(COMBAT_SPORTS)),
                    PickModel.pick_type.in_(VOIDABLE_TYPES))
            .order_by(PickModel.id)
            .all())
    return [(p, r) for p, r in rows if r is None or r.result != VOID_RESULT]


def run_on_session(session, *, apply: bool = False) -> dict:
    summary = {"regraded": 0, "settled": 0, "units_removed": 0.0,
               "by_result": {}, "pick_ids": []}
    for pick, result in voidable(session):
        summary["pick_ids"].append(pick.id)
        if result is None:
            summary["settled"] += 1
            if apply:
                session.add(PickResult(pick_id=pick.id, result=VOID_RESULT,
                                       payout=VOID_PAYOUT))
            continue
        summary["regraded"] += 1
        summary["by_result"][result.result] = summary["by_result"].get(result.result, 0) + 1
        if result.result == "win":
            summary["units_removed"] += result.payout or 0.0
        elif result.result == "loss":
            summary["units_removed"] -= 1.0
        if apply:
            result.result = VOID_RESULT
            result.payout = VOID_PAYOUT
    if apply and summary["pick_ids"]:
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
        return run_on_session(session, apply=apply)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    verb = "voided" if apply else "would void"
    return "\n".join([
        "Voided combat spreads and totals" if apply else "DRY RUN -- nothing written",
        "",
        f"  graded, {verb:<10}: {s['regraded']}  (was {s['by_result'] or 'none'})",
        f"  ungraded, {verb:<8}: {s['settled']}",
        f"  units removed        : {s['units_removed']:+.2f}",
        f"  pick ids             : {s['pick_ids']}",
        "",
        f"  Settled as {VOID_RESULT}, payout {VOID_PAYOUT}: the stake returns and the",
        "  pick leaves the win-rate denominator. The bouts themselves are untouched.",
    ])


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Void combat spreads/totals. Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--apply", action="store_true", help="Write the changes.")
    args = ap.parse_args(argv)
    print(format_summary(run(args.db, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Delete the duplicate prop picks stored before d8191aa, and their results.

Before that commit every window run re-inserted the day's props, and every
alternate line was its own pick: on 2026-09-28 the table held 6,980 prop rows
for 480 (game, strategy, player, market) keys, and grading counted each row
as a 1u wager. The bankroll read -121.24u.

Kept per key: the OLDEST row -- the one `_store_prop_picks` refreshes going
forward -- plus every row the digest emailed, since the emailed record points
at it. Everything else in the key goes, with its pick_results row.
pick_results and emailed_picks are the only tables that reference picks.

    python -m backend.scripts.dedupe_prop_picks            # dry run: the plan
    python -m backend.scripts.dedupe_prop_picks --apply    # delete

Take a `sqlite3 .backup` snapshot before --apply; a copy of the live file
is stale under WAL.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.models import EmailedPick, PickModel, PickResult
from backend.pipeline.pick_generator import bankroll_from_results

BATCH = 500


@dataclass
class Plan:
    delete: list[int] = field(default_factory=list)
    keys: int = 0
    rows: int = 0
    results_deleted: int = 0
    bankroll_before: float = 0.0
    bankroll_after: float = 0.0


def plan(session: Session) -> Plan:
    """What --apply would delete, and the bankroll it would leave. Writes nothing."""
    emailed = {pid for (pid,) in session.query(EmailedPick.pick_id)}
    keep: dict[tuple, int] = {}
    delete: list[int] = []
    rows = (session.query(PickModel.id, PickModel.game_id, PickModel.strategy_id,
                          PickModel.prop_player, PickModel.prop_market)
            .filter(PickModel.pick_type == "prop")
            .order_by(PickModel.id.asc()).all())
    for pid, game_id, strategy_id, player, market in rows:
        key = (game_id, strategy_id, player, market)
        if key not in keep:
            keep[key] = pid
        elif pid not in emailed:
            delete.append(pid)

    doomed = set(delete)
    results = (session.query(PickResult.pick_id, PickResult.result, PickResult.payout)
               .order_by(PickResult.id.asc()).all())
    before, _ = bankroll_from_results((r, p) for _, r, p in results)
    after, _ = bankroll_from_results((r, p) for pid, r, p in results
                                     if pid not in doomed)
    return Plan(delete=delete, keys=len(keep), rows=len(rows),
                results_deleted=sum(1 for pid, _, _ in results if pid in doomed),
                bankroll_before=before, bankroll_after=after)


def apply_plan(session: Session, p: Plan) -> None:
    """Delete the planned picks and their results, in one transaction."""
    for i in range(0, len(p.delete), BATCH):
        chunk = p.delete[i:i + BATCH]
        session.query(PickResult).filter(PickResult.pick_id.in_(chunk)).delete(
            synchronize_session=False)
        session.query(PickModel).filter(PickModel.id.in_(chunk)).delete(
            synchronize_session=False)
    session.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="Delete. Without it, only the plan is printed.")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)

    from backend.config import load_config
    from backend.database import get_engine, get_session
    session = get_session(get_engine(load_config(args.config)["database_path"]))
    try:
        p = plan(session)
        print(f"prop picks: {p.rows} rows for {p.keys} keys")
        print(f"to delete : {len(p.delete)} picks, {p.results_deleted} results")
        print(f"bankroll  : {p.bankroll_before:.2f}u -> {p.bankroll_after:.2f}u")
        if not args.apply:
            print("dry run; pass --apply to delete")
            return 0
        apply_plan(session, p)
        print("deleted")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

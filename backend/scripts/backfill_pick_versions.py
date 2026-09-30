"""Backfill a version-1 `pick_versions` row for every pick that has none.

Why this exists
----------------
`pick_versions` only started recording on the merge of `feat/pick-versions`.
Every pick stored before that has no version history at all -- not because
nothing changed, but because nothing was watching. This writes one row per
such pick, from its CURRENT state, `source='backfill'`, `recorded_at` set to
the pick's `created_at` (the best available estimate of when that state
existed -- not "now", which would fabricate an observation time).

This is a last-state backfill, not a first-state one. A pick refreshed
several times before this table existed has only its FINAL pre-kickoff
values on record -- `PickModel` never kept the earlier ones. So a backfilled
version 1 is the pick's LAST known state, not its first, and
`first_seen_at` derived from it in the export is really "last refresh
time" for these rows. See `docs/data-dictionary.md` for the reader-facing
version of this caveat.

Idempotent: a pick that already has any `pick_versions` row (whether written
live or by an earlier backfill run) is skipped, so a second `--apply` writes
zero rows.

Like the other repair scripts, a dry run is the default and `--apply` must
be given. Back the database up first.

    python -m backend.scripts.backfill_pick_versions --db <abs path>
    python -m backend.scripts.backfill_pick_versions --db <abs path> --apply
"""
import argparse
import logging
import os
from collections import Counter

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, PickModel, PickVersion

logger = logging.getLogger(__name__)


def unversioned_picks(session):
    """Every `PickModel` with no row in `pick_versions` yet, joined to its
    game for the per-sport count, ordered by id for a deterministic run."""
    versioned_ids = {pid for (pid,) in session.query(PickVersion.pick_id).distinct()}
    rows = (session.query(PickModel, Game.sport)
            .join(Game, Game.id == PickModel.game_id)
            .order_by(PickModel.id)
            .all())
    return [(p, sport) for p, sport in rows if p.id not in versioned_ids]


def run_on_session(session, apply: bool = False) -> dict:
    summary = {"written": 0, "by_sport": {}, "pick_ids": []}
    for pick, sport in unversioned_picks(session):
        summary["pick_ids"].append(pick.id)
        summary["written"] += 1
        summary["by_sport"][sport] = summary["by_sport"].get(sport, 0) + 1
        if apply:
            session.add(PickVersion(
                pick_id=pick.id, version=1, recorded_at=pick.created_at,
                source="backfill",
                pick_value=pick.pick_value, confidence=pick.confidence,
                edge_pct=pick.edge_pct, odds_at_pick=pick.odds_at_pick,
                model_prob=pick.model_prob,
                suggested_unit_size=pick.suggested_unit_size,
                rationale_json=pick.rationale_json,
            ))
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
    verb = "wrote" if apply else "would write"
    lines = [
        "Backfilled pick_versions" if apply else "DRY RUN -- nothing written",
        "",
        f"  version-1 rows {verb}: {s['written']}",
    ]
    for sport, n in sorted(s["by_sport"].items()):
        lines.append(f"    {sport}: {n}")
    lines += [
        "",
        "  Each row's recorded_at is the pick's created_at, not now -- it is",
        "  the pick's LAST known pre-kickoff state, not its first. See",
        "  docs/data-dictionary.md for what that means for first_seen_at.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Backfill pick_versions version-1 rows. Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--apply", action="store_true", help="Write the changes.")
    args = ap.parse_args(argv)
    print(format_summary(run(args.db, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

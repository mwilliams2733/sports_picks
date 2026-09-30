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

Run order matters and is NOT enforced by this script
-----------------------------------------------------
Run this only with the scheduler stopped, strictly between deploy and
restart:

    1. stop the scheduler;
    2. merge and deploy this code;
    3. run `--apply`;
    4. restart the scheduler.

Two hazards if that order is not followed, both from the same root cause --
this script and a live `generate_and_store_picks`/`_store_prop_picks` run
deciding independently what a pick's "next" version is:

* **Running the backfill against a db the new code is already live on**
  quietly loses data, not just correctness: the scheduler's first refresh
  of any pre-existing pick writes version 1 itself (`source='refresh'`),
  from whatever state the pick is in AT THAT REFRESH -- and this script's
  `unversioned_picks` filter then skips that pick forever, because it now
  has a version. The pick's TRUE pre-merge final state (what this script
  exists to capture) is gone, silently replaced by whatever the first live
  refresh happened to compute.
* **Running the backfill concurrently with a live scheduler** can race a
  window run for the same pick: both independently compute "this pick has
  no version yet, so mine is version 1", and one write wins while the
  other hits `uq_pick_versions_pick_version` and is caught and skipped
  (see `pick_versions.record_pick_version`'s docstring) -- survivable, but
  still a hazard worth avoiding, since which side's "version 1" you get is
  then a coin flip.

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
from backend.pipeline.pick_versions import record_pick_version

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
            # Same helper the live write paths use, not a second field list
            # that could drift from it -- `record_pick_version` already
            # knows `source='backfill'` means `recorded_at = created_at`
            # (see its own docstring), and every pick here is, by
            # `unversioned_picks`'s filter, guaranteed to have no prior
            # version, so this always writes version 1.
            record_pick_version(session, pick, "backfill")
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

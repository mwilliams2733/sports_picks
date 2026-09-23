"""Seed `line_snapshots` from the prices `Odds` still holds.

What this can and cannot recover
--------------------------------
`Odds` is upserted in place, so it holds ONE price per (game, bookmaker):
the last one seen, at the timestamp it was last written. Every earlier quote
is already gone and no backfill can bring it back.

So this recovers one anchor point per series, not a history. A game whose
line moved four times before today contributes a single row here, stamped
with when that final price was written. That is enough to make the table
non-empty and to give every existing game a comparable late price, and it is
NOT enough to measure movement on any game that predates the collector
change. Anything studying movement has to filter on series length, which is
why `--report` prints it.

The one exception is `nflverse_close`, whose rows are genuine closing lines
imported from nflverse. Those seed correctly because a closing line is
exactly the single point it claims to be.

Idempotent: a (game, bookmaker) pair that already has any snapshot is left
alone, so a second run adds nothing rather than stamping a duplicate anchor.

    python -m backend.scripts.backfill_line_snapshots --db <abs path>
    python -m backend.scripts.backfill_line_snapshots --db <abs path> --apply
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import timezone

from backend.analysis.line_snapshots import PRICE_FIELDS
from backend.database import get_engine, get_session, run_migrations
from backend.models import LineSnapshot, Odds

logger = logging.getLogger(__name__)


def seed_from_odds(session, *, apply: bool = False) -> dict:
    """Write one snapshot per Odds row that has no series yet."""
    seeded = set(session.query(LineSnapshot.game_id, LineSnapshot.bookmaker)
                 .distinct().all())
    summary = {"odds_rows": 0, "already_seeded": 0, "seeded": 0, "empty": 0}

    for odds in session.query(Odds).order_by(Odds.id).all():
        summary["odds_rows"] += 1
        if (odds.game_id, odds.bookmaker) in seeded:
            summary["already_seeded"] += 1
            continue

        values = {f: getattr(odds, f) for f in PRICE_FIELDS}
        # A row of all NULLs is not a quote and seeding it would invent an
        # observation that never happened.
        if all(v is None for v in values.values()):
            summary["empty"] += 1
            continue

        summary["seeded"] += 1
        if not apply:
            continue

        when = odds.timestamp
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        session.add(LineSnapshot(game_id=odds.game_id,
                                 bookmaker=odds.bookmaker,
                                 captured_at=when, last_seen_at=when,
                                 **values))
        seeded.add((odds.game_id, odds.bookmaker))

    if apply:
        session.commit()
    return summary


def series_report(session) -> dict[int, int]:
    """How many series have 1 observation, 2, 3... -- the answer to "can I
    measure movement on this game yet"."""
    lengths: dict[tuple[int, str], int] = {}
    for game_id, bookmaker in session.query(LineSnapshot.game_id,
                                            LineSnapshot.bookmaker).all():
        lengths[(game_id, bookmaker)] = lengths.get((game_id, bookmaker), 0) + 1
    out: dict[int, int] = {}
    for n in lengths.values():
        out[n] = out.get(n, 0) + 1
    return dict(sorted(out.items()))


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="Path to the database.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this it is a dry run.")
    ap.add_argument("--report", action="store_true",
                    help="Also print the distribution of series lengths.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        raise SystemExit(f"{args.db!r} does not exist.")

    engine = get_engine(args.db)
    run_migrations(engine)
    session = get_session(engine)
    try:
        s = seed_from_odds(session, apply=args.apply)
        print("Seeded line_snapshots from Odds" if args.apply
              else "DRY RUN -- nothing written")
        print(f"  odds rows scanned     : {s['odds_rows']}")
        print(f"  {'seeded' if args.apply else 'would seed':<21} : {s['seeded']}")
        print(f"  already had a series  : {s['already_seeded']}")
        print(f"  all-NULL, not a quote : {s['empty']}")
        print("\n  One anchor point per series, not a history -- every price"
              "\n  before the last was already destroyed by the upsert.")
        if args.report:
            print("\n  series length -> how many (game, bookmaker) series:")
            for length, count in series_report(session).items():
                print(f"    {length:>3} observation(s) : {count}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Remove redundant copies of the same pick.

Why they exist
--------------
`generate_and_store_picks` used to insert unconditionally, and every window
run re-picked every scheduled game. A day with three runs therefore carried
three copies of each pick. On 2026-09-19 that was 622 redundant rows out of
1062 -- well over half the table.

Ungraded they are noise. Graded they are worse: nba game 1018's Under was
graded seven times at a payout of 0.909 each, so one wager contributed
+6.36 units instead of +0.909. Any ROI measured over such a table is wrong
by however many times each pick happened to be duplicated.

The insert path is fixed (idempotent per game/strategy/market since
2026-09-19), so this is a cleanup of rows already written, not an ongoing
need.

What survives
-------------
The lowest id in each group -- the pick as first made. That matches the
idempotence rule the generator now follows: the first pick stands, and its
`odds_at_pick` is the price the bet was taken at. It matters because copies
are not always identical; where a group disagrees, later runs re-priced the
same market as the line moved, and the first entry is the real one.

Graded rows are never deleted
-----------------------------
A pick with a `pick_results` row is refused and reported. Removing one would
silently rewrite recorded results, and deciding what a graded duplicate
*should* have been is a judgement this script has no business making.

Unlike the backfill scripts, a dry run is the DEFAULT here and `--apply` is
required. This deletes.

    python -m backend.scripts.dedupe_picks --db <abs path> --sport ncaaf
    python -m backend.scripts.dedupe_picks --db <abs path> --sport ncaaf --apply
"""
import argparse
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, PickModel, PickResult


@dataclass
class Group:
    """One market picked more than once."""
    key: tuple
    keep: int
    drop: list[int] = field(default_factory=list)
    graded: list[int] = field(default_factory=list)


def _key(pick: PickModel) -> tuple:
    # prop_player/prop_market are part of the identity: a single game carries
    # many prop picks, and keying on pick_type alone would call them all
    # duplicates of one another.
    return (pick.game_id, pick.strategy_id, pick.pick_type,
            pick.prop_player or "", pick.prop_market or "")


def duplicate_groups(session, sport: str | None = None,
                     on_date: date | None = None) -> list[Group]:
    """Every market picked more than once, with the survivor and the rest."""
    q = session.query(PickModel).join(Game, Game.id == PickModel.game_id)
    if sport:
        q = q.filter(Game.sport == sport)
    if on_date:
        q = q.filter(Game.date == on_date)

    by_key: dict[tuple, list[PickModel]] = defaultdict(list)
    for pick in q.all():
        by_key[_key(pick)].append(pick)

    graded_ids = {r.pick_id for r in session.query(PickResult.pick_id)}

    groups: list[Group] = []
    for key, picks in by_key.items():
        if len(picks) < 2:
            continue
        picks.sort(key=lambda p: p.id)
        keep, rest = picks[0], picks[1:]
        group = Group(key=key, keep=keep.id)
        for p in rest:
            (group.graded if p.id in graded_ids else group.drop).append(p.id)
        groups.append(group)
    return sorted(groups, key=lambda g: g.keep)


def run(db_path: str, *, apply: bool = False, sport: str | None = None,
        on_date: date | None = None) -> dict:
    """Report, and with ``apply`` remove, redundant pick copies.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo."
        )

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        groups = duplicate_groups(session, sport=sport, on_date=on_date)
        doomed = [i for g in groups for i in g.drop]
        refused = [i for g in groups for i in g.graded]
        if apply and doomed:
            session.query(PickModel).filter(
                PickModel.id.in_(doomed)).delete(synchronize_session=False)
            session.commit()
        return {
            "groups": len(groups),
            "would_delete" if not apply else "deleted": len(doomed),
            "refused_graded": len(refused),
            "refused_ids": refused,
        }
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Deduplicated picks" if apply else "DRY RUN -- nothing written", ""]
    lines.append(f"  duplicate groups : {s['groups']}")
    lines.append(f"  {'deleted' if apply else 'would delete'}      : "
                 f"{s.get('deleted', s.get('would_delete', 0))}")
    if s["refused_graded"]:
        lines.append("")
        lines.append(f"  REFUSED: {s['refused_graded']} duplicate row(s) are graded "
                     "and were left alone.")
        lines.append("  Deleting one would rewrite a recorded result. Decide what")
        lines.append("  those wagers should have been before removing them.")
        lines.append(f"  ids: {s['refused_ids'][:20]}"
                     + (" ..." if len(s["refused_ids"]) > 20 else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Remove redundant copies of the same pick. Keeps the "
                    "earliest of each group; never deletes a graded row.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--sport", help="Limit to one sport.")
    ap.add_argument("--date", dest="on_date",
                    help="Limit to games on this date (YYYY-MM-DD).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this it is a dry run.")
    args = ap.parse_args(argv)

    on_date = date.fromisoformat(args.on_date) if args.on_date else None
    print(format_summary(
        run(args.db, apply=args.apply, sport=args.sport, on_date=on_date),
        args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

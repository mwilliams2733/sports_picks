"""One-off backfill of ``picks.prop_player`` and ``picks.prop_market``.

Prop picks generated before those columns existed carry their player and
market only inside ``pick_value``, as prose: "Dean Wade Over 0.5 3-Pointers".
Grading needs them as fields -- ``grade_prop_pick`` takes a market *key* and
looks the player's box score up by name.

What it writes
--------------
``prop_player``  the text before "Over"/"Under"
``prop_market``  the market KEY ("player_threes"), never the display label

What it deliberately refuses to write
-------------------------------------
A market it cannot resolve exactly. ``prop_pipeline._market_label`` maps 7 of
the 16 keys in ``MARKET_STAT_MAP`` to a display label; the other 9 fall through
as themselves. So the reverse mapping is only defined for labels the forward
map actually produces, and a guess would silently grade a prop against the
wrong stat -- a confident wrong result, which is worse than an ungraded pick.
Unresolvable rows are left NULL and counted.

The reverse map is *derived* by calling ``_market_label`` on every key rather
than retyping its contents, so it cannot drift from the forward map.

Safety
------
``--db`` is **required**. There is no default, so the script cannot be run
against ``sports_picks.db`` by accident. Use a copy first.

Resumability
------------
Each pick is asked individually whether it already has the fields, so a run
that stopped halfway -- or a hole punched in the middle -- is filled correctly.
Nothing tracks "how far it got".

Usage::

    python -m backend.scripts.backfill_prop_fields --db /path/to/copy.db --dry-run
    python -m backend.scripts.backfill_prop_fields --db /path/to/copy.db
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

from backend.database import get_engine, get_session, run_migrations
from backend.models import PickModel
from backend.pipeline.grader import MARKET_STAT_MAP
from backend.pipeline.prop_pipeline import _market_label

#: "<player> Over|Under <line> <label>"
_PICK_VALUE = re.compile(r"^(?P<player>.+?)\s+(?P<outcome>Over|Under)\s+"
                         r"(?P<line>\d+(?:\.\d+)?)\s+(?P<label>.+)$")


def build_label_to_market() -> dict[str, str]:
    """Reverse of ``_market_label``, derived from it rather than retyped.

    Raises ``ValueError`` if two market keys share a display label: the reverse
    mapping would be ambiguous, and picking either is a guess.
    """
    reverse: dict[str, str] = {}
    for market in MARKET_STAT_MAP:
        label = _market_label(market)
        if label in reverse and reverse[label] != market:
            raise ValueError(
                f"display label {label!r} maps to both {reverse[label]!r} and "
                f"{market!r}; the reverse mapping is ambiguous")
        reverse[label] = market
    return reverse


def parse_pick_value(pick_value: str,
                     label_to_market: dict[str, str]) -> tuple[str, str] | None:
    """``(player, market_key)`` for a prop's ``pick_value``, or ``None``.

    ``None`` means "cannot resolve exactly" -- an unparseable string, or a
    trailing label that no market produces. The caller leaves the row NULL.
    """
    match = _PICK_VALUE.match(pick_value or "")
    if match is None:
        return None
    market = label_to_market.get(match.group("label").strip())
    if market is None:
        return None
    return match.group("player").strip(), market


def run(db_path: str, dry_run: bool = False) -> dict:
    """Backfill ``db_path``. Returns a summary dict.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist. Without this,
    ``run_migrations`` would create an empty database at a typo'd path and the
    run would report a successful zero-row backfill against it -- the most
    misleading possible outcome for a script whose job is to populate an
    existing database.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' backfill would hide the typo."
        )

    label_to_market = build_label_to_market()
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)

    resolved = skipped = unresolved = 0
    unresolved_labels: Counter[str] = Counter()
    try:
        props = session.query(PickModel).filter(PickModel.pick_type == "prop").all()
        for pick in props:
            if pick.prop_player and pick.prop_market:
                skipped += 1
                continue
            parsed = parse_pick_value(pick.pick_value, label_to_market)
            if parsed is None:
                unresolved += 1
                match = _PICK_VALUE.match(pick.pick_value or "")
                unresolved_labels[match.group("label").strip() if match
                                  else "<unparseable>"] += 1
                continue
            if not dry_run:
                pick.prop_player, pick.prop_market = parsed
            resolved += 1
        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        session.close()

    return {"props_total": len(props), "resolved": resolved,
            "already_populated": skipped, "unresolved": unresolved,
            "unresolved_labels": dict(unresolved_labels)}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Backfill complete", ""]
    lines.append(f"  prop picks          {summary['props_total']}")
    lines.append(f"  resolved            {summary['resolved']}")
    lines.append(f"  already populated   {summary['already_populated']}")
    lines.append(f"  unresolved          {summary['unresolved']}")
    if summary["unresolved_labels"]:
        lines.append("")
        lines.append("  Left NULL rather than guessed, by trailing label:")
        for label, count in sorted(summary["unresolved_labels"].items(),
                                   key=lambda kv: -kv[1]):
            lines.append(f"    {label!r}  x{count}")
        lines.append("")
        lines.append("  A guessed market grades a prop against the wrong stat.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill picks.prop_player / picks.prop_market from pick_value.")
    parser.add_argument(
        "--db", required=True,
        help="Path to the SQLite database. REQUIRED -- no default, so this "
             "cannot hit production by accident. Use a copy first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts without writing anything.")
    args = parser.parse_args(argv)

    print(format_summary(run(args.db, dry_run=args.dry_run), args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

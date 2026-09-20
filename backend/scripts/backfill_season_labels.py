"""Recompute ``games.season`` for rows already stored.

Why this exists
---------------
The label was written by three sites in three formats -- ``"2026-2027"`` on
the ESPN path, ``"2026"`` on the odds path, ``"2026-27"`` in the backtesting
loader -- so the same season was recorded differently depending on which
collector created the row. ``config.season_label`` settled that, and 598
rows were relabelled by hand to match. By hand is the problem: restoring a
backup, or standing up a second environment, left no way to reproduce it.

It is needed a second time now. The label used to ask whether a date was
past the configured start, which reads right at the tail of a season and
wrong at its head -- an August nfl game, or an nba game in the fortnight
before a 10-22 start, landed a whole season early. Rows written under the
old rule carry that mistake.

How it decides
--------------
By recomputing, not by pattern-matching the old strings. ``season_label``
takes the sport, the date and ESPN's own season year where the row has one,
so this script asks it the same question the pipeline asks and compares the
answer to what is stored. A row that already agrees is counted and left
alone; nothing is parsed out of the existing label, which is exactly the
value that cannot be trusted.

``season_year`` is not a column -- it is read from the feed at collection
time and used to derive the label. This runs offline against what is
stored, so it recomputes from the date alone. That reproduces the stored
label for every row whose date sits inside its season window, which is all
of them bar preseason; where they differ the date is what the pipeline
would fall back to anyway for a row with no event.

``--dry-run`` writes nothing and needs no network. Prefer it first, and
back the database up before the real run, as every other script here says.
"""
import argparse
from collections import Counter, defaultdict

from backend.config import season_label, seasons_config
from backend.database import get_engine, get_session, run_migrations
from backend.models import Game


def run(db_path: str, *, dry_run: bool = False,
        sports: tuple[str, ...] | None = None,
        config_path: str = "config.yaml") -> dict:
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    seasons = seasons_config(config_path)
    per_sport: dict[str, Counter] = defaultdict(Counter)
    moves: dict[str, Counter] = defaultdict(Counter)
    try:
        query = session.query(Game)
        if sports:
            query = query.filter(Game.sport.in_(sports))
        rows = query.all()
        for game in rows:
            # `Game.date` is NOT NULL, so there is no dateless row to defend
            # against here.
            want = season_label(game.sport, game.date, seasons)
            if game.season == want:
                per_sport[game.sport]["already_correct"] += 1
                continue
            per_sport[game.sport]["changed"] += 1
            moves[game.sport][f"{game.season} -> {want}"] += 1
            if not dry_run:
                game.season = want
        if not dry_run:
            session.commit()
    finally:
        session.close()
    return {"rows": len(rows),
            "per_sport": {s: dict(c) for s, c in per_sport.items()},
            "moves": {s: dict(c) for s, c in moves.items()}}


def format_summary(summary: dict, dry_run: bool) -> str:
    lines = ["DRY RUN -- nothing written" if dry_run else "Relabel complete", ""]
    lines.append(f"  rows examined   : {summary['rows']}")
    if not summary["per_sport"]:
        lines.append("  no rows matched.")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"  {'sport':<8} {'changed':>8} {'correct':>8}")
    for sport, counts in sorted(summary["per_sport"].items()):
        lines.append(f"  {sport:<8} {counts.get('changed', 0):>8} "
                     f"{counts.get('already_correct', 0):>8}")
        for move, n in sorted(summary["moves"].get(sport, {}).items()):
            lines.append(f"      {move:<24} {n:>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute games.season from config.season_label."
    )
    parser.add_argument("--db", required=True,
                        help="Path to the database. Back it up first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change, but write nothing.")
    parser.add_argument("--sport", action="append", dest="sports",
                        help="Limit to a sport. Repeatable.")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to config.yaml (default: %(default)s).")
    args = parser.parse_args(argv)
    summary = run(args.db, dry_run=args.dry_run,
                  sports=tuple(args.sports) if args.sports else None,
                  config_path=args.config)
    print(format_summary(summary, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

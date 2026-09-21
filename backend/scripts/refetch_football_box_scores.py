"""Refill football game logs that were parsed against basketball's schema.

What went wrong
---------------
`espn_box_score.SPORT_PATHS` has listed nfl and ncaaf since the module was
written, but its label map only ever held basketball columns
(MIN/PTS/REB/AST/STL/BLK/TO/3PT). Football games were fetched, parsed and
stored all along -- as rows with every football column NULL. On 2026-09-20
production held 1,965 nfl and 19,112 ncaaf `game_log` rows with
`pass_yards`, `rush_yards`, `rec_yards`, `receptions` and `touchdowns`
non-null in exactly zero of them.

611 rows are not blank: they carry `points`, matched from the `kicking`
block's PTS column. Every one is a placekicker. That is the fingerprint
that identified the cause -- the parser was not failing, it was reading
football against the wrong sport's schema.

Consequence: `PropAnalyzer.analyze` needs `_MIN_VARIANCE_SAMPLES` usable
game-by-game values before it returns anything, so every football prop
returned None. The prop pipeline has generated no picks since 2026-05-25
while still buying the lines -- 6,188 of them on 2026-09-20 alone.

Why delete and recollect rather than patch in place
---------------------------------------------------
`collect_box_scores_for_final_games` skips any game that already has
`game_log` rows. That skip is what makes collection resumable without
tracking progress, and it is correct -- but it assumes "rows present"
means "collected", which the hollow rows violate. Restoring that
invariant lets the existing collector do the work unchanged. Filling the
rows in place would mean a second write path duplicating a collector that
already resolves event ids, carries team attribution, uses OUR game date
rather than ESPN's, and upserts.

Cost: one ESPN request per final game, and every football game in
production carries an `espn_id`, so there is no scoreboard search. 290
games as of 2026-09-20 (30 nfl, 260 ncaaf). Run one sport at a time --
ESPN publishes no rate limit and starts answering 403 once a client asks
quickly enough.

Dry run is the DEFAULT, as in `void_stuck_bouts` and `dedupe_combat_games`.
This deletes rows.

    python -m backend.scripts.refetch_football_box_scores --db <abs path>
    python -m backend.scripts.refetch_football_box_scores --db <abs path> --sport nfl --apply
"""
import argparse
import logging
import os

from backend.collectors.espn_box_score import collect_box_scores_for_final_games
from backend.database import get_engine, get_session, run_migrations
from backend.models import PlayerStat

logger = logging.getLogger(__name__)

#: The sports the broken parser produced hollow rows for. Taken from the
#: football entries in ``espn_box_score.SPORT_PATHS``; basketball parsed
#: correctly and must never be passed through this predicate.
FOOTBALL_SPORTS = ("nfl", "ncaaf")

#: A football `game_log` row is a measurement only if at least one of these
#: is populated. ``points`` is deliberately excluded: the 611 rows that have
#: it got it from the `kicking` block, and treating it as a football stat
#: would leave those games skipped forever.
FOOTBALL_FIELDS = ("pass_yards", "rush_yards", "rec_yards", "receptions",
                   "touchdowns")


def hollow_logs(session, sport: str) -> list[PlayerStat]:
    """Football ``game_log`` rows carrying no football statistic.

    Returns nothing for a sport outside :data:`FOOTBALL_SPORTS`. That guard
    is the whole safety of this module: a basketball row has no football
    field by definition, so running this predicate over nba would select
    all 27,936 of them.
    """
    if sport not in FOOTBALL_SPORTS:
        return []
    q = (session.query(PlayerStat)
         .filter(PlayerStat.sport == sport,
                 PlayerStat.stat_type == "game_log"))
    for field in FOOTBALL_FIELDS:
        q = q.filter(getattr(PlayerStat, field).is_(None))
    return q.all()


def run_on_session(session, *, sports: tuple[str, ...] = FOOTBALL_SPORTS,
                   apply: bool = False, collect: bool = True) -> dict:
    """Report, and with ``apply``, delete hollow rows and recollect.

    ``collect=False`` stops after the delete, for a run that wants to
    inspect the gap before spending 290 ESPN requests on it.
    """
    summary = {"hollow": 0, "deleted": 0, "refetched": 0, "sports": list(sports)}

    for sport in sports:
        rows = hollow_logs(session, sport)
        summary["hollow"] += len(rows)
        if not apply:
            continue

        for row in rows:
            session.delete(row)
        session.commit()
        summary["deleted"] += len(rows)
        logger.info("%s: deleted %d hollow game_log rows", sport, len(rows))

        if collect:
            # The collector commits internally, so it must not run during a
            # dry run -- see the module docstring.
            written = collect_box_scores_for_final_games(session, sport=sport)
            summary["refetched"] += written
            logger.info("%s: recollected %d rows", sport, written)

    return summary


def run(db_path: str, *, sports: tuple[str, ...] = FOOTBALL_SPORTS,
        apply: bool = False, collect: bool = True) -> dict:
    """CLI entry point.

    Raises ``FileNotFoundError`` rather than letting the engine create an
    empty database at a typo'd path and report a cheerful zero-row success.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")

    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return run_on_session(session, sports=sports, apply=apply, collect=collect)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Refilled football box scores" if apply
             else "DRY RUN -- nothing written", ""]
    lines.append(f"  sports              : {', '.join(s['sports'])}")
    lines.append(f"  {'deleted' if apply else 'hollow rows':<19} : "
                 f"{s['deleted'] if apply else s['hollow']}")
    if apply:
        lines.append(f"  recollected         : {s['refetched']}")
    lines.append("")
    lines.append("  A hollow row carries none of: " + ", ".join(FOOTBALL_FIELDS))
    lines.append("  It blocks its own refetch, because the collector skips")
    lines.append("  any game that already has game_log rows.")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Refill football box scores parsed against basketball's "
                    "schema. Dry run by default.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--sport", action="append", choices=list(FOOTBALL_SPORTS),
                    help="Limit to one sport. Repeatable. Default: both. "
                         "Prefer one at a time; ESPN throttles with 403.")
    ap.add_argument("--no-collect", action="store_true",
                    help="Delete the hollow rows without recollecting.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write. Without this it is a dry run.")
    args = ap.parse_args(argv)
    sports = tuple(args.sport) if args.sport else FOOTBALL_SPORTS
    print(format_summary(
        run(args.db, sports=sports, apply=args.apply,
            collect=not args.no_collect), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Remove `pitcher_skill_score` rows that are exactly 0.5.

Why they exist
---------------
Before plan 022, `scheduler.fetch_pitcher_scores_for_date` substituted 0.5
for a side with no announced starter, indistinguishable from a genuine
league-average pitcher (ERA exactly 4.00 AND K/9 exactly 8.5). No live
pitcher produces that pair by chance, so every stored row of exactly 0.5 is
an "unknown", not a measurement -- and it poisons the per-sport signal
report plan 021 built on this table: it cannot tell "two average starters"
from "we didn't know".

`fetch_pitcher_scores_for_date` now stores `None` (persisted as: nothing
written, see `scheduler._persist_pitcher_scores`) for an unknown side, so
this cleanup only touches rows written by the old code path.

Dry run is the DEFAULT and `--apply --i-have-a-backup` is required to
delete. **Take a `sqlite3 .backup` of the live database before running with
--apply** -- see `docs/sports-picks-db-snapshot.md` / the project memory
entry on db snapshots for why a plain file copy of a WAL-mode database is
not a valid backup.

    python -m backend.scripts.drop_unknown_pitcher_rows --db <abs path>
    python -m backend.scripts.drop_unknown_pitcher_rows --db <abs path> --apply --i-have-a-backup
"""
import argparse
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

from backend.database import get_engine, get_session, run_migrations
from backend.models import Game, Team, TeamStat

logger = logging.getLogger(__name__)

STAT_TYPE = "pitcher_skill_score"
EXACT_HALF = 0.5


@dataclass
class AffectedGame:
    game_id: int
    date: object
    home_abbr: str
    away_abbr: str
    sides: list[str] = field(default_factory=list)  # "home", "away", or both


def find_affected_games(session) -> list[AffectedGame]:
    """Games with one or both sides' pitcher_skill_score stored as exactly 0.5."""
    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == STAT_TYPE, TeamStat.value == EXACT_HALF).all()
    if not rows:
        return []

    game_ids = {r.game_id for r in rows}
    games = {g.id: g for g in session.query(Game).filter(Game.id.in_(game_ids))}
    team_ids = {r.team_id for r in rows} | {
        g.home_team_id for g in games.values()} | {
        g.away_team_id for g in games.values()}
    teams = {t.id: t.abbreviation for t in
             session.query(Team).filter(Team.id.in_(team_ids))}

    by_game: dict[int, list[TeamStat]] = defaultdict(list)
    for r in rows:
        by_game[r.game_id].append(r)

    out = []
    for game_id, stat_rows in by_game.items():
        game = games.get(game_id)
        if game is None:
            continue
        sides = []
        for r in stat_rows:
            if r.team_id == game.home_team_id:
                sides.append("home")
            elif r.team_id == game.away_team_id:
                sides.append("away")
        out.append(AffectedGame(
            game_id=game_id, date=game.date,
            home_abbr=teams.get(game.home_team_id, "?"),
            away_abbr=teams.get(game.away_team_id, "?"),
            sides=sorted(sides)))
    return sorted(out, key=lambda a: (a.date, a.game_id))


def run_on_session(session, *, apply: bool = False) -> dict:
    affected = find_affected_games(session)
    total_rows = sum(len(a.sides) for a in affected)
    summary = {"games": affected, "row_count": total_rows}

    if apply and affected:
        game_ids = [a.game_id for a in affected]
        session.query(TeamStat).filter(
            TeamStat.stat_type == STAT_TYPE,
            TeamStat.value == EXACT_HALF,
            TeamStat.game_id.in_(game_ids),
        ).delete(synchronize_session=False)
        session.commit()
    return summary


def run(db_path: str, *, apply: bool = False) -> dict:
    """CLI entry point.

    Raises ``FileNotFoundError`` if ``db_path`` does not exist: otherwise the
    engine would create an empty database at a typo'd path and report a
    cheerful zero-row success against it.
    """
    if db_path != ":memory:" and not os.path.exists(db_path):
        raise FileNotFoundError(
            f"--db {db_path!r} does not exist. Refusing to create a new, empty "
            f"database: a zero-row 'successful' run would hide the typo.")
    engine = get_engine(db_path)
    run_migrations(engine)
    session = get_session(engine)
    try:
        return run_on_session(session, apply=apply)
    finally:
        session.close()


def format_summary(s: dict, apply: bool) -> str:
    lines = ["Dropped ambiguous pitcher_skill_score rows" if apply
             else "DRY RUN -- nothing written", ""]
    if not s["games"]:
        lines.append("  No rows at exactly 0.5 found.")
        return "\n".join(lines)

    for a in s["games"]:
        both = len(a.sides) == 2
        lines.append(
            f"  game {a.game_id:<6} {a.date}  {a.home_abbr} vs {a.away_abbr}  "
            f"{'BOTH sides' if both else f'{a.sides[0]} side only'} at 0.5")
    lines.append("")
    lines.append(f"  games affected : {len(s['games'])}")
    lines.append(f"  {'deleted' if apply else 'would delete':<15}: {s['row_count']} row(s)")
    return "\n".join(lines)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="Delete TeamStat rows of stat_type='pitcher_skill_score' "
                    "with value exactly 0.5 -- ambiguous rows written before "
                    "plan 022, where 0.5 meant 'unknown starter', not a "
                    "measurement. Dry run by default. Take a `sqlite3 "
                    ".backup` of the live database before running --apply.")
    ap.add_argument("--db", required=True, help="Path to the database. Back it up first.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this it is a dry run.")
    ap.add_argument("--i-have-a-backup", action="store_true", dest="has_backup",
                    help="Required alongside --apply, confirming a "
                        "`sqlite3 .backup` snapshot was taken first.")
    args = ap.parse_args(argv)

    if args.apply and not args.has_backup:
        print("Refusing --apply without --i-have-a-backup. Take a "
              "`sqlite3 <db> \".backup 'backup.db'\"` snapshot first "
              "(a plain file copy of a WAL-mode database is not valid), "
              "then re-run with both flags.")
        return 1

    print(format_summary(run(args.db, apply=args.apply), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

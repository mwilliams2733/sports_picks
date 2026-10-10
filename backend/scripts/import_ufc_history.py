"""Load UFC fight history into the app and replay MMA Elo from it.

Owner, 2026-10-10 (docs/FINDINGS.md, MMA at 0.5): MMA fighters had no history
because since 2026-09-22 every MMA game arrives from the odds feed and nothing
loaded past fights, so the model priced every bout at 50%. This loads every
UFC bout from the UFCStats CSVs (github.com/Greco1899/scrape_ufc_stats) as a
final MMA game, then replays MMA Elo from seed with
`dedupe_combat_games.rebuild_combat_elo` -- the grader's own arithmetic.
`_build_fighter_stats` reads final games, so form, fight count and days off
come from the same rows.

Rules:
- A fighter is matched to an existing MMA Team by `name_key` (order, accents,
  punctuation and Jr./Sr. ignored); two existing rows with one key -> the
  lowest id, counted in `ambiguous_existing`. An unknown fighter gets a new
  Team row. Known limitation: two different fighters with the same name
  become one row (the CSV has names, not fighter ids).
- A bout whose two fighters already have a game within 1 day is skipped:
  one card can be stored under two date conventions, and a double-counted
  bout inflates Elo and form (the 2026-09-20 duplicate-bouts incident).
- Existing games, picks and results are never modified. Re-running inserts
  only bouts not yet loaded.

Dry run by default (prints, then rolls back); --apply commits. On the live db:
stop the scheduler and take a sqlite3.backup first.

    python -m backend.scripts.import_ufc_history --results <ufc_fight_results.csv> --events <ufc_event_details.csv> [--apply]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import or_

from backend.collectors.ufcstats_history import (HistoricalBout, name_key, read_bouts,
                                                 read_event_dates)
from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, Team
from backend.scripts.dedupe_combat_games import rebuild_combat_elo
from backend.time_utils import et_today

SAME_BOUT_DAYS = 1


def import_bouts(session, bouts: list[HistoricalBout], sport: str = "mma") -> dict:
    by_key: dict[str, int] = {}
    ambiguous = 0
    for team in session.query(Team).filter(Team.sport == sport).order_by(Team.id):
        key = name_key(team.name)
        if key in by_key:
            ambiguous += 1
            continue
        by_key[key] = team.id
    known: dict[frozenset, list[date]] = defaultdict(list)
    for g in session.query(Game).filter(Game.sport == sport):
        known[frozenset({g.home_team_id, g.away_team_id})].append(g.date)

    created = 0

    def team_id(name: str) -> int:
        nonlocal created
        key = name_key(name)
        if key not in by_key:
            team = Team(name=name, abbreviation=name, sport=sport)
            session.add(team)
            session.flush()
            by_key[key] = team.id
            created += 1
        return by_key[key]

    inserted = duplicates = same_fighter = 0
    for b in sorted(bouts, key=lambda b: b.date):
        if name_key(b.fighter_a) == name_key(b.fighter_b):
            same_fighter += 1
            continue
        home, away = team_id(b.fighter_a), team_id(b.fighter_b)
        pair = frozenset({home, away})
        if any(abs((b.date - d).days) <= SAME_BOUT_DAYS for d in known[pair]):
            duplicates += 1
            continue
        session.add(Game(sport=sport, season=str(b.date.year), date=b.date,
                         home_team_id=home, away_team_id=away, status="final",
                         home_score=b.a_score, away_score=b.b_score))
        known[pair].append(b.date)
        inserted += 1
    session.flush()
    return {"inserted": inserted, "duplicates": duplicates, "same_fighter": same_fighter,
            "teams_created": created, "ambiguous_existing": ambiguous}


def coverage(session, today: date, days: int = 30, sport: str = "mma") -> dict:
    """Scheduled bouts in the next ``days``: how many fighters have at least
    one final bout before the fight date (what `_build_fighter_stats` needs)."""
    games = (session.query(Game)
             .filter(Game.sport == sport, Game.status == "scheduled",
                     Game.date >= today, Game.date < today + timedelta(days=days)).all())

    def has_history(team_id: int, before: date) -> bool:
        return session.query(Game.id).filter(
            Game.sport == sport, Game.status == "final", Game.date < before,
            or_(Game.home_team_id == team_id, Game.away_team_id == team_id)).first() is not None

    with_history = both = 0
    for g in games:
        h, a = has_history(g.home_team_id, g.date), has_history(g.away_team_id, g.date)
        with_history += int(h) + int(a)
        both += int(h and a)
    return {"games": len(games), "fighters": 2 * len(games),
            "fighters_with_history": with_history, "games_both_known": both}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--db", help="database path (default: config.yaml database_path)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    db = args.db or load_config("config.yaml")["database_path"]
    session = get_session(get_engine(db))
    try:
        bouts, skipped = read_bouts(args.results, read_event_dates(args.events))
        print(f"parsed {len(bouts)} bouts {min(b.date for b in bouts)}..{max(b.date for b in bouts)}; "
              f"skipped {skipped}")
        today = et_today()
        print("coverage before:", coverage(session, today))
        print("import:", import_bouts(session, bouts))
        print("elo:", rebuild_combat_elo(session, "mma"))
        print("coverage after:", coverage(session, today))
        if args.apply:
            session.commit()
            print("applied")
        else:
            session.rollback()
            print("dry run: rolled back (use --apply)")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

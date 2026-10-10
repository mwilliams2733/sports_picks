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
- A bout whose two fighters (by name key, so a fighter split across two
  rows still matches) already have a FINAL game within ADJACENT_DAYS is
  skipped: one card can be stored under two date conventions, and a
  double-counted bout inflates Elo and form (the 2026-09-20 duplicate-bouts
  incident). A match on a game that is not final (stuck scheduled,
  canceled) is listed in `matched_non_final`. With `--finalize-unfinished`
  (owner, 2026-10-10: the 15 Mar-Jul bouts stuck scheduled/canceled), the
  ONE matching game is set final with the CSV result instead -- only when no
  model pick and no paper bet is attached, so no money or record moves.
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

from backend.collectors.ufc import normalize_name
from backend.collectors.ufcstats_history import (_SUFFIXES, HistoricalBout, fighter_key, read_bouts,
                                                 read_event_dates)
from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, PaperPick, PickModel, Team
from backend.scripts.dedupe_combat_games import ADJACENT_DAYS, rebuild_combat_elo
from backend.time_utils import et_today

#: One card can sit under two date conventions (dedupe_combat_games).
SAME_BOUT_DAYS = ADJACENT_DAYS


def name_words(name: str) -> frozenset[str]:
    """A name's words of two letters or more, Jr./Sr./II-IV and initials
    aside. Two spellings of one boxer share at least one: "T. J. Doheny" /
    "TJ Doheny", "Christopher Gurrero" / "Christopher Guerrero", "Rene
    Osvaldo Palacios Galvan" / "Rene Palacios"."""
    return frozenset(w for w in normalize_name(name).split()
                     if len(w) >= 2 and w not in _SUFFIXES)


def import_bouts(session, bouts: list[HistoricalBout], sport: str = "mma",
                 finalize_unfinished: bool = False) -> dict:
    by_key: dict[str, int] = {}
    key_of: dict[int, str] = {}
    ambiguous = 0
    for team in session.query(Team).filter(Team.sport == sport).order_by(Team.id):
        key = fighter_key(sport, team.name)
        key_of[team.id] = key
        if key in by_key:
            ambiguous += 1
            continue
        by_key[key] = team.id
    # Keyed on the fighters' NAME keys, not row ids: an odds-feed row and a
    # history row for one fighter are one fighter here.
    known: dict[frozenset, list[tuple[date, int]]] = defaultdict(list)
    status_of: dict[int, str] = {}
    # Boxing only: each fighter's bouts as (date, opponent key, opponent
    # words, game id), to catch one bout stored under two spellings of a
    # name -- a different name PAIR, which `known` cannot see (final review
    # 2026-10-10: "T. J. Doheny" / "TJ Doheny", ~52 bouts doubled; the feed's
    # "Jermaine Franklin Jr" v Itauma left canceled beside a final twin).
    by_fighter: dict[str, list[tuple[date, str, frozenset, int]]] = defaultdict(list)
    name_of = {t.id: t.name for t in session.query(Team).filter(Team.sport == sport)}

    def remember(gid, day, a_key, a_name, b_key, b_name, status):
        known[frozenset({a_key, b_key})].append((day, gid))
        status_of[gid] = status
        if sport == "boxing":
            by_fighter[a_key].append((day, b_key, name_words(b_name), gid))
            by_fighter[b_key].append((day, a_key, name_words(a_name), gid))

    for g in session.query(Game).filter(Game.sport == sport):
        remember(g.id, g.date, key_of.get(g.home_team_id), name_of.get(g.home_team_id, ""),
                 key_of.get(g.away_team_id), name_of.get(g.away_team_id, ""), g.status)

    created = 0

    def team_id(name: str) -> int:
        nonlocal created
        key = fighter_key(sport, name)
        if key not in by_key:
            team = Team(name=name, abbreviation=name, sport=sport)
            session.add(team)
            session.flush()
            by_key[key] = team.id
            key_of[team.id] = key
            created += 1
        return by_key[key]

    inserted = duplicates = same_fighter = name_variants = 0
    non_final: list[int] = []
    finalized: list[int] = []

    def has_bets(game_id: int) -> bool:
        return (session.query(PickModel.id).filter(PickModel.game_id == game_id).first() is not None
                or session.query(PaperPick.id).filter(PaperPick.game_id == game_id).first() is not None)
    for b in sorted(bouts, key=lambda b: b.date):
        a_key, b_key = fighter_key(sport, b.fighter_a), fighter_key(sport, b.fighter_b)
        if a_key == b_key:
            same_fighter += 1
            continue
        pair = frozenset({a_key, b_key})
        near = sorted({gid for d, gid in known[pair] if abs((b.date - d).days) <= SAME_BOUT_DAYS})
        variant = False
        if not near and sport == "boxing":
            near = sorted({gid for me, other, other_name in ((a_key, b_key, b.fighter_b),
                                                             (b_key, a_key, b.fighter_a))
                           for d, opp_key, opp_words, gid in by_fighter[me]
                           if abs((b.date - d).days) <= SAME_BOUT_DAYS and opp_key != other
                           and opp_words & name_words(other_name)})
            variant = bool(near)
        if any(status_of[gid] == "final" for gid in near):
            duplicates += 1
            name_variants += variant
            continue
        if near:
            if finalize_unfinished and len(near) == 1 and not has_bets(near[0]):
                game = session.get(Game, near[0])
                a_home = (key_of.get(game.home_team_id) == a_key
                          or key_of.get(game.away_team_id) == b_key)
                game.home_score, game.away_score = ((b.a_score, b.b_score) if a_home
                                                    else (b.b_score, b.a_score))
                game.status = "final"
                status_of[game.id] = "final"
                finalized.append(game.id)
                continue
            non_final.extend(near)
            continue
        home, away = team_id(b.fighter_a), team_id(b.fighter_b)
        game = Game(sport=sport, season=str(b.date.year), date=b.date,
                    home_team_id=home, away_team_id=away, status="final",
                    home_score=b.a_score, away_score=b.b_score)
        session.add(game)
        session.flush()
        remember(game.id, b.date, a_key, b.fighter_a, b_key, b.fighter_b, "final")
        inserted += 1
    session.flush()
    return {"inserted": inserted, "duplicates": duplicates, "name_variants": name_variants,
            "same_fighter": same_fighter,
            "teams_created": created, "ambiguous_existing": ambiguous,
            "matched_non_final": sorted(set(non_final)),
            "finalized": sorted(set(finalized))}


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
    parser.add_argument("--finalize-unfinished", action="store_true",
                        help="set a matched stuck (scheduled/canceled) game final from the CSV, "
                             "only if it has no pick or paper bet")
    args = parser.parse_args(argv)
    db = args.db or load_config("config.yaml")["database_path"]
    session = get_session(get_engine(db))
    try:
        bouts, skipped = read_bouts(args.results, read_event_dates(args.events))
        print(f"parsed {len(bouts)} bouts {min(b.date for b in bouts)}..{max(b.date for b in bouts)}; "
              f"skipped {skipped}")
        today = et_today()
        print("coverage before:", coverage(session, today))
        print("import:", import_bouts(session, bouts, finalize_unfinished=args.finalize_unfinished))
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

# UFC Fight History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load every UFC bout since 1994 into the app as completed fights, replay MMA fighter ratings from them, and stop MMA picks on any fight where a fighter still has no history — so MMA picks are priced on real records instead of the no-information 50%.

**Architecture:** A pure parser (`backend/collectors/ufcstats_history.py`) reads the two UFCStats CSVs published by github.com/Greco1899/scrape_ufc_stats. An importer script (`backend/scripts/import_ufc_history.py`) matches fighter names to our MMA `Team` rows (order-, accent- and suffix-insensitive), creates rows for fighters we have never seen, inserts each bout as a `final` MMA `Game` (scores 1/0, draws 1/1) unless the same two fighters already have a game within a day, then replays MMA Elo from seed with the existing `dedupe_combat_games.rebuild_combat_elo`. Everything downstream — `_build_fighter_stats` (form, fight count, days off) and the Elo — already reads final `Game` rows, so no reader changes. `CombatSportsStrategy` applies its boxing "both fighters need history" gate to MMA too.

**Tech Stack:** Python 3.11, SQLAlchemy/SQLite, pytest.

**Spec:** No separate spec. The design is `docs/FINDINGS.md` § "2026-10-09 — MMA picks at model_prob 0.5" (option 2) plus the owner's decisions of 2026-10-10, recorded verbatim below. They are binding.

## Owner decisions (2026-10-10)

- Load UFC history from the public CSVs of github.com/Greco1899/scrape_ufc_stats (GPL-3.0, refreshed daily from UFCStats.com). Downloading `ufc_fight_results.csv` and `ufc_event_details.csv` at build and merge time: **approved.**
- After the load, MMA **requires history for both fighters** before it makes a pick (the boxing gate) — a fighter with no history then usually means "not in the UFC data", and pricing a known fighter against the 1500 seed would publish a built-in bias as edge.
- Boxing history: **later, a separate plan.** Not in scope.

## Global Constraints

- The history goes in as `games` rows only; existing `games`, `picks`, `pick_results`, `emailed_picks` and `paper_picks` rows are never modified or deleted by the importer. Only MMA `elo_ratings` / `elo_history` are rebuilt (from seed, chronologically, via `rebuild_combat_elo` — never a second copy of the Elo arithmetic).
- Combat result convention (grader `_apply_combat_elo_update`): winner 1, loser 0; a draw is 1/1. No-contests and unknown results are not loaded.
- A bout already stored (same two fighters, dates within 1 day — one card can sit under two date conventions, memory: mma duplicate bouts) is never inserted again. Rematches on other dates are real and kept.
- The importer is a dry run by default and needs `--apply`; on the live db it runs with the scheduler stopped and after a `sqlite3.backup` (never `cp`, WAL).
- Downloaded files are data: saved into their own new directory, read with `csv`, never executed. Pass paths as arguments.
- Backend tests: from the repo root (a worktree uses the MAIN venv `/c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python`) `... -m pytest <file> -q -p no:cacheprovider`. Delete `__pycache__` after every mutation write and restore.
- Write regex/backslash code with the Write/Edit tools, never bash heredocs.
- Commit only named files; never `config.yaml`. Trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A bout we already have from the odds feed**, stored in mirrored corner order and/or one day off (date convention): never inserted twice (a double-counted bout inflates Elo and form — the 2026-09-20 duplicate-bouts incident). Pinned in Task 2.
2. **The same fighter under different spellings** ("Wang Cong" vs "Cong Wang", "José Aldo" vs "Jose Aldo", "Rosas Jr." vs "Rosas"): one fighter, one Team row; two existing Team rows with the same key: the lowest id is used and the count reported. Pinned in Tasks 1 and 2.
3. **Draws, no-contests, bad rows** (D/D, NC/NC, a BOUT without " vs. ", an event with no date or two dates): draw 1/1, the rest skipped and counted, never guessed. Pinned in Task 1.
4. **Running the importer twice** (a later refresh): the second run inserts nothing already loaded. Pinned in Task 2.
5. **An MMA fight where one or both fighters have no history** after the load: no pick (owner decision), while a fight with history on both sides still gets one. Pinned in Task 3.

Known limitation, accepted: the results CSV names fighters but carries no fighter id, so two different fighters with the same name (UFC has a few, e.g. two "Bruno Silva"s) become one Team row. This is not detected or corrected; Task 4 documents it in FINDINGS and the data dictionary, and the merge report states it.

---

### Task 1: Parse the UFCStats CSVs

**Files:**
- Create: `backend/collectors/ufcstats_history.py`
- Create: `backend/tests/test_ufcstats_history.py`

**Interfaces:**
- Consumes: `backend.collectors.ufc.normalize_name(name) -> str` (casefold, strip accents and punctuation, hyphen -> space).
- Produces: `name_key(name: str) -> str`; `@dataclass(frozen=True) HistoricalBout(date, event, fighter_a, fighter_b, a_score, b_score)`; `read_event_dates(path) -> dict[str, date | None]` (None = the event name has two different dates); `read_bouts(results_path, event_dates) -> tuple[list[HistoricalBout], dict[str, int]]` (skip counts keyed `no_result`, `event_date_unknown`, `bad_bout`).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_ufcstats_history.py`:

```python
"""UFCStats history CSVs (github.com/Greco1899/scrape_ufc_stats) -> bouts."""
from datetime import date

from backend.collectors.ufcstats_history import (HistoricalBout, name_key, read_bouts,
                                                 read_event_dates)

EVENTS = (
    "﻿EVENT,URL,DATE,LOCATION\n"
    'UFC 332: Silva vs. Wang,http://x/e1,"October 03, 2026","Salt Lake City, Utah, USA"\n'
    'UFC Fight Night: A vs. B,http://x/e2,"September 26, 2026","Las Vegas, Nevada, USA"\n'
    'UFC Fight Night: Twice,http://x/e3,"May 01, 2010","X"\n'
    'UFC Fight Night: Twice,http://x/e4,"June 05, 2012","Y"\n'
)
RESULTS = (
    "EVENT,BOUT,OUTCOME,WEIGHTCLASS,METHOD,ROUND,TIME,TIME FORMAT,REFEREE,DETAILS,URL\n"
    "UFC 332: Silva vs. Wang,Natalia Silva vs. Wang Cong,W/L,Flyweight,Decision,5,5:00,f,r,d,u1\n"
    "UFC 332: Silva vs. Wang,Deiveson Figueiredo vs. Payton Talbott,L/W,Bantam,KO/TKO,1,2:09,f,r,d,u2\n"
    "UFC Fight Night: A vs. B,Draw One vs. Draw Two,D/D,Light,Decision,3,5:00,f,r,d,u3\n"
    "UFC Fight Night: A vs. B,Nc One vs. Nc Two,NC/NC,Light,Overturned,3,5:00,f,r,d,u4\n"
    "UFC Fight Night: Twice,Old One vs. Old Two,W/L,Light,Decision,3,5:00,f,r,d,u5\n"
    "UFC 999: Missing,Gone One vs. Gone Two,W/L,Light,Decision,3,5:00,f,r,d,u6\n"
    "UFC 332: Silva vs. Wang,No Separator Here,W/L,Light,Decision,3,5:00,f,r,d,u7\n"
)


def _files(tmp_path):
    e, r = tmp_path / "events.csv", tmp_path / "results.csv"
    e.write_text(EVENTS, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    return e, r


def test_name_key_ignores_order_accents_punctuation_and_suffixes():   # Review Focus 2
    assert name_key("Wang Cong") == name_key("Cong Wang")
    assert name_key("José Aldo") == name_key("Jose Aldo")
    assert name_key("Alexander Hernandez Jr.") == name_key("alexander hernandez")
    assert name_key("Abdul-Kareem Al-Selwady") == name_key("Abdul Kareem Al Selwady")
    assert name_key("Jon Jones") != name_key("Jon Jones Smith")


def test_event_dates_and_an_ambiguous_event_name(tmp_path):
    events, _ = _files(tmp_path)
    dates = read_event_dates(events)
    assert dates["UFC 332: Silva vs. Wang"] == date(2026, 10, 3)
    assert dates["UFC Fight Night: Twice"] is None          # two dates: never guessed


def test_bouts_wins_losses_draws_and_skips(tmp_path):              # Review Focus 3
    events, results = _files(tmp_path)
    bouts, skipped = read_bouts(results, read_event_dates(events))
    assert bouts == [
        HistoricalBout(date(2026, 10, 3), "UFC 332: Silva vs. Wang", "Natalia Silva", "Wang Cong", 1, 0),
        HistoricalBout(date(2026, 10, 3), "UFC 332: Silva vs. Wang", "Deiveson Figueiredo", "Payton Talbott", 0, 1),
        HistoricalBout(date(2026, 9, 26), "UFC Fight Night: A vs. B", "Draw One", "Draw Two", 1, 1),
    ]
    assert skipped == {"no_result": 1, "event_date_unknown": 2, "bad_bout": 1}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_ufcstats_history.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: backend.collectors.ufcstats_history`.

- [ ] **Step 3: Implement**

`backend/collectors/ufcstats_history.py`:

```python
"""UFC fight history from the UFCStats CSVs published by
github.com/Greco1899/scrape_ufc_stats (GPL-3.0, refreshed daily).

`ufc_event_details.csv` (EVENT, URL, DATE "October 03, 2026", LOCATION) gives
each event's date; `ufc_fight_results.csv` (EVENT, BOUT "A vs. B", OUTCOME
"W/L" | "L/W" | "D/D" | "NC/NC", ...) gives each bout. Parsing only: the
importer (backend/scripts/import_ufc_history.py) decides what reaches the db.

Scores follow the grader's combat convention: winner 1, loser 0, draw 1/1.
A no-contest (or any other outcome) is skipped, never guessed; so is a bout
whose event has no date, or two different dates under one name.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.collectors.ufc import normalize_name

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_OUTCOMES = {"W/L": (1, 0), "L/W": (0, 1), "D/D": (1, 1)}


def name_key(name: str) -> str:
    """A fighter's name as an order-free key: "Wang Cong" == "Cong Wang";
    accents, punctuation, hyphens and Jr./Sr./II-IV are ignored."""
    tokens = [t for t in normalize_name(name).split() if t not in _SUFFIXES]
    return " ".join(sorted(tokens))


@dataclass(frozen=True)
class HistoricalBout:
    date: date
    event: str
    fighter_a: str
    fighter_b: str
    a_score: int
    b_score: int


def read_event_dates(path: str | Path) -> dict[str, date | None]:
    """Event name -> date; None when one name carries two different dates."""
    seen: dict[str, set[date]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["EVENT"].strip()
            day = datetime.strptime(row["DATE"].strip(), "%B %d, %Y").date()
            seen.setdefault(name, set()).add(day)
    return {name: (next(iter(days)) if len(days) == 1 else None) for name, days in seen.items()}


def read_bouts(results_path: str | Path,
               event_dates: dict[str, date | None]) -> tuple[list[HistoricalBout], dict[str, int]]:
    skipped = {"no_result": 0, "event_date_unknown": 0, "bad_bout": 0}
    bouts: list[HistoricalBout] = []
    with open(results_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            scores = _OUTCOMES.get(row["OUTCOME"].strip())
            if scores is None:
                skipped["no_result"] += 1
                continue
            event = row["EVENT"].strip()
            day = event_dates.get(event)
            if day is None:
                skipped["event_date_unknown"] += 1
                continue
            a, sep, b = row["BOUT"].partition(" vs. ")
            if not sep or not a.strip() or not b.strip():
                skipped["bad_bout"] += 1
                continue
            bouts.append(HistoricalBout(day, event, a.strip(), b.strip(), *scores))
    return bouts, skipped
```

- [ ] **Step 4: Run them**

Run: `.venv/Scripts/python -m pytest backend/tests/test_ufcstats_history.py -q -p no:cacheprovider`
Expected: PASS (3).

- [ ] **Step 5: Mutation checks**

1. `name_key` without `sorted(...)` (keep token order) → the name test FAILS. Restore.
2. `_OUTCOMES` without `"D/D"` → the bouts test FAILS (draw missing, no_result 2). Restore.
Delete `backend/collectors/__pycache__` after each write and restore.

- [ ] **Step 6: Commit**

```bash
git add backend/collectors/ufcstats_history.py backend/tests/test_ufcstats_history.py
git commit -m "feat(mma): parse UFCStats fight history CSVs into bouts (draws kept, no-contests skipped)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Import the bouts and replay MMA Elo

**Files:**
- Create: `backend/scripts/import_ufc_history.py`
- Create: `backend/tests/test_import_ufc_history.py`

**Interfaces:**
- Consumes: Task 1's `HistoricalBout`, `name_key`, `read_event_dates`, `read_bouts`; `backend.scripts.dedupe_combat_games.rebuild_combat_elo(session, sport) -> {"sport", "bouts_replayed"}`.
- Produces: `import_bouts(session, bouts, sport="mma") -> dict` with keys `inserted, duplicates, same_fighter, teams_created, ambiguous_existing`; `coverage(session, today: date, days: int = 30, sport="mma") -> dict` with `games, fighters, fighters_with_history, games_both_known`; `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_import_ufc_history.py`:

```python
"""UFC history goes in as final MMA games; Elo is replayed from them."""
from datetime import date

from backend.collectors.ufcstats_history import HistoricalBout
from backend.models import Base, EloRating, Game, Team
from backend.scripts.dedupe_combat_games import rebuild_combat_elo
from backend.scripts.import_ufc_history import coverage, import_bouts

D = date(2026, 9, 26)


def _team(session, tid, name):
    session.add(Team(id=tid, name=name, abbreviation=name, sport="mma"))


def _seed(session):
    _team(session, 1, "Cong Wang")            # odds-feed order; CSV says "Wang Cong"
    _team(session, 2, "Natalia Silva")
    _team(session, 3, "Payton Talbott")
    _team(session, 4, "Deiveson Figueiredo")
    _team(session, 9, "Deiveson Figueiredo")  # a duplicate row: lowest id (4) is used
    session.flush()
    # The odds feed already has Silva-Wang, mirrored and one day later.
    session.add(Game(sport="mma", season="2026", date=date(2026, 10, 4), status="final",
                     home_team_id=1, away_team_id=2, home_score=0, away_score=1))
    # An upcoming card: Talbott (will have history) vs a newcomer (none).
    _team(session, 5, "Brand New")
    session.flush()
    session.add(Game(sport="mma", season="2026", date=date(2026, 10, 17), status="scheduled",
                     home_team_id=3, away_team_id=5))
    session.commit()


BOUTS = [
    HistoricalBout(date(2026, 10, 3), "UFC 332", "Natalia Silva", "Wang Cong", 1, 0),
    HistoricalBout(date(2026, 10, 3), "UFC 332", "Deiveson Figueiredo", "Payton Talbott", 0, 1),
    HistoricalBout(D, "UFC FN", "Payton Talbott", "Someone Else", 1, 1),
    HistoricalBout(D, "UFC FN", "Same Guy", "Same Guy", 1, 0),
]


def test_import_matches_names_skips_known_bouts_and_creates_new_fighters(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    summary = import_bouts(db_session, BOUTS)
    assert summary == {"inserted": 2, "duplicates": 1, "same_fighter": 1,       # Review Focus 1
                       "teams_created": 1, "ambiguous_existing": 1}           # Review Focus 2
    figs = db_session.query(Game).filter(Game.date == date(2026, 10, 3)).one()
    assert (figs.home_team_id, figs.away_team_id, figs.home_score, figs.away_score, figs.status) == (
        4, 3, 0, 1, "final")
    draw = db_session.query(Game).filter(Game.date == D).one()
    assert (draw.home_score, draw.away_score) == (1, 1)
    assert db_session.query(Team).filter(Team.name == "Someone Else").count() == 1


def test_a_second_run_inserts_nothing(db_engine, db_session):                   # Review Focus 4
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    import_bouts(db_session, BOUTS)
    db_session.commit()
    again = import_bouts(db_session, BOUTS)
    assert again["inserted"] == 0 and again["teams_created"] == 0


def test_elo_replays_from_the_imported_bouts_and_coverage_is_measured(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    _seed(db_session)
    before = coverage(db_session, date(2026, 10, 10))
    assert before == {"games": 1, "fighters": 2, "fighters_with_history": 0, "games_both_known": 0}
    import_bouts(db_session, BOUTS)
    rebuild_combat_elo(db_session, "mma")
    ratings = {r.team_id: r.rating for r in db_session.query(EloRating).filter(EloRating.sport == "mma")}
    assert ratings[3] > 1500 > ratings[4]                 # Talbott beat Figueiredo
    after = coverage(db_session, date(2026, 10, 10))
    assert after == {"games": 1, "fighters": 2, "fighters_with_history": 1, "games_both_known": 0}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest backend/tests/test_import_ufc_history.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: backend.scripts.import_ufc_history`.

- [ ] **Step 3: Implement**

`backend/scripts/import_ufc_history.py`:

```python
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
        print(f"parsed {len(bouts)} bouts {min(b.date for b in bouts)}..{max(b.date for b in bouts)}; skipped {skipped}")
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
```

- [ ] **Step 4: Run them**

Run: `.venv/Scripts/python -m pytest backend/tests/test_import_ufc_history.py backend/tests/test_ufcstats_history.py backend/tests/test_dedupe_combat_games.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Mutation checks**

1. `SAME_BOUT_DAYS = 1` → `0` → the import test FAILS (Silva-Wang inserted twice: inserted 3, duplicates 0). Restore.
2. In `import_bouts`, drop the `if key in by_key: ambiguous += 1; continue` guard (later rows overwrite) → the import test FAILS (Figueiredo maps to 9). Restore.
Delete `backend/scripts/__pycache__` after each write and restore.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/import_ufc_history.py backend/tests/test_import_ufc_history.py
git commit -m "feat(mma): import UFC fight history as final games and replay MMA Elo from it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: MMA needs history for both fighters (owner decision)

**Files:**
- Modify: `backend/analysis/variants/combat_sports.py:34-41`
- Modify: `backend/tests/test_combat_sports_strategy.py` (replace `test_mma_strategy_still_picks_for_debut_fighters`)

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_combat_sports_strategy.py`, replace the whole function `test_mma_strategy_still_picks_for_debut_fighters` with:

```python
def test_mma_strategy_skips_when_either_fighter_has_no_history():   # Review Focus 5
    """Owner, 2026-10-10: once UFC history is loaded, an MMA fighter with no
    fights usually means "not in the UFC data", not a debut -- and pricing a
    known fighter against the 1500 seed is a built-in bias, not an edge. So
    MMA has the boxing gate too."""
    home = FighterStats(elo_rating=1700, recent_form_score=0.8,
                       opponent_avg_elo=1500, fights_count=15, days_since_last_fight=120)
    away = FighterStats(elo_rating=1500, recent_form_score=0.5,
                       opponent_avg_elo=None, fights_count=0, days_since_last_fight=None)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=-150, moneyline_away=+130,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    assert strat.predict(_fight_data(home, away, odds, sport="mma")) == []


def test_mma_strategy_picks_when_both_fighters_have_history():      # Review Focus 5
    home = FighterStats(elo_rating=1750, recent_form_score=0.85,
                       opponent_avg_elo=1600, fights_count=20, days_since_last_fight=150)
    away = FighterStats(elo_rating=1500, recent_form_score=0.50,
                       opponent_avg_elo=1480, fights_count=10, days_since_last_fight=200)
    odds = OddsSnapshot(bookmaker="dk", moneyline_home=+100, moneyline_away=-120,
                        spread_home=0.0, spread_away=0.0, over_under=0.0)
    strat = CombatSportsStrategy(name="combat", config={"min_edge": 3.0})
    picks = strat.predict(_fight_data(home, away, odds, sport="mma"))
    assert len(picks) == 1 and picks[0].pick_type == "moneyline"
```

Run: `.venv/Scripts/python -m pytest backend/tests/test_combat_sports_strategy.py -q -p no:cacheprovider`
Expected: FAIL — `test_mma_strategy_skips_when_either_fighter_has_no_history` gets a pick.

- [ ] **Step 2: Implement**

In `backend/analysis/variants/combat_sports.py`, replace the block

```python
        # Boxing data-thin gate: Wikidata only covers top-tier boxers, so a
        # missing fight history likely means "fighter unknown to us" rather
        # than "genuine debut." MMA bypasses this gate — UFCStats coverage
        # is comprehensive enough that fights_count=0 is a real debut signal.
        if game.sport == "boxing" and (
            home_fighter.fights_count == 0 or away_fighter.fights_count == 0
        ):
            return []
```

with

```python
        # Data-thin gate: no pick unless BOTH fighters have fight history.
        # Boxing: Wikidata covers only top-tier boxers. MMA (owner,
        # 2026-10-10): history comes from the UFCStats import, so a fighter
        # with no fights usually means "not in the UFC data", and pricing a
        # known fighter against the 1500 seed would publish a built-in bias
        # as edge (docs/FINDINGS.md, MMA picks at 0.5).
        if game.sport in ("boxing", "mma") and (
            home_fighter.fights_count == 0 or away_fighter.fights_count == 0
        ):
            return []
```

Run: `.venv/Scripts/python -m pytest backend/tests/test_combat_sports_strategy.py backend/tests/test_combat_integration.py -q -p no:cacheprovider`
Expected: PASS. If `test_combat_integration.py` relied on an MMA debut pick, rule on it in the ledger (the owner decision is binding) and update its expectation, naming the test.

- [ ] **Step 3: Mutation check**

`("boxing", "mma")` → `("boxing",)` → the MMA skip test FAILS. Restore; delete `backend/analysis/variants/__pycache__`.

- [ ] **Step 4: Full suite and commit**

Run: `.venv/Scripts/python -m pytest -q -p no:cacheprovider` → all pass (record the count).

```bash
git add backend/analysis/variants/combat_sports.py backend/tests/test_combat_sports_strategy.py
git commit -m "fix(mma): no pick unless both fighters have history (owner decision, as boxing)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Dry run on real data, measure, document

**Files:**
- Modify: `docs/FINDINGS.md` (append a dated section)
- Modify: `docs/data-dictionary.md` (games: imported UFC history; MMA gate)

- [ ] **Step 1: Download the CSVs (owner-approved) into a new, empty directory**

```bash
D=<scratchpad>/ufcstats-$(date +%Y%m%d) && mkdir "$D" && \
curl -sSfL -o "$D/ufc_fight_results.csv" https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fight_results.csv && \
curl -sSfL -o "$D/ufc_event_details.csv" https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_event_details.csv && \
wc -l "$D"/*.csv && head -2 "$D"/*.csv
```

Expected: results ~8,000+ lines with header `EVENT,BOUT,OUTCOME,...`; events ~750+ lines with `EVENT,URL,DATE,LOCATION`. A different header → stop and rule (the parser keys on these names).

- [ ] **Step 2: Dry run against a snapshot**

Snapshot the live db with `sqlite3.backup` to `<scratchpad>/uh.db`, then from the worktree root:

`.venv/Scripts/python -m backend.scripts.import_ufc_history --results "$D/ufc_fight_results.csv" --events "$D/ufc_event_details.csv" --db <scratchpad>/uh.db --apply`

(--apply on the SNAPSHOT only.) Record: bouts parsed and date range; skip counts; `import` summary (inserted, duplicates, teams_created, ambiguous_existing); `elo` bouts replayed; coverage before -> after.

- [ ] **Step 3: Sanity checks on the snapshot (read-only queries)**

- Top 10 MMA Elo ratings with names: they should be recognisable elite fighters (champions/contenders). A list of unknowns means the replay or the matching is wrong — stop and debug.
- The 2026-09-22+ odds-feed games: none duplicated (count MMA (date, unordered pair) groups with >1 row within 1 day — must be 0 new ones).
- The fighters of the next 30 days' scheduled MMA games with NO history after import: list their names (these are the non-UFC / unmatched ones). Spot-check 5 against the CSV for a missed match (e.g. a spelling the key does not cover); if a pattern appears, rule on it in the ledger rather than silently adding aliases.
- Regenerate MMA picks on the snapshot for the next card (`generate_and_store_picks` for the MMA/combat strategy and the card's date) and report how many picks result and their model_prob spread — none should be exactly 0.5 any more (the gate removes no-history fights; the stopgap would catch the rest).

- [ ] **Step 4: Write it down**

Append to `docs/FINDINGS.md` a section `## 2026-10-10 — UFC history import (dry run on a snapshot)` with every number from Steps 2-3, the date of the CSV refresh, the known same-name limitation, and what was not checked.

In `docs/data-dictionary.md`, under the games/combat notes, add:

```
- **From 2026-10-10, `games` holds UFC history for sport `mma`** (UFCStats via
  github.com/Greco1899/scrape_ufc_stats): every UFC bout since 1994 as a `final`
  game, winner 1 / loser 0, draws 1/1, no-contests not loaded. These rows have
  no `espn_id`, no `odds_api_id`, no start time and no odds, and no picks were
  ever made on them. MMA Elo (`elo_ratings`, `elo_history`) was replayed from
  seed over them. Fighters are matched by name only, so two fighters sharing a
  name share a row. From the same date MMA, like boxing, makes no pick unless
  both fighters have at least one earlier final bout.
```

- [ ] **Step 5: Commit**

```bash
git add docs/FINDINGS.md docs/data-dictionary.md
git commit -m "docs: UFC history import measured on a snapshot; data dictionary for imported MMA games and the MMA gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Merge notes (for "merge", not the build)

1. Check ET and that no MMA card is in progress; save `scheduler.log` aside.
2. Stop the scheduler (both processes). The app server can stay up (the import writes only games/teams/Elo).
3. `sqlite3.backup` the live db -> `sports_picks.backup-<stamp>-pre-ufc-history.db`.
4. `git merge --no-ff feat/ufc-fight-history`.
5. Download fresh CSVs into a new directory (Task 4 Step 1). Dry run on the live db (no `--apply`) and compare its numbers with the snapshot run; then run with `--apply`.
6. Restart the scheduler detached (`Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\start_scheduler.ps1' -WindowStyle Hidden`) — it loads the new gate. Restart uvicorn only if a backend import path it serves changed (none here).
7. Verify: MMA EloRating count, top-10 names, coverage line, scheduler log clean. The next MMA window's picks should carry real probabilities.

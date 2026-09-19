# ncaab Team Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every ncaab team row an ESPN-matchable `abbreviation`, so the 59
stranded ncaab games can be identified, finalised and graded — and stop the
pipeline creating new display-name rows.

**Architecture:** One pure, offline resolver (`backend/team_identity.py`) backed
by a committed snapshot of ESPN's 362-team table. Both the one-off repair script
and the live odds pipeline call that same resolver, so the mapping has exactly
one definition. The repair reclassifies each bad row as a *rename* (no existing
row owns the target abbreviation) or a *merge* (one does), repoints every
foreign key discovered from the schema, and then resolves any duplicate games
the merge creates.

**Tech Stack:** Python 3.11+ (prod runs 3.12), SQLAlchemy 2.0, sqlite3, pytest,
pytest-httpx, httpx.

**Spec:** No separate spec doc. The measured basis for this plan is recorded in
`plans/HANDOFF.md` and reproduced under "Measured starting state" below; the
surveys that produced it are throwaway scripts from the investigating session.

---

## Global Constraints

- **Never write to `sports_picks.db` without a fresh backup** taken after
  `PRAGMA wal_checkpoint(TRUNCATE)`. The backup is the only rollback.
- **Stop the scheduler before any production write.** It holds the same file
  and `morning_scout` runs 08:00 ET.
- `abbreviation` is the **in-memory Elo replay key** (`team_stats.py:362`,
  `:382-383`). `EloHistory` and `EloRating` persist `team_id`, so a rename
  cannot corrupt stored Elo — but two rows sharing an abbreviation would
  silently merge two schools in the replay. Uniqueness of `(sport,
  abbreviation)` is the invariant this plan must establish and not break.
- `abbreviation` is also the box-score join key (`espn_box_score.py:225`).
- Secrets live in `C:\Users\mwill\.secrets\shared.env`; reference by name,
  never hardcode or echo a value.
- No network calls in unit tests. The ESPN snapshot is committed; the only
  code allowed to fetch is the regeneration script, which is not run by tests.

## Measured starting state

Measured 2026-09-18 against production (`sports_picks.db`, 1713 games):

| | count |
|---|---|
| ncaab team rows | 147 |
| — holding a display name in `abbreviation` | **78** |
| — already correct | 69 |
| ncaab games | 120 |
| — touching a display-name row | **59** |
| — not final | **59** |
| — missing `espn_id` | **59** |
| ncaab picks / graded | **429 / 16** |

Those three 59s are the same 59 games: an unmatchable team row means
`backfill_espn_ids` reports `no_match`, so the game never gets an `espn_id` and
the catch-up can never finalise it. **They are stranded originals, not
duplicates** — a same-matchup/±1-day scan finds 0 twin pairs among games
sharing team rows.

Resolving all 78 against ESPN's `displayName`:

| resolution tier | rows |
|---|---|
| exact `displayName` match | 64 |
| after `\bSt\b` → `State` | 8 |
| hand-written alias | 5 |
| **unresolved** | **1** (`'Queens University Royals'`, 1 game) |

| action | rows | game refs |
|---|---|---|
| **rename** (target abbreviation free) | 17 | 19 |
| **merge** (an existing row owns it) | 60 | 89 |
| leave alone (unresolved) | 1 | 1 |

No two bad rows resolve to the same target, so there are no merge-of-merges.

## Root cause

`backend/pipeline/full_pipeline.py:400` and `:404`:

```python
home_team = Team(name=home_name, abbreviation=home_name, sport=sport)
```

The comment says "primarily for boxing/MMA fighters", where a fighter's name
*is* the identity and this is correct. But the branch is not restricted by
sport, so every Odds API ncaab event naming a school absent from our 147-row
seed created a row whose `abbreviation` is `'Pennsylvania Quakers'`. ESPN sends
`PENN`. They never match.

## File Structure

| File | Responsibility |
|---|---|
| `backend/data/ncaab_teams.json` (create) | Committed snapshot of ESPN's 362 ncaab teams: `espn_id`, `abbreviation`, `display_name`, `location`. Data, not code. |
| `backend/team_identity.py` (create) | Pure resolver. `canonical_abbr(sport, label)`. No network, no session, no DB. The single definition of "what school is this string". |
| `backend/scripts/refresh_ncaab_teams.py` (create) | Regenerates the JSON from ESPN. Run by hand, never by tests. |
| `backend/scripts/fix_team_identity.py` (create) | The repair: classify, repoint FKs, resolve resulting duplicate games. `--dry-run` default, `--apply` to write. |
| `backend/pipeline/full_pipeline.py:396-406` (modify) | Stop inventing display-name rows for abbreviation-identity sports. |
| `tests/test_team_identity.py` (create) | Resolver unit tests. |
| `tests/test_fix_team_identity.py` (create) | Repair tests against an in-memory DB. |
| `tests/test_full_pipeline_team_creation.py` (create) | Guard proving the junk-row path is closed. |

---

### Task 1: The offline resolver and its data

**Files:**
- Create: `backend/data/ncaab_teams.json`
- Create: `backend/team_identity.py`
- Create: `backend/scripts/refresh_ncaab_teams.py`
- Test: `tests/test_team_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `canonical_abbr(sport: str, label: str) -> str | None` — the ESPN
    abbreviation for a team label, or `None` if unresolved.
  - `resolution_of(sport: str, label: str) -> tuple[str | None, str]` — the
    abbreviation and the tier that produced it, one of `"exact"`,
    `"normalised"`, `"alias"`, `"already_abbr"`, `"unresolved"`.
  - `ABBREVIATION_SPORTS: frozenset[str]` — sports where the abbreviation is
    the identity.

- [ ] **Step 1: Generate the committed snapshot**

Create `backend/scripts/refresh_ncaab_teams.py`:

```python
"""Regenerate backend/data/ncaab_teams.json from ESPN.

Run by hand when ESPN adds or renames a school. NOT run by tests: the
snapshot is committed so the resolver stays pure and offline.

    python -m backend.scripts.refresh_ncaab_teams
"""

import json
import pathlib

import httpx

URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
       "mens-college-basketball/teams?limit=500")
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "ncaab_teams.json"


def main() -> int:
    payload = httpx.get(URL, timeout=30).json()
    teams = [t["team"] for t in payload["sports"][0]["leagues"][0]["teams"]]
    rows = [
        {
            "espn_id": t["id"],
            "abbreviation": t["abbreviation"],
            "display_name": t["displayName"],
            "location": t.get("location", ""),
        }
        for t in teams
        if t.get("abbreviation") and t.get("displayName")
    ]
    rows.sort(key=lambda r: r["abbreviation"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} teams to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it:

```bash
.venv/Scripts/python.exe -m backend.scripts.refresh_ncaab_teams
```

Expected: `wrote 362 teams to ...backend\data\ncaab_teams.json`

- [ ] **Step 2: Write the failing resolver test**

Create `tests/test_team_identity.py`:

```python
import pytest

from backend.team_identity import (
    ABBREVIATION_SPORTS,
    canonical_abbr,
    resolution_of,
)


def test_exact_display_name_resolves():
    assert canonical_abbr("ncaab", "Pennsylvania Quakers") == "PENN"


def test_state_is_spelled_out_before_matching():
    # The Odds API writes 'St' where ESPN writes 'State'.
    assert canonical_abbr("ncaab", "Michigan St Spartans") == "MSU"
    assert resolution_of("ncaab", "Michigan St Spartans")[1] == "normalised"


def test_hand_written_alias_resolves():
    assert canonical_abbr("ncaab", "GW Revolutionaries") == "GW"
    assert resolution_of("ncaab", "GW Revolutionaries")[1] == "alias"


def test_a_real_abbreviation_passes_through():
    assert canonical_abbr("ncaab", "PENN") == "PENN"
    assert resolution_of("ncaab", "PENN")[1] == "already_abbr"


def test_unknown_label_is_unresolved_not_guessed():
    assert canonical_abbr("ncaab", "Springfield Isotopes") is None
    assert resolution_of("ncaab", "Springfield Isotopes")[1] == "unresolved"


def test_st_is_not_expanded_inside_a_word():
    """Guard the rule directly, via _normalise -- NOT via canonical_abbr.

    'Stonehill Skyhawks' is an exact ESPN display name, so resolution stops
    at the first tier and never reaches the St rule. A version of this test
    written as `canonical_abbr(...) == "STO"` passes even with the word
    boundaries deleted from the regex. Verified during execution.
    """
    from backend.team_identity import _normalise

    assert _normalise("Stonehill Skyhawks") == "Stonehill Skyhawks"
    assert _normalise("St. John's Red Storm") == "St. John's Red Storm"
    assert _normalise("Michigan St Spartans") == "Michigan State Spartans"


def test_sports_without_a_table_resolve_nothing():
    assert canonical_abbr("mma", "Jon Jones") is None


def test_abbreviation_sports_excludes_combat_sports():
    assert "ncaab" in ABBREVIATION_SPORTS
    assert "mma" not in ABBREVIATION_SPORTS
    assert "boxing" not in ABBREVIATION_SPORTS


def test_every_abbreviation_in_the_snapshot_is_unique():
    from backend.team_identity import _table
    abbrs = [r["abbreviation"] for r in _table("ncaab")]
    assert len(abbrs) == len(set(abbrs)), "snapshot has duplicate abbreviations"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.team_identity'`

- [ ] **Step 4: Implement the resolver**

Create `backend/team_identity.py`:

```python
"""What school is this string?

One definition, used by both the repair script and the live odds pipeline.

The Odds API names teams by display name ("Pennsylvania Quakers"); ESPN and
our own team rows key on an abbreviation ("PENN"). This module is the only
place that bridges the two. It is deliberately pure -- no session, no network,
no I/O beyond reading a committed snapshot once -- so it is cheap to test and
cannot behave differently in production than it does under pytest.
"""

from __future__ import annotations

import functools
import json
import pathlib
import re

_DATA = pathlib.Path(__file__).resolve().parent / "data"

#: Sports whose identity is an abbreviation. Combat sports are excluded on
#: purpose: a fighter's name *is* the identity, so a display-name team row is
#: correct there and must not be "repaired".
ABBREVIATION_SPORTS = frozenset({"nba", "nfl", "ncaab", "ncaaf", "mlb"})

#: Schools whose Odds API label differs from ESPN's display name by more than
#: the St/State rule. Hand-written and deliberately short: every entry is a
#: claim that two strings name the same school, and a wrong entry silently
#: merges two programmes. Add only what has been checked by eye.
_ALIASES: dict[str, dict[str, str]] = {
    "ncaab": {
        "Prairie View Panthers": "Prairie View A&M Panthers",
        "LIU Sharks": "Long Island University Sharks",
        "Cal Baptist Lancers": "California Baptist Lancers",
        "Seattle Redhawks": "Seattle U Redhawks",
        "GW Revolutionaries": "George Washington Revolutionaries",
    },
}

_ST = re.compile(r"\bSt\b(?!\.)")


@functools.lru_cache(maxsize=None)
def _table(sport: str) -> tuple[dict[str, str], ...]:
    path = _DATA / f"{sport}_teams.json"
    if not path.exists():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def _by_display(sport: str) -> dict[str, str]:
    return {r["display_name"]: r["abbreviation"] for r in _table(sport)}


@functools.lru_cache(maxsize=None)
def _abbrs(sport: str) -> frozenset[str]:
    return frozenset(r["abbreviation"] for r in _table(sport))


def resolution_of(sport: str, label: str) -> tuple[str | None, str]:
    """Resolve ``label`` to an ESPN abbreviation, reporting how.

    Tiers run most-exact first and stop at the first hit, so a label that
    matches exactly is never reinterpreted by a looser rule.
    """
    if not label:
        return None, "unresolved"
    if label in _abbrs(sport):
        return label, "already_abbr"

    display = _by_display(sport)
    if label in display:
        return display[label], "exact"

    normalised = _ST.sub("State", label)
    if normalised in display:
        return display[normalised], "normalised"

    alias = _ALIASES.get(sport, {}).get(label)
    if alias and alias in display:
        return display[alias], "alias"

    return None, "unresolved"


def canonical_abbr(sport: str, label: str) -> str | None:
    """The ESPN abbreviation for ``label``, or None if we cannot tell."""
    return resolution_of(sport, label)[0]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_team_identity.py -v`
Expected: PASS, 12 tests.

If `test_st_is_not_expanded_inside_a_word` fails, the `\b` anchors are wrong —
fix the regex, not the test.

- [ ] **Step 6: Prove the tests bite (mutation)**

Three mutations, each restored afterwards. All three were run during
execution and all three fail the expected test:

1. `_ST = re.compile("St")` (drop the word boundaries) →
   `test_st_is_not_expanded_inside_a_word` FAILS.
   **This only works because that test calls `_normalise` directly.** The
   tiered design means an exact-matching label never reaches the St rule, so
   any end-to-end test of it is vacuous.
2. Delete the `normalised` tier from `resolution_of` →
   `test_state_is_spelled_out_before_matching` FAILS.
3. Reorder the tiers so alias is tried before exact →
   `test_exact_match_wins_over_a_looser_tier` FAILS.

The ordering guard is this test:

```python
def test_exact_match_wins_over_a_looser_tier():
    # A label that is BOTH an exact display name and an alias key must
    # resolve by the exact tier.
    from backend import team_identity as ti
    # NB: resolution_of is not itself cached -- only the table loaders are --
    # so mutating _ALIASES takes effect immediately with no cache to clear.
    ti._ALIASES["ncaab"]["Duke Blue Devils"] = "North Carolina Tar Heels"
    try:
        assert ti.resolution_of("ncaab", "Duke Blue Devils") == ("DUKE", "exact")
    finally:
        ti._ALIASES["ncaab"].pop("Duke Blue Devils")
```

- [ ] **Step 7: Commit**

```bash
git add backend/team_identity.py backend/data/ncaab_teams.json \
        backend/scripts/refresh_ncaab_teams.py tests/test_team_identity.py
git commit -m "feat(teams): one offline resolver for ncaab team identity"
```

---

### Task 2: Stop the pipeline creating display-name rows

**Files:**
- Modify: `backend/pipeline/full_pipeline.py:396-406`
- Test: `tests/test_full_pipeline_team_creation.py`

**Interfaces:**
- Consumes: `canonical_abbr`, `ABBREVIATION_SPORTS` from Task 1.
- Produces: no new public names. Behaviour change only.

This task is what stops the bleeding. Do it before the repair, so the repair
cannot be undone by the next odds fetch.

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_full_pipeline_team_creation.py`:

```python
"""The odds path must not invent teams whose abbreviation is a display name.

`_ensure_game_from_odds` already documents the intended rule -- "Only creates
new teams/games for sports without ESPN coverage (boxing, etc.)" -- but does
not enforce it. These tests make the docstring true.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Game, Team
from backend.pipeline.full_pipeline import _ensure_game_from_odds


def event(home, away, commence="2026-01-05T23:00:00Z"):
    return {"home_team": home, "away_team": away, "commence_time": commence}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_known_school_resolves_to_an_existing_row(session):
    session.add_all([
        Team(name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
        Team(name="Duke", abbreviation="DUKE", sport="ncaab"),
    ])
    session.commit()

    # The existing lookup matches on Team.name only, so these labels miss it
    # and fall through to the creation branch -- which is the bug.
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))

    assert session.query(Team).count() == 2, "must reuse PENN/DUKE, not add rows"
    game = session.query(Game).one()
    by_abbr = {t.abbreviation: t.id for t in session.query(Team).all()}
    assert game.home_team_id == by_abbr["PENN"]
    assert game.away_team_id == by_abbr["DUKE"]


def test_known_school_is_created_with_its_abbreviation(session):
    """No existing row: create one, but keyed by abbreviation, not display name."""
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))

    abbrs = {t.abbreviation for t in session.query(Team).all()}
    assert abbrs == {"PENN", "DUKE"}


def test_unresolvable_school_creates_no_junk_team(session):
    _ensure_game_from_odds(
        session, "ncaab", event("Springfield Isotopes", "Shelbyville Atoms"))

    # A row we cannot match to ESPN can never get an espn_id, never finalise
    # and never grade. Refusing beats creating one that looks fine.
    assert session.query(Team).count() == 0
    assert session.query(Game).count() == 0


def test_combat_sports_still_create_teams_from_fighter_names(session):
    # For mma/boxing the fighter's NAME is the identity. This path must stay.
    _ensure_game_from_odds(session, "mma", event("Jon Jones", "Stipe Miocic"))

    assert {t.abbreviation for t in session.query(Team).all()} == {
        "Jon Jones", "Stipe Miocic",
    }
    assert session.query(Game).count() == 1


def test_an_existing_game_is_not_duplicated(session):
    """Guard the behaviour the current code already has, before changing it."""
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))
    _ensure_game_from_odds(
        session, "ncaab", event("Pennsylvania Quakers", "Duke Blue Devils"))
    assert session.query(Game).count() == 1
```

Read `full_pipeline.py:359-412` before writing these, and confirm
`_ensure_game_from_odds` still takes `(session, sport, event)` and still reads
`home_team` / `away_team` / `commence_time` from the event.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_full_pipeline_team_creation.py -v`
Expected three failures, for the right reasons:
- `test_unresolvable_school_creates_no_junk_team` — two junk rows are created.
- `test_known_school_resolves_to_an_existing_row` — a third and fourth row are
  created instead of reusing PENN/DUKE, because the lookup matches on
  `Team.name` only.
- `test_known_school_is_created_with_its_abbreviation` — rows are created with
  `abbreviation='Pennsylvania Quakers'`.

`test_combat_sports_still_create_teams_from_fighter_names` and
`test_an_existing_game_is_not_duplicated` should PASS already. If either fails
now, stop: you have misread the current behaviour.

- [ ] **Step 3: Implement**

In `backend/pipeline/full_pipeline.py`, add the import:

```python
from backend.team_identity import ABBREVIATION_SPORTS, canonical_abbr
```

Replace lines 396-406:

```python
    # Create teams only if they don't exist (primarily for boxing/MMA fighters)
    if not home_team:
        home_team = Team(name=home_name, abbreviation=home_name, sport=sport)
        session.add(home_team)
        session.flush()
    if not away_team:
        away_team = Team(name=away_name, abbreviation=away_name, sport=sport)
        session.add(away_team)
        session.flush()
```

with:

```python
    # For combat sports the fighter's NAME is the identity, so creating a row
    # from the label is correct. For team sports the identity is an
    # abbreviation: a row whose abbreviation is "Pennsylvania Quakers" can
    # never match ESPN, never gets an espn_id, and never finalises -- which is
    # how 59 ncaab games and 413 picks were stranded. Resolve, or refuse.
    resolved: dict[str, Team] = {"home": home_team, "away": away_team}
    for side, label in (("home", home_name), ("away", away_name)):
        team = resolved[side]
        if team:
            continue
        if sport not in ABBREVIATION_SPORTS:
            created = Team(name=label, abbreviation=label, sport=sport)
        else:
            abbr = canonical_abbr(sport, label)
            if abbr is None:
                logger.warning(
                    "Cannot identify %s team %r for %s; skipping game rather "
                    "than creating an unmatchable row", sport, label, side,
                )
                return
            existing = session.query(Team).filter(
                Team.sport == sport, Team.abbreviation == abbr,
            ).first()
            created = existing or Team(name=label, abbreviation=abbr, sport=sport)
        if created.id is None:
            session.add(created)
            session.flush()
        resolved[side] = created
    home_team, away_team = resolved["home"], resolved["away"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_full_pipeline_team_creation.py -v`
Expected: PASS, 3 tests.

Then the whole suite — this touches a hot path:

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 646 passed (the pre-existing baseline) plus the new tests.

- [ ] **Step 5: Prove the guard bites**

Revert just the `if abbr is None: return` to `abbr = label` and re-run.
Expected: `test_unresolvable_school_creates_no_junk_team` FAILS. Restore.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/full_pipeline.py tests/test_full_pipeline_team_creation.py
git commit -m "fix(pipeline): resolve team identity instead of inventing display-name rows"
```

---

### Task 3: The repair script

**Files:**
- Create: `backend/scripts/fix_team_identity.py`
- Test: `tests/test_fix_team_identity.py`

**Interfaces:**
- Consumes: `resolution_of`, `ABBREVIATION_SPORTS` from Task 1.
- Produces:
  - `fk_children(cur, parent: str) -> list[tuple[str, str]]` — every
    `(table, column)` with a foreign key to `parent`.
  - `classify(cur, sport) -> dict[str, list[Plan]]` — buckets `"rename"`,
    `"merge"`, `"unresolved"`.
  - `Plan` — `NamedTuple(team_id: int, label: str, target_abbr: str | None,
    target_team_id: int | None, games: int)`.

The merge half of this is the same shape as the Clippers repair, generalised:
discover FK children from the schema rather than listing them. That script
listed `picks` and `odds` by hand and its DELETE died on a `FOREIGN KEY`
constraint because `player_props` also pointed at `games` — 578 rows it had
not accounted for. Ask the schema.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fix_team_identity.py`:

```python
"""The ncaab team-identity repair, against a real sqlite file."""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, EloHistory, Game, Team, TeamStat
from backend.scripts.fix_team_identity import classify, fk_children, repair


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        # A correct row, and a display-name row that collides with it.
        s.add_all([
            Team(id=1, name="Pennsylvania", abbreviation="PENN", sport="ncaab"),
            Team(id=2, name="Pennsylvania Quakers",
                 abbreviation="Pennsylvania Quakers", sport="ncaab"),
            # A display-name row whose target abbreviation is free.
            Team(id=3, name="Yale Bulldogs", abbreviation="Yale Bulldogs",
                 sport="ncaab"),
            Team(id=4, name="Duke", abbreviation="DUKE", sport="ncaab"),
        ])
        s.add(Game(id=10, sport="ncaab", season="2026", date="2026-01-05",
                   home_team_id=2, away_team_id=4, status="scheduled"))
        s.add(Game(id=11, sport="ncaab", season="2026", date="2026-01-06",
                   home_team_id=3, away_team_id=4, status="scheduled"))
        s.add(TeamStat(team_id=2, game_id=10, stat_type="pts", value=70.0))
        s.add(EloHistory(team_id=2, game_id=10, sport="ncaab", rating=1500.0))
        s.commit()
    return str(path)


def test_fk_children_discovers_every_team_reference(db):
    con = sqlite3.connect(db)
    found = set(fk_children(con.cursor(), "teams"))
    # Discovered from the schema, not hardcoded -- games references teams TWICE.
    assert ("games", "home_team_id") in found
    assert ("games", "away_team_id") in found
    assert ("team_stats", "team_id") in found
    assert ("elo_history", "team_id") in found


def test_classify_splits_rename_from_merge(db):
    con = sqlite3.connect(db)
    buckets = classify(con.cursor(), "ncaab")
    assert [p.team_id for p in buckets["merge"]] == [2]
    assert buckets["merge"][0].target_team_id == 1
    assert [p.team_id for p in buckets["rename"]] == [3]
    assert buckets["rename"][0].target_abbr == "YALE"


def test_merge_repoints_every_child_then_deletes_the_row(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    repair(con, "ncaab", apply=True)

    cur = con.cursor()
    assert cur.execute("SELECT COUNT(*) FROM teams WHERE id=2").fetchone()[0] == 0
    # The game, the stat and the elo row all moved to the surviving row.
    assert cur.execute(
        "SELECT home_team_id FROM games WHERE id=10").fetchone()[0] == 1
    assert cur.execute(
        "SELECT team_id FROM team_stats WHERE game_id=10").fetchone()[0] == 1
    assert cur.execute(
        "SELECT team_id FROM elo_history WHERE game_id=10").fetchone()[0] == 1


def test_rename_keeps_the_row_and_its_id(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    repair(con, "ncaab", apply=True)
    cur = con.cursor()
    assert cur.execute(
        "SELECT abbreviation FROM teams WHERE id=3").fetchone()[0] == "YALE"
    assert cur.execute(
        "SELECT home_team_id FROM games WHERE id=11").fetchone()[0] == 3


def test_dry_run_writes_nothing(db):
    con = sqlite3.connect(db)
    before = con.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    repair(con, "ncaab", apply=False)
    assert con.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == before
    assert con.execute(
        "SELECT abbreviation FROM teams WHERE id=3").fetchone()[0] == "Yale Bulldogs"


def test_abbreviations_are_unique_after_repair(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    repair(con, "ncaab", apply=True)
    abbrs = [r[0] for r in con.execute(
        "SELECT abbreviation FROM teams WHERE sport='ncaab'")]
    # The invariant the Elo replay depends on.
    assert len(abbrs) == len(set(abbrs))


def test_unresolved_rows_are_left_untouched(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("INSERT INTO teams (id, name, abbreviation, sport) "
                "VALUES (9, 'Springfield Isotopes', 'Springfield Isotopes', 'ncaab')")
    con.commit()
    repair(con, "ncaab", apply=True)
    assert con.execute(
        "SELECT abbreviation FROM teams WHERE id=9").fetchone()[0] \
        == "Springfield Isotopes"


def test_merge_that_would_create_a_duplicate_game_is_reported(db):
    """Two rows for one school can hold the same fixture twice."""
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    # Team 1 already has the same fixture on the same date as team 2's game 10.
    con.execute("INSERT INTO games (id, sport, season, date, home_team_id, "
                "away_team_id, status) VALUES "
                "(12, 'ncaab', '2026', '2026-01-05', 1, 4, 'final')")
    con.commit()
    result = repair(con, "ncaab", apply=True)
    assert result["duplicate_games"], "a collision must be reported, not silently created"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fix_team_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.scripts.fix_team_identity`.

- [ ] **Step 3: Implement**

Create `backend/scripts/fix_team_identity.py`:

```python
"""Repair team rows whose `abbreviation` holds a display name.

Each bad row is one of:

  rename  -- no existing row owns the target abbreviation. Rewrite in place,
             keeping the id, so nothing needs repointing.
  merge   -- an existing row owns it. Repoint every foreign key to the
             survivor and delete the bad row.

`(sport, abbreviation)` uniqueness is the point of the exercise: the Elo
replay keys on abbreviation in memory (team_stats.py:362), so two rows
sharing one would silently merge two programmes' ratings.

Child tables are DISCOVERED from the schema, never listed. A hand-written
list is how the Clippers repair missed 578 `player_props` rows and died on a
FOREIGN KEY constraint.

    python -m backend.scripts.fix_team_identity --db <abs path> [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import NamedTuple

from backend.team_identity import ABBREVIATION_SPORTS, resolution_of


class Plan(NamedTuple):
    team_id: int
    label: str
    target_abbr: str | None
    target_team_id: int | None
    games: int


def ident(name: str) -> str:
    """Validate an identifier that cannot be a bound parameter."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"refusing to interpolate identifier {name!r}")
    return f'"{name}"'


def fk_children(cur, parent: str) -> list[tuple[str, str]]:
    """Every (table, column) holding a foreign key to ``parent``.id."""
    out: list[tuple[str, str]] = []
    for (table,) in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        for fk in cur.execute(
            "PRAGMA foreign_key_list(" + ident(table) + ")"
        ).fetchall():
            if fk[2] == parent:
                out.append((table, fk[3]))
    return out


def classify(cur, sport: str) -> dict[str, list[Plan]]:
    rows = cur.execute(
        "SELECT id, name, abbreviation FROM teams WHERE sport=? ORDER BY id",
        (sport,),
    ).fetchall()
    owner = {abbr: tid for tid, _, abbr in rows}

    buckets: dict[str, list[Plan]] = {"rename": [], "merge": [], "unresolved": []}
    for tid, _name, abbr in rows:
        target, how = resolution_of(sport, abbr)
        if how == "already_abbr":
            continue
        games = cur.execute(
            "SELECT COUNT(*) FROM games WHERE home_team_id=? OR away_team_id=?",
            (tid, tid),
        ).fetchone()[0]
        if target is None:
            buckets["unresolved"].append(Plan(tid, abbr, None, None, games))
        elif target in owner and owner[target] != tid:
            buckets["merge"].append(Plan(tid, abbr, target, owner[target], games))
        else:
            buckets["rename"].append(Plan(tid, abbr, target, None, games))
    return buckets


def _duplicate_games(cur, sport: str) -> list[tuple[int, ...]]:
    """Fixtures now held twice: same sport, date and teams."""
    return cur.execute(
        "SELECT sport, date, home_team_id, away_team_id, COUNT(*) c, "
        "       GROUP_CONCAT(id) ids "
        "FROM games WHERE sport=? "
        "GROUP BY sport, date, home_team_id, away_team_id HAVING c > 1",
        (sport,),
    ).fetchall()


def repair(con: sqlite3.Connection, sport: str, *, apply: bool) -> dict:
    if sport not in ABBREVIATION_SPORTS:
        raise ValueError(f"{sport} identifies teams by name, not abbreviation")
    cur = con.cursor()
    children = fk_children(cur, "teams")
    buckets = classify(cur, sport)

    print(f"{'APPLY' if apply else 'DRY RUN'} -- sport={sport}")
    print("  team child columns:", ", ".join(f"{t}.{c}" for t, c in children))
    for name, plans in buckets.items():
        print(f"  {name:<11} {len(plans):>3} rows, "
              f"{sum(p.games for p in plans):>3} game references")

    if apply:
        for p in buckets["merge"]:
            for table, column in children:
                sql = ("UPDATE " + ident(table) + " SET " + ident(column)
                       + "=? WHERE " + ident(column) + "=?")
                cur.execute(sql, (p.target_team_id, p.team_id))
            cur.execute("DELETE FROM teams WHERE id=?", (p.team_id,))
        for p in buckets["rename"]:
            cur.execute("UPDATE teams SET abbreviation=? WHERE id=?",
                        (p.target_abbr, p.team_id))
        con.commit()

        abbrs = [r[0] for r in cur.execute(
            "SELECT abbreviation FROM teams WHERE sport=?", (sport,))]
        assert len(abbrs) == len(set(abbrs)), "abbreviations not unique after repair"
        for table, column in children:
            sql = ("SELECT COUNT(*) FROM " + ident(table) + " x "
                   "LEFT JOIN teams t ON t.id = x." + ident(column)
                   + " WHERE x." + ident(column) + " IS NOT NULL AND t.id IS NULL")
            orphan = cur.execute(sql).fetchone()[0]
            assert orphan == 0, f"{orphan} orphaned rows in {table}"

    dupes = _duplicate_games(cur, sport)
    if dupes:
        print(f"\n  !! {len(dupes)} fixtures are now held by more than one game row.")
        print("     Merging teams revealed twins that the old ids hid.")
        for row in dupes[:10]:
            print(f"     {row[1]} teams={row[2]}/{row[3]} game ids={row[5]}")
        print("     Resolve these before finalising: the FINAL row survives.")

    return {"buckets": buckets, "duplicate_games": dupes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--sport", default="ncaab")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")
    repair(con, args.sport, apply=args.apply)
    if not args.apply:
        print("\n  (dry run -- nothing written; re-run with --apply)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fix_team_identity.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove the uniqueness assertion bites**

Change `classify` so the `merge` bucket falls through to `rename` (i.e. treat a
collision as a free rename) and re-run.
Expected: `test_abbreviations_are_unique_after_repair` FAILS with
`abbreviations not unique after repair`, and
`test_merge_repoints_every_child_then_deletes_the_row` FAILS. Restore.

Then delete the `("games", "away_team_id")` discovery by hardcoding
`children = [("games", "home_team_id")]` and re-run.
Expected: `test_fk_children_discovers_every_team_reference` FAILS. Restore.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/fix_team_identity.py tests/test_fix_team_identity.py
git commit -m "feat(scripts): repair team rows holding display names"
```

---

### Task 4: Apply to production

**Files:** none changed. This task runs what Tasks 1-3 built.

**Interfaces:**
- Consumes: `backend.scripts.fix_team_identity`, plus the existing
  `backfill_espn_ids`, `catch_up_finals` and grading entry points.
- Produces: a repaired `sports_picks.db`.

This task writes to live data. Every step is gated.

- [ ] **Step 1: Stop the scheduler**

It holds the same database file and `morning_scout` fires at 08:00 ET.
Confirm it is stopped before continuing.

- [ ] **Step 2: Back up**

```bash
.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect(r'<abs db path>'); c.execute('pragma wal_checkpoint(TRUNCATE)'); c.close()"
cp sports_picks.db "sports_picks.backup-$(date +%Y%m%d-%H%M%S).db"
```

Verify the backup independently: `PRAGMA integrity_check` is `ok`, and its
games/picks counts match the live file. It is the only rollback.

- [ ] **Step 3: Dry run**

```bash
.venv/Scripts/python.exe -m backend.scripts.fix_team_identity --db "<abs db path>"
```

Expected, from the 2026-09-18 survey: `rename 17 rows / 19 game references`,
`merge 60 rows / 89 game references`, `unresolved 1 rows / 1 game references`.

**If the numbers differ, stop and re-survey.** They are a fingerprint of the
database this plan was written against.

- [ ] **Step 4: Rehearse on a copy**

```bash
cp sports_picks.db /tmp/trial.db   # or the session scratchpad
.venv/Scripts/python.exe -m backend.scripts.fix_team_identity --db "<abs trial path>" --apply
```

Expected: the assertions pass, and the duplicate-fixture report prints whatever
collisions the merge revealed. A dry run cannot surface those, because they
only exist once team ids have been merged — this step is the only place they
appear before production. Record the count.

- [ ] **Step 5: Apply**

```bash
.venv/Scripts/python.exe -m backend.scripts.fix_team_identity --db "<abs db path>" --apply
```

- [ ] **Step 6: Resolve any duplicate fixtures the merge revealed**

For each reported pair, the **final** row survives and the empty twin's
children are repointed onto it — the same rule as the Clippers repair, and the
opposite of `merge_duplicate_games.py`'s "row with picks wins", which would
keep the empty row and discard real scores. If Step 4 reported none, skip.

- [ ] **Step 7: Re-run the identification chain**

```bash
.venv/Scripts/python.exe -m backend.scripts.backfill_espn_ids --db "<abs db path>" --apply
.venv/Scripts/python.exe -m backend.scripts.catch_up_finals --db "<abs db path>" --apply
```

Expected: `no_match` for ncaab drops from 59 toward 0, and ncaab's "stuck"
count falls as games finalise. Confirm the exact flag names against each
script's `--help`; do not guess them.

- [ ] **Step 8: Grade**

Run the grading entry point, then confirm ncaab's graded count has risen from
its baseline of 16 of 429.

- [ ] **Step 9: Record what actually happened**

Append the measured before/after to `plans/HANDOFF.md`: rows renamed, rows
merged, duplicate fixtures resolved, `no_match` before and after, ncaab picks
graded before and after. **Correct the handoff's current claim that ncaab's
problem is "~10 real games exist twice"** — the measured shape is 59 stranded
originals and 0 twin pairs.

---

### Task 5: Re-measure calibration with ncaab included

**Files:**
- Modify: `plans/HANDOFF.md`

**Interfaces:**
- Consumes: the repaired database from Task 4.
- Produces: an updated calibration record.

- [ ] **Step 1: Re-run the calibration report**

```bash
.venv/Scripts/python.exe -m backend.analysis.prop_calibration --db "<abs db path>" --sport nba
```

and the 007 report for team-sport picks.

- [ ] **Step 2: State the effective sample size, not the raw win rate**

Every prop tier is currently flagged unreliable; tier 5's effective n is 19.5
against a `min_bin` of 30. Adding ncaab picks changes the count but **not** the
independence: picks within one game are correlated. Report effective n under
both ICC assumptions, as the existing report already does.

- [ ] **Step 3: Do not change `digest.enabled`**

It stays `false`. More graded picks make the measurement possible; they do not
make the model good. Any decision to enable the digest is a separate,
explicitly-argued change.

- [ ] **Step 4: Commit**

```bash
git add plans/HANDOFF.md
git commit -m "docs: record ncaab team identity repair and its calibration effect"
```

---

## Notes for whoever executes this

- **The one unresolved row is fine.** `'Queens University Royals'` (1 game)
  stays as it is. Guessing an alias for a single game is how a wrong merge
  gets in. If you do resolve it, confirm the school by eye against ESPN's
  table first and add it to `_ALIASES` with a test.
- **The alias table is the dangerous part of this plan.** Every entry asserts
  two strings name the same school. A wrong entry does not crash — it merges
  two programmes' games and Elo history, and looks plausible afterwards. Five
  entries, each checkable by eye, is the reason to keep it small.
- **Do not reuse `merge_duplicate_games.py` for the duplicate fixtures in Task
  4 Step 6.** Its survivor rule is "the row with picks", which is wrong
  whenever picks sit on the empty twin and the real scores sit on the other.

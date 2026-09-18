# Plan 014: ESPN dates are UTC, so every evening game is stored a day late

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.
>
> **Drift check (run first)**:
> `git diff --stat 1c75c11..HEAD -- backend/pipeline/full_pipeline.py backend/backtesting/historical.py backend/models.py backend/database.py backend/collectors/espn.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

**Goal:** Stop storing evening games under the wrong date, and give `Game` a
stable identity so ingestion is idempotent regardless of which date convention
a row was created under.

**Architecture:** Two changes, in a forced order. First give `Game` the
`espn_id` the collector already returns and discards, and match on it before
falling back to (date, teams) — that makes ingestion idempotent. Only then flip
`_parse_date` to Eastern time. Doing the date fix first would create a fresh
duplicate for every evening game already stored under its UTC date.

**Tech Stack:** Python 3.14 local / 3.12 production, SQLAlchemy 2.0,
`zoneinfo`, pytest.

**Spec:** none. Derived from the plan 013 Task 3 investigation recorded here.
Read "Why this matters" as the spec.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MEDIUM-HIGH — it changes game *identity* and the date rows are
  keyed by, which Elo replay ordering, `team_stats` scoping and prop lookups
  all depend on
- **Depends on**: plan 013 (done) — its catch-up script is the shape the
  `espn_id` backfill needs
- **Category**: bug
- **Planned at**: commit `1c75c11`, 2026-09-17

## Global Constraints

- Python floor `>=3.12`; CI runs **3.12 and 3.14** and both must pass.
- **Never run a migration or backfill against `sports_picks.db` directly.**
  Copy it first. 1664 games, 708 picks.
- Tests must be network-free (`pytest-httpx`, exact `url=` strings) and
  mutation-proved. Note `nba_api` uses `requests`, not `httpx`, so `httpx_mock`
  cannot intercept it — see `test_game_finalization._no_window_runs`.
- **Do not attempt to merge historical duplicate rows in this plan.** See
  "Out of scope"; it is a separate, riskier decision.

## Why this matters

`full_pipeline.py:429`:

```python
def _parse_date(date_str: str) -> date:
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
```

It parses ESPN's UTC timestamp correctly and then takes the **UTC** date. ESPN
groups its scoreboard by **Eastern** date, so for any game starting after 8pm
ET the two disagree. Measured against the live API for 2026-03-14:

```
BKN @ PHI    raw=2026-03-14T17:00Z   parsed=2026-03-14   (1pm ET -- agrees)
MIL @ ATL    raw=2026-03-14T19:00Z   parsed=2026-03-14
CHA @ SA     raw=2026-03-14T19:30Z   parsed=2026-03-14
WSH @ BOS    raw=2026-03-14T22:00Z   parsed=2026-03-14
ORL @ MIA    raw=2026-03-15T00:00Z   parsed=2026-03-15   <-- 8pm ET, DAY LATE
DEN @ LAL    raw=2026-03-15T00:30Z   parsed=2026-03-15   <-- 8:30pm ET
SAC @ LAC    raw=2026-03-15T02:30Z   parsed=2026-03-15   <-- 10:30pm ET
```

All seven were requested as `dates=20260314` and all seven are ET-dated
2026-03-14 by ESPN. Three land a day late.

`_parse_start_time` is **correct** and must not change — it returns the full
UTC datetime, and a timestamp is timezone-absolute.

### What it costs

The same game exists twice, under both conventions, in production:

| id | date | status | teams |
|---|---|---|---|
| 13 | 2026-03-14 | **scheduled** | MIA vs ORL |
| 1015 | 2026-03-15 | **final** | MIA vs ORL |

The ET-dated row came from another source; the ESPN-dated row was created
alongside it and finalized. The upsert cannot connect them because it matches
on `(sport, date, home_team_id, away_team_id)` and the dates differ.

This is why plan 013's catch-up left 17 NBA rows non-final. They were never
"unmatched" — each has a finalized twin one day later.

**Scope in production, measured 2026-09-17:** 36 pairs share
`(sport, home, away)` on adjacent dates, across nba 16, mlb 12, boxing 4,
mma 4. **Not all are duplicates** — MLB plays three-game series, so most of
those 12 are legitimate consecutive games. Do not treat adjacency as proof.

**Nothing is currently stranded.** Zero picks point at a non-final row whose
adjacent twin is final, so no pick is ungradeable *because of this bug*. That
is why this is P1 and not P0.

### The same bug, twice

`backtesting/historical.py:164` is a byte-for-byte duplicate:

```python
def _parse_date(date_str: str) -> date:
    """Parse ESPN date format (ISO 8601) to a date object."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
```

Two copies, one defect. And `ET = ZoneInfo("America/New_York")` is **already**
defined twice (`digest/job.py:18`, `pipeline/scheduler.py:26`), so the fix must
not add a third.

### The identity key is already in flight

`collectors/espn.py:40` puts `"espn_id": event["id"]` on every event it
returns. `Game` has no `espn_id` column, so the value is carried through
`_store_games` and discarded. `historical.store_games` even documents matching
"by espn_id-like uniqueness (date + teams)" at line 70 — a comment describing
the workaround for a field that was available all along.

## Current state (verified 2026-09-17 at `1c75c11`)

```
games                                 1664
  start_time IS NULL                  nba 1242/1262, boxing 135/135,
                                      mma 137/137, ncaab 79/103, mlb 12/27
adjacent-date same-team pairs           36  (nba 16, mlb 12, boxing 4, mma 4)
picks stranded on a duplicate            0
picks on a final game                   61
picks on a non-final game              647
```

**`start_time` is not a usable dedup key** — it is NULL for almost everything,
because most rows came from `historical.store_games`, which never set it. That
rules out the obvious "same teams, same kickoff" approach.

## Order matters, and why

**Do `espn_id` first.** If `_parse_date` is flipped to ET while matching is
still `(sport, date, teams)`, then every evening game already stored under its
UTC date stops matching, and the next ingest inserts a *second* row under the
ET date. The fix would double the problem it is meant to solve.

With `espn_id` matched first, an existing row is found regardless of which date
it was stored under, and its date can be corrected in place.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/models.py` | `Game.espn_id` | 1 |
| `backend/database.py` | migration adding the column | 1 |
| `backend/pipeline/full_pipeline.py` | match on `espn_id` first; ET `_parse_date` | 1, 2 |
| `backend/backtesting/historical.py` | import the shared parser instead of its copy | 2 |
| `backend/time_utils.py` | **new** — one `ET`, one `espn_date` | 2 |
| `backend/scripts/backfill_espn_ids.py` | **new** | 1 |
| `backend/scripts/report_duplicate_games.py` | **new**, read-only | 3 |
| `backend/tests/test_espn_game_identity.py` | **new** | 1, 2 |

---

### Task 1: Give `Game` the identity the collector already provides

**Files:**
- Modify: `backend/models.py` (class `Game`, lines 19-33)
- Modify: `backend/database.py` (new migration, registered in `MIGRATIONS`)
- Modify: `backend/pipeline/full_pipeline.py:167-180`
- Create: `backend/scripts/backfill_espn_ids.py`
- Test: `backend/tests/test_espn_game_identity.py` (new)

**Interfaces:**
- Produces: `Game.espn_id: str | None`, and `_store_games` matching on it
  before `(sport, date, home_team_id, away_team_id)`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_game_is_matched_by_espn_id_even_when_its_stored_date_is_wrong(db_session, httpx_mock):
    """The upsert matched on (sport, date, teams), so an evening game stored
    under its UTC date could never be connected to the same game arriving with
    its ET date. That is how production ended up with MIA/ORL twice -- id 13
    scheduled on 2026-03-14 and id 1015 final on 2026-03-15.
    """
```

Seed one game with `espn_id="401`…`"` dated **2026-03-15** and `scheduled`.
Stub ESPN's `dates=20260314` returning that same `espn_id` as `STATUS_FINAL`.
Assert: the existing row is **finalized**, its date is corrected, and **no
second row is created** (`session.query(Game).count() == 1`).

```python
def test_two_legitimate_games_between_the_same_teams_stay_separate(db_session, httpx_mock):
    """MLB plays three-game series: the same pair on consecutive days is
    normal, not a duplicate. 12 of production's 36 adjacent pairs are mlb.
    Distinct espn_ids must remain distinct rows."""
```

- [ ] **Step 2: Run both and watch the first fail** (a second row is created).

- [ ] **Step 3: Add the column and migration**

`models.py`, class `Game`:

```python
    #: ESPN's stable event id. The collector has always returned this
    #: (collectors/espn.py:40) and it was discarded for want of a column, so
    #: rows were identified by (date, teams) instead -- which breaks when the
    #: same game arrives under two date conventions.
    espn_id = Column(String, nullable=True, index=True)
```

`database.py`, following the established pattern exactly (read
`migrate_pick_prop_fields` first) and registered in `MIGRATIONS`:

```sql
ALTER TABLE games ADD COLUMN espn_id VARCHAR
```

Add a `test_migrations.py` case in the style of
`test_run_migrations_adds_the_prop_grading_columns_to_a_legacy_picks_table`:
an unregistered migration gives a pre-existing database the model attribute
but not the column.

- [ ] **Step 4: Match on it in `_store_games`**

Before the existing `(sport, date, teams)` lookup:

```python
        existing = None
        if g.get("espn_id"):
            existing = session.query(Game).filter(
                Game.sport == sport, Game.espn_id == g["espn_id"]
            ).first()
            if existing is not None and existing.date != game_date:
                # The row was stored under the other date convention. ESPN's
                # id is authoritative for identity, so correct the date rather
                # than insert a twin.
                logger.info("Correcting %s game %s date: %s -> %s",
                            sport, existing.id, existing.date, game_date)
                existing.date = game_date
        if existing is None:
            existing = session.query(Game).filter(
                Game.sport == sport,
                Game.date == game_date,
                Game.home_team_id == home_id,
                Game.away_team_id == away_id,
            ).first()
```

and set `espn_id=g.get("espn_id")` on the insert path, plus
`if existing.espn_id is None: existing.espn_id = g.get("espn_id")` on the
update path so existing rows acquire it as they are seen.

- [ ] **Step 5: Run both tests and watch them pass. Then the full suite.**

- [ ] **Step 6: Mutation-prove** — remove the `espn_id` branch and confirm the
  first test fails with two rows.

- [ ] **Step 7: Backfill script**

Create `backend/scripts/backfill_espn_ids.py`, modelled on
`backend/scripts/catch_up_finals.py` — copy its `--db` safety (required, no
default, `FileNotFoundError` on a missing path), `--dry-run`, and its
data-derived date list. For each `(sport, date)` present in `games`, fetch that
date's scoreboard and set `espn_id` on rows whose `(home, away)` matches,
**where `espn_id` is currently NULL**.

**It must never change a date and never create a row** — it only fills in
`espn_id`. Report matched / unmatched counts per sport. Unmatched is expected
for ncaab (see STOP condition 3).

- [ ] **Step 8: Dry-run, then run, against a copy. Report the match rate.**

- [ ] **Step 9: Commit.**

---

### Task 2: One `_parse_date`, in Eastern time

Only after Task 1, and only after the `espn_id` backfill has run on the target
database. Without it this task creates duplicates.

**Files:**
- Create: `backend/time_utils.py`
- Modify: `backend/pipeline/full_pipeline.py:429-435`
- Modify: `backend/backtesting/historical.py:164-166`
- Modify: `backend/digest/job.py:18`, `backend/pipeline/scheduler.py:26`
- Test: `backend/tests/test_espn_game_identity.py`

**Interfaces:**
- Produces: `backend.time_utils.ET` and
  `backend.time_utils.espn_date(date_str: str) -> date`.
- Both `full_pipeline._parse_date` and `historical._parse_date` are deleted in
  favour of it. `_parse_start_time` is **unchanged**.

- [ ] **Step 1: Write the failing test**

```python
import datetime
from backend.time_utils import espn_date


def test_an_evening_game_is_dated_by_its_eastern_date_not_its_utc_date():
    """ESPN groups its scoreboard by Eastern date but timestamps events in UTC,
    so anything after 8pm ET lands on the next UTC day. Measured against the
    live API for 2026-03-14: ORL@MIA is 2026-03-15T00:00Z and DEN@LAL is
    2026-03-15T00:30Z, both returned under dates=20260314.
    """
    assert espn_date("2026-03-15T00:00Z") == datetime.date(2026, 3, 14)
    assert espn_date("2026-03-15T02:30Z") == datetime.date(2026, 3, 14)
    # An afternoon game agrees under both conventions.
    assert espn_date("2026-03-14T17:00Z") == datetime.date(2026, 3, 14)


def test_the_two_parsers_are_the_same_function():
    """full_pipeline and backtesting/historical each carried a byte-for-byte
    copy, so the same defect existed twice. Derive, do not duplicate."""
    from backend.pipeline import full_pipeline
    from backend.backtesting import historical
    assert full_pipeline.espn_date is historical.espn_date is espn_date


def test_dst_boundary_is_handled_by_the_zone_not_a_fixed_offset():
    """ET is -5 in winter and -4 in summer. A fixed offset would mis-date
    every game for half the year."""
    assert espn_date("2026-01-15T00:30Z") == datetime.date(2026, 1, 14)  # EST
    assert espn_date("2026-07-15T03:30Z") == datetime.date(2026, 7, 14)  # EDT
```

- [ ] **Step 2: Run them and watch them fail** (`No module named
  'backend.time_utils'`).

- [ ] **Step 3: Create `backend/time_utils.py`**

```python
"""Shared time handling. One ET, one ESPN date parser.

``ET`` was defined separately in ``digest/job.py`` and ``pipeline/scheduler.py``,
and the ESPN date parser existed as a byte-for-byte copy in
``pipeline/full_pipeline.py`` and ``backtesting/historical.py`` -- with the
same defect in both.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def espn_date(date_str: str) -> date:
    """The Eastern calendar date ESPN files an event under.

    ESPN timestamps events in UTC but groups its scoreboard by Eastern date, so
    anything starting after 8pm ET carries the *next* UTC day. Taking
    ``.date()`` off the UTC datetime files every evening game a day late --
    which is how the same game came to exist twice in this database, once per
    convention.

    ``ZoneInfo`` rather than a fixed offset: ET is -5 in winter and -4 in
    summer, and a constant would mis-date half the year.
    """
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(ET).date()
```

- [ ] **Step 4: Replace both copies.** Delete `_parse_date` from
  `full_pipeline.py` and `historical.py`, import `espn_date` in each, and
  update call sites. Re-point `digest/job.py` and `scheduler.py` at
  `time_utils.ET` and delete their local definitions. **Leave
  `_parse_start_time` alone** — it is correct.

- [ ] **Step 5: Run the tests, watch them pass, then the full suite.**

Expect failures in tests that assert a parsed date. **Each one must be checked
against the ET convention, not simply updated to match the new output** — that
is how a fix gets papered over. `test_historical.py::test_parse_date_iso` has two
assertions and **only one of them changes** — verified while writing this plan:

| assertion | under ET | |
|---|---|---|
| `_parse_date("2026-03-14T00:00Z") == date(2026, 3, 14)` | **2026-03-13** | changes (that instant is 7pm ET the previous day) |
| `_parse_date("2026-01-05T19:30:00+00:00") == date(2026, 1, 5)` | 2026-01-05 | unchanged (2:30pm ET, same day) |

Updating both would hide the fact that the second one was always right.
Work each failing expectation out from its raw timestamp.

- [ ] **Step 6: Mutation-prove** — revert `espn_date` to `.date()` on the UTC
  datetime and confirm the evening-game test fails.

- [ ] **Step 7: Commit.**

---

### Task 3: Report duplicates; do not merge them

**Files:**
- Create: `backend/scripts/report_duplicate_games.py` (read-only)
- Test: `backend/tests/test_report_duplicate_games.py` (new)

- [ ] **Step 1: Write the report.** Read-only; writes no rows. For each group
  sharing `(sport, home_team_id, away_team_id)` within ±1 day, print both rows
  with id, date, status, scores, `espn_id`, and counts of referencing `picks`
  and `pick_results`.

- [ ] **Step 2: Classify, and be explicit about what is unknowable.**

  - **Same `espn_id`** → certainly the same game. Safe to merge, later.
  - **Different `espn_id`** → certainly distinct. An MLB series, not a bug.
  - **Either side NULL** → **unknown.** Most rows predate Task 1 and will land
    here until the backfill has run. The report must say "unknown", not guess.

- [ ] **Step 3: Run it against a copy and report the three-way split.**

That output is the input to a future merge decision. **This plan stops here.**

---

## STOP conditions

1. **You are about to flip `_parse_date` before `espn_id` is populated on the
   target database.** That creates a duplicate for every evening game already
   stored under its UTC date — the opposite of the goal.
2. **A test that asserts a date starts failing and you cannot say which
   convention is correct for it.** Work it out from the raw timestamp before
   touching the expectation.
3. **The `espn_id` backfill match rate is low for ncaab.** Expected, and not
   this plan's problem: those teams have display names in the `abbreviation`
   column (`"Pennsylvania Quakers"`), found during plan 013. Report and move
   on.
4. **You are about to merge or delete a duplicate game row.** Out of scope.
   Task 3 reports only.
5. **The full suite drops below 599 passing** at any point.

## Verification

Baseline before starting: **599 passing**, 0 failed, on 3.12 and 3.14.

After Task 2, re-running plan 013's catch-up against a copy should finalize the
17 NBA rows it previously could not — that is the observable proof this worked.

## Out of scope

- **Merging historical duplicates.** It needs a rule for which row survives
  when both carry picks or results, and Task 3's report is the input to that
  decision. Its own plan.
- **ncaab teams with display names in `abbreviation`.** 60 rows cannot match
  ESPN at all. Found in plan 013; needs a team-identity fix, not a date fix.
- **`EspnStatsSource._find_team_id` does not exist**, so
  `fetch_season_averages` raises `AttributeError` and the season-average
  fallback is dead. Unrelated to dates, found in the same investigation, and
  P1 in its own right given `nba_api` cannot reach `stats.nba.com` from here.
- **`historical.store_games`'s comment at line 70** describing "espn_id-like
  uniqueness (date + teams)" becomes obsolete once Task 1 lands. Update it
  there rather than leaving a comment that describes a workaround for a field
  that now exists.

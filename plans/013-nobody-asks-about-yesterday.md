# Plan 013: Nobody asks ESPN about yesterday, so games never finalize

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
> `git diff --stat e0c6809..HEAD -- backend/pipeline/full_pipeline.py backend/pipeline/scheduler.py backend/config.py backend/collectors/espn.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

**Goal:** Make the daily pipeline capable of finalizing a game that has been
played, and use that same path to clear the backlog of 570 past games still
marked non-final.

**Architecture:** `morning_scout` asks ESPN about `today` and nothing else, at
8/9/10am ET — before any of that day's games have been played. Nothing ever
revisits a past date, so a score can only land by accident of timing, which
this schedule never produces. The fix is a lookback window: reconcile the last
N days as well as today, in **finalize-only** mode so a lookback can never
cancel a row it merely failed to match.

**Tech Stack:** Python 3.14 local / 3.12 production, SQLAlchemy 2.0, httpx,
APScheduler, pytest.

**Spec:** none. Derived from the 2026-09-17 spike recorded here. Read
"Why this matters" as the spec.

## Status

- **Priority**: P0 — every other plan's output is downstream of this
- **Effort**: M
- **Risk**: MEDIUM — it writes game status and scores, and the existing
  reconciliation path can mark rows `canceled`
- **Depends on**: none
- **Category**: bug (composition, not component)
- **Blocks**: plan 010's grading (needs `final`), plan 010's box-score
  collector (walks final games only), plan 012 (needs `game_log`, which needs
  box scores), `elo_history` growth, and `digest.enabled`
- **Planned at**: commit `e0c6809`, 2026-09-17

## Global Constraints

- Python floor `>=3.12`; CI runs **3.12 and 3.14** and both must pass.
- **Never run the catch-up against `sports_picks.db` directly.** Copy it first.
  It has 708 picks and 1664 games.
- ESPN's scoreboard needs **no API key** and is not on the Odds API budget, so
  a lookback window costs nothing but wall-clock. Do not add it to the budget
  accounting.
- Tests must be network-free (`pytest-httpx`, exact `url=` strings — see
  `test_mlb_stats.py:46`) and mutation-proved.

## Why this matters

`fetch_and_store_games(session, sports, target_date)` fetches a **single
date's** scoreboard:

```python
date_str = target_date.strftime("%Y%m%d")
games = await espn.fetch_scoreboard(sport, date_str)
```

It is called from exactly one place, `scheduler.py:147`:

```python
today = date.today()
asyncio.run(fetch_and_store_games(session, scheduled_sports, today))
```

and the only jobs that reach it are `morning_scout` at **8am, 9am and 10am ET**
(`scheduler.py:81-92`). So for every game:

| time | state |
|---|---|
| 8/9/10am ET on game day | ESPN reports `scheduled` — not yet played |
| that evening | the game is played |
| next morning | the scheduler asks about the **new** today |

**No code path ever asks ESPN about a date in the past.** The upsert that would
finalize a game is correct and unreachable (`full_pipeline.py:176-179`):

```python
if g["status"] == "final" and existing.status != "final":
    existing.home_score = g["home_score"]
    existing.away_score = g["away_score"]
    existing.status = g["status"]
```

Every component works. The composition does not. That is why this survived a
nine-plan audit: there is no error to find, no exception to log, and no test to
fail — only a question nobody asks.

### What it has cost

Measured on production, 2026-09-17:

```
  games with a PAST date still not final:
     nba      239   2026-01-09 .. 2026-06-20
     mma      126   2026-03-16 .. 2026-08-02
     boxing   110   2026-03-20 .. 2026-07-05
     ncaab     81   2026-03-15 .. 2026-03-23
     mlb       14   2026-05-23 .. 2026-05-24

  most recent FINAL game:  nba 2026-05-24, mlb 2026-05-24, ncaab 2026-03-20
```

The finals that do exist almost certainly came from
`backtesting/historical.py:store_games`, a whole-season loader whose entry
point `load_historical_data` has **no callers** — i.e. a one-off manual run,
not the daily pipeline.

Downstream, everything built in plans 010-012 is dormant because of it:

- `grade_pending_picks` filters `Game.status == "final"` → `pick_results` has
  **0 rows**, and all 82 props are ungraded.
- `collect_box_scores_for_final_games` walks final games only → `game_log` has
  **0 rows**.
- Plan 012's fix needs three `game_log` rows per player to engage the
  distribution model → it would change nothing today.
- `elo_history` stops growing, which reintroduces exactly the staleness plan
  008 fixed.

### A second, smaller gap

`scheduler.py:141`:

```python
scheduled_sports = [s for s in active_sports if s in ("nba", "nfl")]
```

Only NBA and NFL are fetched at all, which is why ncaab has 81 stuck rows.
`mma` and `boxing` have their own ingestion (`fetch_ufc_events` writes finals
directly, and `grade_completed_games` owns their Elo), so they are **not** in
scope here.

## Current state (verified 2026-09-17 at `e0c6809`)

Helpers this plan needs already exist and take the parameters required:

- `config.is_sport_in_season(sport, seasons, today: date | None = None)` —
  already accepts a date override, so a lookback day can be tested against the
  season that applied **on that day**.
- `collectors/espn.ESPNCollector.fetch_scoreboard(sport, date_str)` — takes any
  date, needs no key.
- `full_pipeline._reconcile_against_espn(session, sport, target_date, espn_pairs)`
  — marks non-final rows `canceled` when ESPN's list for that date omits their
  team pair. Skips `final` and `in_progress`. **This is the hazard.**

## The reconciliation hazard, and the chosen shape

`_reconcile_against_espn` exists so a postponed game disappears from Today's
Picks. Applied to a **past** date it is dangerous in a way it is not for today:
it matches on an unordered team-id pair, so any abbreviation drift or team-row
duplication makes a real game look absent, and the row is marked `canceled`
rather than finalized. That turns "we failed to match it" into "it did not
happen", silently, and on a row that should have become `final`.

**So the lookback runs in finalize-only mode: it upserts scores and status, and
never reconciles.** Today's pass keeps reconciling exactly as it does now.
A lookback can only ever move a row forward to `final`.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/pipeline/full_pipeline.py` | `reconcile` flag; lookback-capable fetch | 1 |
| `backend/pipeline/scheduler.py` | `morning_scout` walks a lookback window; covers in-season team sports | 1, 2 |
| `backend/scripts/catch_up_finals.py` | **new** — one-off backlog clear over the same path | 3 |
| `backend/tests/test_game_finalization.py` | **new** | 1, 2 |

---

### Task 1: Give the pipeline a finalize-only lookback

**Files:**
- Modify: `backend/pipeline/full_pipeline.py:16-27` and `:135`
- Modify: `backend/pipeline/scheduler.py:145-147`
- Test: `backend/tests/test_game_finalization.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `fetch_and_store_games(session, sports, target_date, *, reconcile: bool = True) -> int`
  - `_store_games(session, sport, target_date, games, *, reconcile: bool = True) -> int`
  - `LOOKBACK_DAYS = 3` in `backend/pipeline/scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_game_played_yesterday_is_finalized_today(db_session, httpx_mock):
    """The whole bug in one test. morning_scout asks about today at 8am, so a
    game played last night is never revisited and stays 'scheduled' forever.
    """
```

Seed a `scheduled` NBA game dated yesterday with NULL scores. Stub ESPN's
scoreboard for **yesterday's** date returning that game as `final` with scores,
and for today returning `{"events": []}`. Call `morning_scout`. Assert the game
is `final` with the scores.

```python
def test_a_lookback_day_never_cancels_a_row_it_could_not_match(db_session, httpx_mock):
    """Finalize-only. _reconcile_against_espn matches on an unordered team-id
    pair, so abbreviation drift makes a real game look absent -- and on a past
    date the row would be marked 'canceled' instead of 'final'. That converts
    "we failed to match" into "it did not happen", on exactly the rows this
    plan exists to rescue.
    """
```

Seed two `scheduled` games dated yesterday. Stub yesterday's scoreboard
returning **only one** of them, as `final`. Assert the listed one becomes
`final` **and the unlisted one is still `scheduled`, not `canceled`**.

```python
def test_todays_pass_still_reconciles(db_session, httpx_mock):
    """The lookback must not weaken today's behaviour: a postponed game still
    has to drop out of Today's Picks."""
```

Seed a `scheduled` game dated today. Stub today's scoreboard with a different
game (non-empty, so reconciliation is not skipped). Assert the seeded game
becomes `canceled`.

- [ ] **Step 2: Run all three and watch the first two fail**

```
.venv/Scripts/python.exe -m pytest backend/tests/test_game_finalization.py -q
```

Expected: the first two FAIL, the third passes (it describes today's existing
behaviour). If the first one passes, the bug is already fixed — STOP.

- [ ] **Step 3: Thread a `reconcile` flag through**

`full_pipeline.py`, both signatures:

```python
async def fetch_and_store_games(session: Session, sports: list[str],
                                target_date: date, *,
                                reconcile: bool = True) -> int:
    """Fetch one date's games from ESPN and store them.

    ``reconcile=False`` is finalize-only: scores and status are upserted, but
    :func:`_reconcile_against_espn` is skipped. Used for lookback days, where a
    team-pair match failure would mark a real game ``canceled`` instead of
    ``final`` -- turning a matching bug into a data loss on exactly the rows a
    lookback exists to rescue.
    """
```

```python
def _store_games(session: Session, sport: str, target_date: date,
                 games: list[dict], *, reconcile: bool = True) -> int:
```

and guard the existing reconciliation call site with `if reconcile:`. Read the
surrounding lines to find it — it is called after the upsert loop, conditional
on ESPN having returned at least one event.

- [ ] **Step 4: Walk the window in `morning_scout`**

Replace `scheduler.py:145-147`:

```python
        today = date.today()
        try:
            asyncio.run(fetch_and_store_games(session, scheduled_sports, today))
```

with:

```python
        today = date.today()
        # Today's pass reconciles (a postponed game must drop out of Today's
        # Picks). The lookback days are finalize-only: their job is to capture
        # scores for games that had not been played when this ran yesterday.
        # Without them nothing ever asks ESPN about a past date, and a game's
        # score never lands -- 570 rows were stuck that way on 2026-09-17.
        windows = [(today, True)] + [
            (today - timedelta(days=d), False) for d in range(1, LOOKBACK_DAYS + 1)
        ]
        try:
            for day, reconcile in windows:
                day_sports = [
                    s for s in scheduled_sports
                    if is_sport_in_season(s, config["seasons"], today=day)
                ]
                if not day_sports:
                    continue
                asyncio.run(fetch_and_store_games(
                    session, day_sports, day, reconcile=reconcile))
```

Add `LOOKBACK_DAYS = 3` near the top of `scheduler.py` with a comment, and
`timedelta` to the existing `datetime` import. Note `is_sport_in_season` is
re-evaluated **per day** — a lookback into last week may cross a season
boundary, and asking about a sport out of season that day is a wasted request.

- [ ] **Step 5: Run the three tests and watch them pass.**

- [ ] **Step 6: Mutation-prove both guards.** Set `LOOKBACK_DAYS = 0` and
  confirm test 1 fails. Then force `reconcile=True` for lookback days and
  confirm test 2 fails. Restore.

- [ ] **Step 7: Full suite, then commit**

```
.venv/Scripts/python.exe -m pytest backend/tests -q
git commit -m "fix(scheduler): finalize games played since the last run"
```

Expected: **595 + 3 = 598 passing**, 0 failed.

---

### Task 2: Cover every in-season team sport, not just NBA and NFL

**Files:**
- Modify: `backend/pipeline/scheduler.py:141`
- Test: `backend/tests/test_game_finalization.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: no signature change; `scheduled_sports` widens.

- [ ] **Step 1: Write the failing test**

```python
def test_an_in_season_college_game_is_fetched_too(db_session, httpx_mock):
    """ncaab had 81 past games stuck non-final because scheduled_sports was
    hard-coded to ("nba", "nfl"). ESPN's scoreboard needs no API key, so
    breadth here costs wall-clock, not budget.
    """
```

- [ ] **Step 2: Run it and watch it fail** (no request is made for ncaab).

- [ ] **Step 3: Widen the filter**

```python
        # Team sports whose games come from ESPN's scoreboard. mma and boxing
        # are excluded deliberately: fetch_ufc_events writes their finals
        # directly and grade_completed_games owns their Elo, so pulling them
        # through this path would create a second writer.
        ESPN_TEAM_SPORTS = ("nba", "nfl", "ncaab", "ncaaf", "mlb")
        scheduled_sports = [s for s in active_sports if s in ESPN_TEAM_SPORTS]
```

Define `ESPN_TEAM_SPORTS` at module level next to `LOOKBACK_DAYS`, not inline.
**Verified 2026-09-17:** `collectors/espn.SPORT_URLS` contains
`mlb, mma, nba, ncaab, ncaaf, nfl`, so all five sports above are covered and
none is a silent no-op. Re-check anyway if the drift check showed movement in
`collectors/espn.py` — a sport absent from `SPORT_URLS` makes
`fetch_scoreboard` return `[]` rather than raising.

- [ ] **Step 4: Run it, watch it pass, then the full suite. Commit.**

---

### Task 3: Clear the 570-game backlog over the same path

A lookback of 3 days will not reach January. The backlog needs one deliberate
pass — but through the **same** code, not a parallel script with its own
semantics.

**Files:**
- Create: `backend/scripts/catch_up_finals.py`
- Test: `backend/tests/test_catch_up_finals.py` (new)

**Interfaces:**
- Consumes: Task 1's `reconcile=False`.
- Produces: a CLI that calls `fetch_and_store_games(..., reconcile=False)` once
  per distinct date that has a non-final past game.

- [ ] **Step 1: Write the script, modelled on `backfill_prop_fields.py`**

Copy its safety exactly: `--db` **required** with no default, a
`FileNotFoundError` for a path that does not exist, `--dry-run`, and a summary
by sport. Derive the date list from the database rather than iterating a
calendar:

```sql
SELECT DISTINCT sport, date(date) FROM games
WHERE status != 'final' AND date(date) < :today
```

That is ~40-80 ESPN requests rather than 250 days x 5 sports, and it asks only
about dates that actually need it. Add `--sport` and `--since` to bound a run.

**Always `reconcile=False`.** The same argument as Task 1, only stronger: these
rows are months old and the cost of a mismatch is marking a played game
`canceled`.

- [ ] **Step 2: Test it**

Stub ESPN per date with `pytest-httpx`. Assert: only dates with non-final past
games are requested; a matched game becomes `final`; an unmatched row stays
`scheduled`; `--dry-run` writes nothing; a nonexistent `--db` raises.

- [ ] **Step 3: Dry-run against a copy**

```bash
cp sports_picks.db /tmp/catchup.db
.venv/Scripts/python.exe -m backend.scripts.catch_up_finals --db 'C:\path\to\catchup.db' --dry-run
```

Record the number of dates and the per-sport counts. **Expect roughly 570 rows
across the five sports listed above; mma and boxing will be reported as out of
scope.**

- [ ] **Step 4: Run it against the copy and verify**

Check, on the copy:
- how many rows moved to `final`, by sport
- that **no** row moved to `canceled`
- that `picks`, `pick_results` and `team_stats` counts are unchanged by this
  script itself

**If a large fraction does not finalize, stop and report the reason before
touching production.** The likely causes are team-abbreviation drift and
`_ensure_team` having created duplicate team rows — both are real findings, not
things to work around.

- [ ] **Step 5: Run the downstream chain on the same copy**

Now that games are final, the rest of the session's work should engage for the
first time:

```
morning_scout equivalent: collect_box_scores_for_final_games -> grade_pending_picks
```

Report `pick_results` by `pick_type`, and `player_stats` `game_log` row count.
**This is the first end-to-end exercise of plans 010-013 against real data.**

- [ ] **Step 6: Production, with a backup first**

```bash
cp sports_picks.db "sports_picks.backup-$(date +%Y%m%d-%H%M%S).db"
```

Verify the backup with `pragma integrity_check` and a `picks` count before
writing anything, exactly as the 2026-09-16 backfill did. Then dry-run, then
run. Confirm `picks` max id is still 708 afterwards.

---

## STOP conditions

1. **Test 1 in Task 1 passes before the fix.** The stub is probably not being
   reached; check that the mocked URL matches what `ESPNCollector` builds.
2. **Any lookback or catch-up run marks a row `canceled`.** That is the hazard
   this plan is shaped to avoid. Stop and find out why `reconcile=True` was in
   effect.
3. **A large fraction of the backlog does not finalize** in Task 3 Step 4.
   Report the cause — likely abbreviation drift or duplicate team rows — rather
   than loosening the match.
4. **You are about to run the catch-up against `sports_picks.db` without a
   verified backup.**
5. **A sport added in Task 2 is missing from `collectors/espn.SPORT_URLS`.**
   `fetch_scoreboard` returns `[]` for it, so it would be a silent no-op.
6. **The full suite drops below 595 passing** at any point.

## Verification

Baseline before starting: **595 passing**, 0 failed, on 3.12 and 3.14.

After Task 3 Step 5, the numbers that say this worked:

| | before | expected after |
|---|---|---|
| past games not `final` | 570 | near 0 for nba/ncaab/mlb |
| `pick_results` | 0 | non-zero |
| `player_stats` `game_log` | 0 | non-zero |

## Out of scope

- **`mma` and `boxing`.** 236 stuck rows between them, but they have their own
  ingestion and their own Elo convention (post-game, per
  `[[sports-picks-elo-basis]]`). Pulling them through the ESPN scoreboard path
  would create a second writer for the same rows. Their own plan.
- **Plan 012.** Runs *after* this one, and only becomes observable because of
  it. Do not merge the two.
- **Whether `LOOKBACK_DAYS = 3` is the right number.** Three covers a missed
  weekend. It is a guess, not a measurement; revisit once the catch-up has run
  and the steady-state gap is visible.
- **`backtesting/historical.py:load_historical_data` still has no callers.**
  It is the whole-season loader that produced the finals that do exist. After
  this plan the daily path can finalize games, so that loader's role shrinks to
  seeding a new season — decide then whether to wire it or delete it, alongside
  the `EloRating` decision already open in `plans/HANDOFF.md`.

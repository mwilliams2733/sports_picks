# Plan 001: Pin the paper-trading money path with characterization tests

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 5c2e0d0..HEAD -- backend/api/users.py backend/tests/`
> If `backend/api/users.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `5c2e0d0`, 2026-09-16

## Why this matters

`backend/api/users.py` is 656 lines and contains the entire paper-trading money
path: balance computation, stake validation, single-pick grading, parlay
combined-odds math, parlay grading, and streak tracking. **No test file imports
it.** A grep for `backend.api.users` across `backend/tests/` returns nothing.

Four separate confirmed bugs live in this file (parlay payouts never reaching
balance, parlays never settling, pending stakes not reserved, grading logic
duplicated with the scheduler). Those bugs will be fixed in later plans that
rewrite the balance formula. Rewriting untested money code is how you trade one
bug for three.

**This plan does NOT fix any bug.** It writes tests that pin down what the code
does *today*, including the buggy behavior, so that later plans can change
behavior deliberately and see exactly what moved. Where a test documents
behavior that is known to be wrong, it is marked with an
`# CHARACTERIZATION (known bug, see plans/NNN)` comment so the next executor
knows it is expected to change that assertion.

## Current state

### Files

- `backend/api/users.py` — the module under test. Router mounted at prefix
  `/users` (see `backend/api/main.py:101`). No tests exist for it.
- `backend/tests/conftest.py` — provides `db_engine` / `db_session` fixtures.
  Note: the existing API tests do **not** use these; they build their own app.
- `backend/tests/test_api_picks.py` — **the structural pattern to follow.**

### Test conventions in this repo (match these exactly)

Every API test builds its own in-memory app and seeds through a helper. From
`backend/tests/test_api_picks.py:1-31`:

```python
from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models import Base, Team, Game, PickModel, PickResult, StrategyModel

def _seed_db(client):
    engine = client.app.state.engine
    Base.metadata.create_all(engine)
    from backend.database import get_session
    session = get_session(engine)
    t1 = Team(id=1, name="Boston Celtics", abbreviation="BOS", sport="nba")
    t2 = Team(id=2, name="LA Lakers", abbreviation="LAL", sport="nba")
    g = Game(id=1, sport="nba", season="2025-26", date=date.today(),
             home_team_id=1, away_team_id=2, status="scheduled")
    session.add_all([t1, t2, g])
    session.commit()
    session.close()

def test_get_today_picks():
    app = create_app(":memory:")
    client = TestClient(app)
    _seed_db(client)
    response = client.get("/picks/today")
    assert response.status_code == 200
```

Key conventions: plain `def` test functions (no classes), `create_app(":memory:")`
per test, a module-level `_seed_*` helper, direct `assert response.status_code`.
There is no `pytest.ini` / `[tool.pytest.ini_options]`; tests are discovered by
default rules.

### Model facts the seed helper needs

From `backend/models.py`:

- `Game` requires `sport`, `season`, `date`, `home_team_id`, `away_team_id`
  (all `nullable=False`). `status` defaults to `"scheduled"`. A game counts as
  gradeable when `status == "final"` **and** both `home_score` and `away_score`
  are non-null.
- `Team` requires `name`, `abbreviation`, `sport`.
- `UserProfile` has `name` (unique), `starting_balance` (default `10000.0`),
  `current_streak`, `best_streak`, `streak_type`.
- `PaperPick` has `user_id`, `game_id`, `pick_type`, `pick_value`, `odds`,
  `stake`, `result` (null = pending), `payout`, `prop_market`, `prop_player`,
  `parlay_id`.
- `Parlay` has `user_id`, `stake`, `combined_odds`, `result`, `payout`.

**A `Game` cannot be created with a bare string team name** — `home_team_id`
is a foreign key. Seed `Team` rows first and use `session.flush()` to get ids.

### The endpoints under test

From `backend/api/users.py`, in registration order:

| Method | Path | Handler | Line |
|---|---|---|---|
| GET | `/users/` | `list_users` | 61 |
| POST | `/users/` | `create_user` | 103 |
| DELETE | `/users/{user_id}` | `delete_user` | 119 |
| GET | `/users/{user_id}` | `get_user` | 130 |
| POST | `/users/{user_id}/picks` | `place_pick` | 171 |
| GET | `/users/{user_id}/picks` | `get_user_picks` | 275 |
| POST | `/users/{user_id}/parlay` | `place_parlay` | 318 |
| POST | `/users/grade` | `grade_paper_picks` | 458 |
| GET | `/users/{user_id}/stats` | `get_user_stats` | 590 |
| GET | `/users/feed` | `get_activity_feed` | 634 |

**`GET /users/feed` currently returns 422**, because `GET /users/{user_id}` is
registered first and FastAPI matches routes in registration order. This is a
known bug fixed in plan 004. Your characterization test must assert the
**current** 422 behavior and be marked as a characterization test.

### The balance formula as it exists today

`backend/api/users.py:181-186` (inside `place_pick`):

```python
        # Calculate current balance
        picks = session.query(PaperPick).filter(PaperPick.user_id == user_id).all()
        total_payout = sum(p.payout or 0 for p in picks)
        current_balance = user.starting_balance + total_payout

        if body.stake > current_balance:
            raise HTTPException(status_code=400, detail="Insufficient balance")
```

The identical formula is repeated at `users.py:74-76` (`list_users`),
`:149-151` (`get_user`), and `:331-336` (`place_parlay`). Note it sums
`PaperPick.payout` only — pending picks have `payout = None` and contribute 0,
and parlay payouts live on `Parlay.payout`, which is never summed.

### Parlay leg storage

`backend/api/users.py:400-412` — legs are stored with `stake=0` and `payout=0`:

```python
            pick = PaperPick(
                user_id=user_id,
                game_id=leg.game_id,
                pick_type=leg.pick_type,
                pick_value=leg.pick_value,
                odds=leg.odds,
                stake=0,  # Individual legs have 0 stake; parlay has the stake
                result=result,
                payout=0,
                prop_market=leg.prop_market,
                prop_player=leg.prop_player,
                parlay_id=parlay.id,
            )
```

### Parlay combined-odds math

`backend/api/users.py:339-350`:

```python
        combined_decimal = 1.0
        for leg in body.legs:
            if leg.odds < 0:
                combined_decimal *= 1 + (100 / abs(leg.odds))
            else:
                combined_decimal *= 1 + (leg.odds / 100)

        # Convert back to American odds
        if combined_decimal >= 2.0:
            combined_american = int(round((combined_decimal - 1) * 100))
        else:
            combined_american = int(round(-100 / (combined_decimal - 1)))
```

Two `-110` legs give `combined_decimal = (1 + 100/110)^2 = 3.6446...`, so
`combined_american = round(2.6446 * 100) = 264` and a $500 stake has
`potential_payout = round(500 * 2.6446, 2) = 1322.31`. **These exact numbers are
verified** — use them as expected values.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Run new tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q` | all pass |
| Run full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | `360 passed` + your new tests, 0 failed |
| Run one test | `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py::test_name -q` | passes |

Run from the repo root `C:\Users\mwill\Documents\mwilliams2733\sports_picks`.
The full suite takes ~4 minutes; use the single-file command while iterating.

## Scope

**In scope** (the only files you may modify or create):
- `backend/tests/test_api_users.py` (create)

**Out of scope** (do NOT touch, even though they look related):
- `backend/api/users.py` — **this plan fixes nothing.** If a test you write
  fails because the production code is buggy, that is the expected outcome:
  assert the buggy behavior and mark it `# CHARACTERIZATION`.
- `backend/pipeline/scheduler.py` — its grading path is tested separately in
  `backend/tests/test_scheduler_lifespan.py`.
- `backend/tests/conftest.py` — the existing API tests don't use its fixtures;
  don't add fixtures there for this.
- Any other existing test file.

## Git workflow

- Branch: `advisor/001-paper-trading-tests`
- Commit style is conventional commits (from `git log`):
  `test(api): add characterization tests for paper-trading money path`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Create the file and the seed helper

Create `backend/tests/test_api_users.py`. Write the imports and a
`_seed_games(client, specs)` helper that creates `Team` pairs and `Game` rows
and returns the list of created game ids.

`specs` is a list of dicts like
`{"status": "final", "home_score": 110, "away_score": 100}` or
`{"status": "scheduled"}`. Create a fresh `Team` pair per game so no
uniqueness assumptions are needed. Use `session.flush()` after adding teams to
populate their ids before constructing the `Game`.

Also write a `_make_user(client, name="tester")` helper that POSTs to
`/users/` and returns the created user's `id`.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ `no tests ran` (or collected 0) with **exit code 5 and no collection errors**.
An ImportError here means the file is broken — fix before continuing.

### Step 2: User CRUD tests

Write these tests:

1. `test_create_user_returns_starting_balance` — POST `/users/` with
   `{"name": "alice"}` → 200, body has `starting_balance == 10000.0`.
2. `test_create_duplicate_user_rejected` — create "alice" twice → second is
   400 with detail `"Username already taken"`.
3. `test_get_unknown_user_404` — GET `/users/999` → 404.
4. `test_delete_user_removes_picks` — create a user, place a pick on a final
   game, DELETE the user, then GET the user → 404.
5. `test_list_users_sorted_by_balance_desc` — create two users, give one a
   winning graded pick, assert `GET /users/` returns the higher balance first.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ 5 passed.

### Step 3: Single-pick placement and grading tests

1. `test_place_pick_on_scheduled_game_is_pending` — place on a `scheduled`
   game → 200, `result is None`, `payout is None`.
2. `test_place_pick_on_final_game_grades_immediately_win` — game final
   110–100, pick `pick_type="moneyline"`, `pick_value="HOME ML"`, `odds=-110`,
   `stake=100` → `result == "win"`, `payout == pytest.approx(90.909, rel=1e-3)`
   (because `calculate_payout(-110) == 100/110`).
3. `test_place_pick_zero_stake_rejected` — `stake=0` → 400
   `"Stake must be positive"`.
4. `test_place_pick_unknown_game_404` — `game_id=9999` → 404.
5. `test_place_pick_exceeding_balance_rejected` — `stake=10001` on a fresh
   user → 400 `"Insufficient balance"`.
6. `test_pending_stakes_are_not_reserved` — **CHARACTERIZATION (known bug,
   fixed in a later plan)**: place five separate `stake=10000` picks on a
   `scheduled` game for a user with a $10,000 balance. Assert **all five return
   200** and `GET /users/{id}` still reports `current_balance == 10000.0`.
   Add a comment stating that after the bankroll-reservation fix, picks 2-5
   must return 400 and this assertion must be inverted.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ 11 passed.

### Step 4: Parlay tests

1. `test_parlay_requires_two_legs` — one leg → 400
   `"Parlay requires at least 2 legs"`.
2. `test_parlay_combined_odds_two_minus_110_legs` — two `-110` legs, stake 500,
   both games final and won → response `combined_odds == 264` and
   `potential_payout == 1322.31`.
3. `test_parlay_payout_missing_from_balance` — **CHARACTERIZATION (known bug,
   fixed in a later plan)**: same winning parlay as above, then
   `GET /users/{id}` → assert `current_balance == 10000.0` and `profit == 0`,
   i.e. the parlay's $1322.31 profit is **absent**. Comment that after the fix
   these must become `11322.31` / `1322.31`.
4. `test_parlay_on_scheduled_games_never_settles` — **CHARACTERIZATION (known
   bug)**: place a 2-leg parlay where one game is `scheduled`; assert response
   `result is None`. Then mark that game final with a winning score, POST
   `/users/grade`, and assert the `Parlay` row **still** has `result is None`.
   Read the row via `get_session(client.app.state.engine)` and
   `session.get(Parlay, 1)`. Comment that after the fix it must become `"win"`.
5. `test_parlay_legs_stored_with_zero_stake` — assert each created `PaperPick`
   with a non-null `parlay_id` has `stake == 0`.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ 16 passed.

### Step 5: Bulk grading, streaks, stats, feed

1. `test_grade_endpoint_grades_pending_picks` — place a pick on a `scheduled`
   game, flip the game to final + scores, POST `/users/grade` →
   `{"graded": 1}`, and the pick's `result` is set.
2. `test_grade_endpoint_skips_games_without_scores` — game `status="final"`
   but `home_score=None` → `{"graded": 0}`.
3. `test_win_streak_counted` — grade three winning picks, assert
   `GET /users/` shows `current_streak == 3` and `streak_type == "win"`.
4. `test_user_stats_shape` — GET `/users/{id}/stats` → 200 with keys
   `today`, `this_week`, `this_month`, `all_time`, `daily_breakdown`.
5. `test_feed_route_is_shadowed` — **CHARACTERIZATION (known bug, fixed in
   plan 004)**: `GET /users/feed` → assert `status_code == 422`. Comment that
   after plan 004 this must become 200 returning a list.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_users.py -q`
→ 21 passed.

### Step 6: Confirm the suite is green and the guards are real

Per this repo's evidence rules: **a test that still passes when you break the
implementation is not a test.** Prove at least one guard:

Temporarily change `backend/api/users.py:185` from
`if body.stake > current_balance:` to `if False:` and run
`test_place_pick_exceeding_balance_rejected`. It **must fail**. Then revert
the change with `git checkout -- backend/api/users.py` and confirm
`git status` shows `backend/api/users.py` unmodified.

**Verify**:
1. During mutation: that one test fails.
2. After revert: `git status --short` lists only
   `backend/tests/test_api_users.py` (and nothing under `backend/api/`).
3. `.venv/Scripts/python.exe -m pytest backend/tests -q` → `381 passed`
   (360 existing + 21 new), 0 failed.

## Test plan

All 21 tests listed in steps 2-5, in the single new file
`backend/tests/test_api_users.py`, modeled structurally on
`backend/tests/test_api_picks.py`.

Six of them are characterization tests that encode known-wrong behavior and
are expected to be inverted by later plans. Each must carry the comment
`# CHARACTERIZATION (known bug, see plans/README.md)`.

Verification: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass.

## Done criteria

ALL must hold:

- [ ] `backend/tests/test_api_users.py` exists with 21 passing tests
- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0 with
      `381 passed`
- [ ] `grep -c "CHARACTERIZATION" backend/tests/test_api_users.py` returns `4`
      (the four tests named in steps 3.6, 4.3, 4.4 and 5.5 — do NOT invent
      extra ones to reach a count)
- [ ] `git status --short` shows **only** `backend/tests/test_api_users.py`
      as added — no modification to `backend/api/users.py`
- [ ] The step-6 mutation test was run and the guard failed as expected
- [ ] `plans/README.md` status row for 001 updated to DONE

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpts in "Current state" don't match the live code (drift since
  `5c2e0d0`).
- `GET /users/feed` returns **200** rather than 422 — that means plan 004
  already landed and the characterization test in step 5 is wrong; report
  rather than guessing which behavior to encode.
- The full suite has failures **before** you add any tests (run it once at the
  start to confirm the 360-passing baseline).
- You conclude a production-code change in `backend/api/users.py` is needed to
  make a test pass. It is not. Report instead.
- Total test count after your work is not exactly 381 — investigate before
  updating the index.

## Maintenance notes

- The six `CHARACTERIZATION` tests are **deliberate liabilities**. The plans
  that fix parlay accounting, bankroll reservation, and the feed route must
  each invert their corresponding assertion. If a future executor "fixes" a
  failing characterization test by reverting the production fix, that is
  backwards.
- A reviewer should check that no assertion in this file asserts on a value the
  test itself computed with the same formula as production — that tests nothing.
  The parlay odds expectations (`264`, `1322.31`) are hand-verified constants
  and must stay hardcoded.
- Deferred out of this plan: tests for `place_pick`'s prop-grading branch
  (`users.py:203-215`), because `grade_prop_pick` has its own confirmed
  direction bug for `player_anytime_td` that is fixed elsewhere. Adding
  characterization tests for it here would collide with that fix.

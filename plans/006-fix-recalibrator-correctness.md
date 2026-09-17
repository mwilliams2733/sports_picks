# Plan 006: Make the recalibrator compute per-sport, push-aware, newest-first numbers

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 5c2e0d0..HEAD -- backend/analysis/recalibrator.py backend/analysis/confidence.py backend/analysis/prop_confidence.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (but see "Why this matters" — it is **urgent once 002
  lands**)
- **Category**: bug
- **Planned at**: commit `5c2e0d0`, 2026-09-16

## Why this matters

Plan 002 wires the recalibrator's output into live pick generation for the
first time. Today that output is discarded, so its bugs are harmless. **The
moment 002 lands, these three defects start driving production confidence
tiers.** This plan should land immediately before or after 002 — not later.

Three independent bugs in the same ~60 lines:

1. **`Recalibrator` ignores its own `sport`.** The query filters only on
   confidence tier and date; `self.sport` is never applied. But the result is
   *written* as sport-specific, and the caller loops over four sports. So all
   four sports receive the **identical cross-sport pooled win rate**, stored
   under four different labels. Per-sport calibration is currently meaningless.
2. **Pushes are counted as losses.** `actual_rate = wins / total` where `total`
   includes `result == "push"` rows. Spreads and totals on whole numbers push
   regularly, so push-heavy tiers are systematically judged to be
   underperforming and get tightened for no reason.
3. **The "current threshold" baseline walks backwards.** Rows are ordered
   newest-first and then assigned into a dict in iteration order, so when a
   tier has several history rows the **oldest** of the batch overwrites the
   newest. The identical bug exists in `confidence.py:get_thresholds` and
   `prop_confidence.py:get_prop_thresholds` — the two functions plan 002 wires
   into production.

Bug 3 compounds with the others: a wrong baseline feeds a wrong adjustment,
which is persisted and becomes the next run's wrong baseline.

## Current state

### `backend/analysis/recalibrator.py` — the whole relevant body

Lines 30-40 (bug 3 — newest-first iteration overwritten by oldest):

```python
        # Get current thresholds
        thresholds = dict(DEFAULT_THRESHOLDS)
        latest = (
            self.session.query(CalibrationHistory)
            .filter(CalibrationHistory.sport == self.sport)
            .order_by(CalibrationHistory.date.desc())
            .limit(5)
            .all()
        )
        for row in latest:
            thresholds[row.confidence_tier] = row.new_threshold
```

`latest[0]` is the newest row. Assigning in that order means a later (older)
row with the same `confidence_tier` overwrites the newer one. `.limit(5)` is
also conceptually wrong: there are 5 tiers, but a single run may write 1-5 rows,
so the last 5 rows may cover only two tiers, or repeat one tier five times.
What is wanted is **the newest row per tier**, not the newest 5 rows.

Lines 44-64 (bugs 1 and 2):

```python
        for tier in (5, 4, 3, 2, 1):
            picks_with_results = (
                self.session.query(PickModel, PickResult)
                .join(PickResult, PickResult.pick_id == PickModel.id)
                .filter(
                    PickModel.confidence == tier,
                    PickModel.created_at >= cutoff,
                )
                .all()
            )

            total = len(picks_with_results)
            if total < MIN_PICKS_PER_TIER:
                logger.info(
                    "Tier %d: only %d picks (need %d), skipping",
                    tier, total, MIN_PICKS_PER_TIER,
                )
                continue

            wins = sum(1 for p, r in picks_with_results if r.result == "win")
            actual_rate = wins / total
```

No `Game.sport` filter (bug 1). `total` includes pushes (bug 2).

The result is then written as sport-specific at lines 88-97:

```python
            self.session.add(CalibrationHistory(
                date=date.today(),
                sport=self.sport,
                confidence_tier=tier,
                predicted_win_rate=expected_rate,
                actual_win_rate=actual_rate,
                sample_size=total,
                old_threshold=old_threshold,
                new_threshold=new_threshold,
            ))
```

Other constants in that module (do not change them):

```python
MIN_PICKS_PER_TIER = 20
ADJUSTMENT_STEP = 1.0  # percentage points per cycle
EXPECTED_WIN_RATES = {5: 0.70, 4: 0.63, 3: 0.57, 2: 0.53, 1: 0.50}
DEVIATION_THRESHOLD = 0.05  # 5 percentage points
```

### Schema facts you need

- `PickModel` (`backend/models.py:101-113`) has **no `sport` column**. Sport
  lives on `Game`. Reaching it requires joining `Game` on
  `PickModel.game_id == Game.id` — `PickModel` already has a
  `game = relationship("Game")`.
- `PickResult.result` is a non-null `String` holding `"win"`, `"loss"` or
  `"push"` (written by `grade_pick` in `backend/pipeline/grader.py`).
- `CalibrationHistory` (`backend/models.py:~232`) has `date`, `sport`,
  `confidence_tier`, `predicted_win_rate`, `actual_win_rate`, `sample_size`,
  `old_threshold`, `new_threshold`, `created_at`.

### The same ordering bug in the two threshold readers

`backend/analysis/confidence.py:5-19`:

```python
def get_thresholds(session=None, sport: str = "nba") -> dict:
    """Load latest confidence thresholds from DB, or return defaults."""
    if session is None:
        return DEFAULT_THRESHOLDS
    from backend.models import CalibrationHistory
    rows = (
        session.query(CalibrationHistory)
        .filter(CalibrationHistory.sport == sport)
        .order_by(CalibrationHistory.date.desc())
        .limit(5)
        .all()
    )
    if not rows:
        return DEFAULT_THRESHOLDS
    return {row.confidence_tier: row.new_threshold for row in rows}
```

A dict comprehension assigns left to right, so later (older) rows win. Same
defect. `backend/analysis/prop_confidence.py:4-21` is the identical shape, with
tiers offset by +100 on write and `- 100` on read — **preserve that encoding**.

Note both functions fall back to `DEFAULT_THRESHOLDS` wholesale when no rows
exist. A subtler issue worth fixing while here: if the DB has a row for tier 5
only, the current code returns a dict containing *only* tier 5, and
`calculate_confidence` then hits `thresholds.get(tier, 999)` for tiers 4-1,
making them unreachable. The returned dict should always be
`DEFAULT_THRESHOLDS` **updated with** whatever the DB provides.

### Existing test file and its conventions

`backend/tests/test_recalibrator.py:8-46` — use these helpers, extend them:

```python
def _setup_db():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    t1 = Team(name="Team A", abbreviation="TA", sport="nba")
    t2 = Team(name="Team B", abbreviation="TB", sport="nba")
    session.add_all([t1, t2])
    session.commit()
    strat = StrategyModel(name="test", sport="nba", config_json="{}")
    session.add(strat)
    session.commit()
    return session, t1, t2, strat


def _add_picks(session, t1, t2, strat, confidence, wins, losses):
    for i in range(wins + losses):
        game = Game(
            sport="nba", season="2025-26",
            date=date(2026, 3, i % 28 + 1),
            home_team_id=t1.id, away_team_id=t2.id,
            home_score=100 + i, away_score=95,
            status="final",
        )
        session.add(game)
        session.commit()
        pick = PickModel(
            game_id=game.id, strategy_id=strat.id,
            pick_type="moneyline", pick_value="HOME ML",
            confidence=confidence, edge_pct=10.0, odds_at_pick=-150,
        )
        session.add(pick)
        session.commit()
        result = PickResult(
            pick_id=pick.id,
            result="win" if i < wins else "loss",
            payout=100.0 if i < wins else 0.0,
        )
        session.add(result)
    session.commit()
```

`_add_picks` hardcodes `sport="nba"` and has no push option — you will need to
extend it (add `sport` and `pushes` parameters with defaults that preserve
every existing call).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | 0 failed |
| Targeted | `.venv/Scripts/python.exe -m pytest backend/tests/test_recalibrator.py backend/tests/test_confidence.py -q` | all pass |

Run from repo root `C:\Users\mwill\Documents\mwilliams2733\sports_picks`.

## Scope

**In scope**:
- `backend/analysis/recalibrator.py`
- `backend/analysis/confidence.py` — **only** `get_thresholds`
- `backend/analysis/prop_confidence.py` — **only** `get_prop_thresholds`
- `backend/tests/test_recalibrator.py`
- `backend/tests/test_confidence.py`

**Out of scope** (do NOT touch, even though they look related):
- `EXPECTED_WIN_RATES`, `MIN_PICKS_PER_TIER`, `ADJUSTMENT_STEP`,
  `DEVIATION_THRESHOLD` — these are tuning policy, not bugs. In particular
  `EXPECTED_WIN_RATES[1] = 0.50` is arguably wrong (break-even at -110 is
  ~52.4%), but changing it is a calibration decision for the operator, not a
  correctness fix. **Do not change it.** Mention it in your report instead.
- `calculate_confidence` / `calculate_prop_confidence` themselves — their
  signatures already accept thresholds and are correct.
- `backend/pipeline/recalibration_job.py` — its LightGBM retraining loop has a
  separate known problem (trains 4× on identical unfiltered data, discards the
  model). Not this plan.
- The `.limit(5)` → per-tier change must not alter the `CalibrationHistory`
  schema. No migration in this plan.

## Git workflow

- Branch: `advisor/006-recalibrator-correctness`
- Conventional commits:
  - `fix(recalibrate): filter picks by sport instead of pooling all sports`
  - `fix(recalibrate): exclude pushes from win-rate denominator`
  - `fix(confidence): take the newest threshold per tier, not the oldest`

## Steps

### Step 1: Baseline

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed.
Record the count. If anything fails, STOP.

### Step 2: Filter the recalibrator's query by sport

In `backend/analysis/recalibrator.py`, join `Game` and filter on
`Game.sport == self.sport`:

```python
            picks_with_results = (
                self.session.query(PickModel, PickResult)
                .join(PickResult, PickResult.pick_id == PickModel.id)
                .join(Game, PickModel.game_id == Game.id)
                .filter(
                    Game.sport == self.sport,
                    PickModel.confidence == tier,
                    PickModel.created_at >= cutoff,
                )
                .all()
            )
```

Add `Game` to the import at line 8.

**Verify** — add `test_recalibrator_only_counts_its_own_sport`:
seed 25 tier-5 nba picks that all **win**, and 25 tier-5 nfl picks that all
**lose**, then run `Recalibrator(session, sport="nba").run()`. Assert the
recorded `actual_rate` for tier 5 is `1.0` (nba only), not `0.5` (pooled).

You will need to extend `_add_picks` with a `sport` parameter and create Team
rows for the second sport. Keep the default `sport="nba"` so existing calls are
unchanged.

**Prove the guard is real**: remove the `Game.sport` filter, confirm the new
test fails with `0.5`, then restore.

### Step 3: Exclude pushes from the win rate

Still in `recalibrator.py`, compute the rate over decided picks only:

```python
            wins = sum(1 for p, r in picks_with_results if r.result == "win")
            pushes = sum(1 for p, r in picks_with_results if r.result == "push")
            decided = total - pushes
            if decided < MIN_PICKS_PER_TIER:
                logger.info(
                    "Tier %d: only %d decided picks (need %d), skipping",
                    tier, decided, MIN_PICKS_PER_TIER,
                )
                continue
            actual_rate = wins / decided
```

Two decisions baked in above, both deliberate — implement exactly:
- The `MIN_PICKS_PER_TIER` gate now applies to **decided** picks, not raw rows.
  A tier with 25 picks of which 10 pushed has only 15 real data points and
  should be skipped, not acted on.
- Move the existing gate so it runs after pushes are counted (i.e. replace the
  `total < MIN_PICKS_PER_TIER` check rather than adding a second one).

Keep writing `sample_size=total` to `CalibrationHistory` **or** switch it to
`decided` — pick `decided`, since `sample_size` is what a human reads to judge
whether the number is trustworthy, and record that choice in your commit
message.

**Verify** — add `test_pushes_excluded_from_win_rate`: seed a tier-5 batch with
21 wins, 0 losses, 9 pushes. Assert the recorded `actual_rate` is `1.0`, not
`21/30 = 0.7`. Extend `_add_picks` with a `pushes` parameter (default `0`).

**Prove the guard is real**: revert to `wins / total`, confirm the test fails,
restore.

### Step 4: Take the newest row per tier in the recalibrator

Replace the lines 32-40 block so the baseline is the **newest row per tier**.
Two acceptable implementations — pick one:

- (Simplest, preferred) keep the query but iterate oldest-first, so newer rows
  overwrite older: order by `CalibrationHistory.date.asc()`, drop `.limit(5)`
  in favour of a bounded window (e.g. only rows from the last N days), and
  assign in that order.
- Or group in SQL by `confidence_tier` taking `max(date)`.

Whichever you choose, the result must be `DEFAULT_THRESHOLDS` **updated with**
the DB values, never a dict containing only the tiers present in the DB:

```python
        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update(loaded_from_db)
```

**Verify** — add `test_newest_threshold_wins`: insert two `CalibrationHistory`
rows for the same sport and tier, an older one with `new_threshold=9.0` and a
newer one with `new_threshold=7.0`. Run the recalibrator and assert the
`old_threshold` it records is `7.0` (the newer), not `9.0`.

### Step 5: Same fix in the two threshold readers

Apply the identical correction to `confidence.py:get_thresholds` and
`prop_confidence.py:get_prop_thresholds`:

- newest row per tier wins
- always start from the DEFAULTS dict and `.update()` with DB values, so a
  partially-populated table can never make a tier unreachable
- **preserve `prop_confidence`'s +100 tier offset encoding** on read
  (`row.confidence_tier - 100`) and its
  `CalibrationHistory.confidence_tier >= 100` filter

**Verify** — add to `backend/tests/test_confidence.py`:
- `test_get_thresholds_newest_row_per_tier_wins` — two rows, same tier,
  different dates; the newer value is returned.
- `test_get_thresholds_fills_missing_tiers_from_defaults` — insert a row for
  tier 5 only; assert the returned dict has all five tiers, with tiers 4-1
  equal to `DEFAULT_THRESHOLDS`.
- `test_get_thresholds_no_session_returns_defaults` — unchanged behavior.

Add the equivalent prop test asserting the +100 offset still decodes correctly.

### Step 6: Full suite

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --name-only` → only in-scope files
- `git diff backend/analysis/recalibrator.py | grep -E "^\+.*(EXPECTED_WIN_RATES|MIN_PICKS_PER_TIER = |ADJUSTMENT_STEP|DEVIATION_THRESHOLD)"`
  → **no output** (proves you didn't retune policy constants)

## Test plan

New tests in `backend/tests/test_recalibrator.py`:
1. `test_recalibrator_only_counts_its_own_sport` (step 2)
2. `test_pushes_excluded_from_win_rate` (step 3)
3. `test_newest_threshold_wins` (step 4)

New tests in `backend/tests/test_confidence.py`:
4. `test_get_thresholds_newest_row_per_tier_wins` (step 5)
5. `test_get_thresholds_fills_missing_tiers_from_defaults` (step 5)
6. `test_get_thresholds_no_session_returns_defaults` (step 5)
7. prop-threshold equivalent preserving the +100 offset (step 5)

Helper changes: `_add_picks` gains `sport="nba"` and `pushes=0` parameters with
defaults that leave every existing call behaving identically.

Tests 1, 2 and 3 must each be shown to fail when their fix is reverted.

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, 0 failed
- [ ] `grep -n "Game.sport == self.sport" backend/analysis/recalibrator.py`
      returns a match
- [ ] `grep -n "wins / total" backend/analysis/recalibrator.py` returns **no**
      matches
- [ ] `grep -n "limit(5)" backend/analysis/` returns no matches
- [ ] Mutation checks in steps 2, 3, 4 were each run and failed as expected
- [ ] `EXPECTED_WIN_RATES` and the four tuning constants are unchanged
- [ ] `git diff --name-only` contains no file outside the In-scope list
- [ ] `plans/README.md` status row for 006 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite doesn't pass cleanly.
- An existing test in `test_recalibrator.py` or `test_confidence.py` fails
  after your change. These tests encode the current (buggy) pooled/push
  behavior in places; if one fails, report which and what it asserted — do
  **not** edit an assertion to match new output without saying so explicitly.
- Adding the `Game` join changes which picks are returned in a way you did not
  expect (e.g. picks whose `game_id` points at a deleted game silently
  disappear because an inner join drops them). If pick counts drop for a reason
  other than the sport filter, report it — an orphaned-pick problem is a
  separate finding, not something to paper over with an outer join.
- You conclude `EXPECTED_WIN_RATES` needs changing to make a test pass. It
  doesn't; your test seeds should be chosen to exercise the code, not to match
  a tuning constant.

## Maintenance notes

- **Land this next to plan 002.** 002 makes this code load-bearing; until both
  are in, production either ignores the recalibrator (before 002) or consumes
  wrong numbers from it (after 002, before 006).
- After this lands, per-sport thresholds diverge for the first time. The first
  few nightly runs are worth watching: a sport with thin history will now
  correctly skip (rather than borrow another sport's sample), so expect *fewer*
  adjustments, not more.
- `EXPECTED_WIN_RATES[1] = 0.50` remains a live question — at -110 the
  break-even is ~52.4%, so tier 1 is calibrated against a target that loses
  money. Flag this to the operator when you report; it is a policy call.
- `sample_size` in `CalibrationHistory` changes meaning (decided picks, not
  total rows) as of this plan. Rows written before and after are not directly
  comparable. Note it in the commit message.

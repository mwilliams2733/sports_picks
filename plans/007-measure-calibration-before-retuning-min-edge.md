# Plan 007: Measure model calibration before touching `min_edge` or the ranking key

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 54d38c4..HEAD -- backend/analysis/ backend/pipeline/pick_generator.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW — this plan changes no pick-generation behavior and writes no
  rows to `picks`
- **Depends on**: 002 (merged — makes `CalibratedModel` train), 003 (merged —
  makes `edge_pct` de-vigged and therefore meaningful), and the digest feature
  (merged — makes `picks.model_prob` get written going forward)
- **Category**: bug / investigation
- **Planned at**: commit `54d38c4`, 2026-09-16

## Why this matters

The daily digest ranks picks by `edge_pct`, and a dry-run preview against real
data produced a "top 5" that was five heavy favorites at −286, −239, −385, −779
and −2336. That is not a rendering problem. Measured directly from generated
picks:

| Pick | Odds | Market (de-vigged) | Model says | `edge_pct` |
|---|---:|---:|---:|---:|
| HOME ML | −286 | 71.4% | **90.4%** | +19.3 |
| HOME ML | −239 | 67.3% | **85.3%** | +17.6 |
| HOME ML | −385 | 76.5% | **91.5%** | +15.5 |
| HOME ML | −779 | 84.5% | **99.0%** | +14.3 |
| HOME ML | −2336 | 92.0% | **98.0%** | +6.1 |

The model claims **99.0%** on an NBA game where the market says 84.5%. NBA
teams do not win at 99%. Two of those five sit at or beside the
`max(0.01, min(0.99, ...))` clamp in `ensemble._calibrated_probability` — the
model is not predicting there, it is saturating, and the clamp converts
nonsense into apparent confidence.

**This makes `min_edge` the wrong lever.** `edge_pct = model − market`. The
market is well calibrated; the model is not at the tails. So ranking by edge
ranks by *how badly the model disagrees with a well-calibrated market*, which —
when the model is the unreliable party — means ranking by model error rather
than model skill. Raising `min_edge` keeps only the largest disagreements,
i.e. the most overconfident predictions. It is a filter pointed the wrong way.

Three confirmed contributing causes:

1. **The LightGBM path is dead at prediction time.**
   `ensemble._calibrated_probability` prefers LightGBM for NBA, but
   `self._lgbm_model` is `None` on every freshly constructed strategy, and
   `pick_generator` constructs one per game. Verified: a fresh
   `EnsembleStrategy('e', {})` has `_lgbm_model is None`. It is trained nightly
   by `recalibration_job` and discarded.
2. **The fallback logistic model trains but is not calibrated at the tails.**
   `CalibratedModel.train_from_db` now succeeds (1058 games — plan 002's fix),
   with coefficients `[0.0068, 0.1552, 0.1419, 0.0, -0.1389]`. One coefficient
   is exactly `0.0` — a dead feature.
3. **The clamp hides the failure** rather than surfacing it.

**This plan does not fix any of that.** It builds the measurement that tells you
*which* fix is warranted and by how much. Retuning `min_edge`, shrinking
probabilities, or changing the ranking key without a reliability curve is
guessing.

## Current state

### The probability path a pick actually takes

`backend/analysis/variants/ensemble.py:48` — `predict` calls:

```python
        home_prob = self._calibrated_probability(game)
```

`ensemble.py:146-165`:

```python
    def _calibrated_probability(self, game: GameData) -> float:
        """Get calibrated P(home win), preferring LightGBM for NBA."""
        # Use LightGBM regression model for NBA if trained
        if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
            feature_dict = extract_features(game)
            feature_array = features_to_array(feature_dict)
            home_prob = self._lgbm_model.home_win_prob(np.array([feature_array]))
        else:
            home_prob = self._legacy_calibrated_probability(game)

        # Schedule adjustments
        if game.home_stats.is_schedule_fatigued:
            home_prob -= 0.03 * game.home_stats.schedule_fatigue_score
        if game.away_stats.is_schedule_fatigued:
            home_prob += 0.03 * game.away_stats.schedule_fatigue_score
        if game.home_stats.is_lookahead_spot:
            home_prob -= 0.04
        if game.away_stats.is_lookahead_spot:
            home_prob += 0.04
        return max(0.01, min(0.99, home_prob))
```

`_lgbm_model` is initialised to `None` at `ensemble.py:41` and never assigned
outside `train_lgbm_from_db`, so in production the `else` branch always runs.

### Why you cannot measure this from stored picks

`picks.model_prob` is **NULL for all 708 existing rows** — the column was
migrated in months ago and only started being written by the digest work merged
in `54d38c4`. There is no stored predicted probability to compare against
outcomes. Waiting for new picks to accumulate would take weeks.

**The available method instead:** run the *current* model over historical games
that already have known results. `games` holds **1058 rows with
`status='final'`** and non-null scores. Predicting those and comparing to the
actual winner is a proper reliability analysis, and it can run today.

### The leakage trap this plan exists to avoid

`_legacy_calibrated_probability` lazily trains `EnsembleStrategy._calibrated`
via `CalibratedModel.train_from_db(session)`, which selects **every**
`status='final'` game. If you evaluate calibration on those same games, the
model has already seen every outcome you are scoring it against. It will look
well calibrated and the number will be meaningless.

> This repo's conventions are explicit about this: *"train-test leakage,
> fitting on graded outcomes that include the row being predicted"* is called
> out as a known hazard, and *"state effective sample size with any statistical
> claim."* Both bind this plan.

An out-of-sample design is mandatory. `backend/analysis/ml_model.py:86` already
has a `walk_forward_validate` helper — read it before writing your own split;
match its convention if it fits.

### Facts you will need

- `Game` has `sport`, `date`, `status`, `home_score`, `away_score`,
  `home_team_id`, `away_team_id`.
- Home win is `home_score > away_score`. NBA cannot tie; other sports can — a
  tie must be excluded from a binary reliability curve, not counted as a loss.
- `backend/pipeline/pick_generator.py` has `_build_game_data(session, game,
  pitcher_scores=None)` which assembles the `GameData` a strategy needs. It is
  already imported cross-module by `backend/api/backtest.py:96`, so using it is
  consistent with existing practice.
- `EnsembleStrategy(name, config, thresholds=None)` — `config` accepts
  `min_edge`, `k_factor`, `lookback`, `weights`. The live ensemble config is
  `{"min_edge": 3, "k_factor": 20, "lookback": 10, "weights": {"pd": 0.3,
  "elo": 0.35, "rating": 0.25, "hca": 0.1}}`.
- `EnsembleStrategy._calibrated` is a **class attribute** — a trained model
  persists across instances within a process. Your split logic must control it
  explicitly or results will silently leak.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | `481 passed`, 0 failed |
| New tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_calibration_report.py -q` | all pass |
| Run the report | `.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba` | prints a reliability table |

Run from repo root. The full suite takes ~3.5 minutes.

## Scope

**In scope**:
- `backend/analysis/calibration_report.py` (create)
- `backend/tests/test_calibration_report.py` (create)
- `plans/README.md` (status row)

**Out of scope** (do NOT touch):
- `backend/analysis/variants/*.py` — **do not change any probability, clamp,
  weight or `min_edge`.** This plan measures; it does not tune. Changing the
  model before measuring it destroys the baseline you are trying to establish.
- `backend/pipeline/pick_generator.py` — no change to pick generation.
- `config.yaml` — do not retune `min_edge` or the strategy config.
- The `picks` table — this plan writes **no rows**. It reads `games` only.
- `backend/analysis/ml_model.py` — read `walk_forward_validate` for reference;
  do not modify it.
- LightGBM persistence — a real and separate finding, not this plan.

## Git workflow

- Branch: `advisor/007-calibration-report`
- Conventional commits: `feat(analysis): add an out-of-sample calibration report`
- Do NOT push or open a PR.

## Steps

### Step 1: Baseline

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → `481 passed`.
If not, STOP and report.

### Step 2: Write the failing tests

Create `backend/tests/test_calibration_report.py`. The tests define the
contract; write them before the implementation.

Cover at least:

1. **Perfectly calibrated input yields near-zero error.** Feed the binning
   function synthetic `(predicted_prob, actual_outcome)` pairs where each bin's
   predicted rate matches its observed rate, and assert the reported gap per bin
   is ~0 and the Brier score matches a hand-computed value.
2. **Overconfident input is detected.** Feed pairs where predictions of 0.95
   only win 70% of the time, and assert the report shows that bin as
   overconfident by ~0.25 — the actual defect this plan is chasing.
3. **Ties are excluded, not counted as losses.** A game with equal scores must
   not appear in the binary outcome series.
4. **Bin counts are reported**, and a bin below a minimum sample size is
   flagged as unreliable rather than silently averaged.
5. **The split is out-of-sample.** Given games spanning two date ranges, assert
   that no game used to fit the model appears in the evaluation set. This is the
   load-bearing test — see Step 5.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_calibration_report.py -q`
→ FAIL with `ModuleNotFoundError: No module named 'backend.analysis.calibration_report'`.

### Step 3: Build the pure binning and scoring core

In `backend/analysis/calibration_report.py`, write the statistics first, as
pure functions with no DB access:

- `reliability_bins(pairs, n_bins=10, min_bin=30) -> list[Bin]` where `pairs` is
  `list[tuple[float, int]]` of `(predicted_prob, actual_outcome)`. Each `Bin`
  carries the probability range, `n`, mean predicted, observed rate, the signed
  gap (predicted − observed), and a `reliable: bool` flag for `n >= min_bin`.
- `brier_score(pairs) -> float`.

Keeping these pure is what makes tests 1-4 possible without a database.

**Verify**: tests 1-4 pass; test 5 still fails.

### Step 4: Build the out-of-sample evaluation

Add `evaluate(session, sport, n_bins=10, min_bin=30, split_date=None) -> Report`.

Design requirement — **the model must never be fit on a game it is scored
against.** Use a time-based split: fit on games before `split_date`, evaluate on
games on or after it. Default `split_date` to the date that puts roughly 70% of
that sport's final games in the fit set.

Because `EnsembleStrategy._calibrated` is a **class attribute** that persists
across instances, you must explicitly control it — reset it before fitting, and
make sure the fit uses only the training window. If you cannot cleanly restrict
`CalibratedModel.train_from_db` to a date range without modifying it (it is out
of scope to modify), then construct and train a `CalibratedModel` yourself over
the training games and pass it in, rather than relying on the lazy path. Say in
your report which approach you took and why.

For each evaluation game: build `GameData` via `_build_game_data`, get the
model's home-win probability, and pair it with the actual result. Exclude ties.

**Verify**: test 5 passes — no game in the fit set appears in the evaluation set.

### Step 5: Prove the leakage guard is real

This is the step that makes the whole report trustworthy.

Temporarily change the split so the fit and evaluation sets are **identical**
(evaluate on the training games). Run the report and record the Brier score and
the bin gaps. Then restore the proper out-of-sample split and run it again.

**The in-sample run must look meaningfully better than the out-of-sample run.**
If the two are indistinguishable, either the split is not actually splitting or
the model is not being refit — investigate before proceeding, and report what
you found. Include both sets of numbers in your report.

### Step 6: Add the CLI and produce the real numbers

Add an `argparse` entry point (`python -m backend.analysis.calibration_report
--sport nba [--db sports_picks.db] [--bins 10] [--min-bin 30]`) printing:

- the fit/evaluation split (dates and counts)
- the reliability table: bin range, n, mean predicted, observed, gap, reliable?
- overall Brier score
- **the effective sample size**, and an explicit note that games from the same
  team/season are not independent observations

Run it against the real database for `nba` and put the actual output in your
report. If a sport has too few final games to split meaningfully, say so rather
than printing a table nobody should trust.

**Verify**: the command runs and prints a table. Do not tune anything to make
the numbers look better — report them as observed.

### Step 7: Full suite and scope check

**Verify**:
- `.venv/Scripts/python.exe -m pytest backend/tests -q` → 0 failed
- `git diff --name-only` → only the two new files
- `git diff --stat` shows **no** change under `backend/analysis/variants/`,
  `backend/pipeline/`, or `config.yaml`
- `select count(*) from picks` is unchanged from before your run (**708** at the
  time of writing) — this plan writes no picks

## Test plan

Five tests in `backend/tests/test_calibration_report.py` as listed in Step 2,
following the repo's conventions (plain `def test_*`, no classes, model after
`backend/tests/test_api_picks.py`). Tests 1-4 are pure and need no database.
Test 5 needs seeded games.

The Step 5 in-sample-vs-out-of-sample comparison is a manual verification, not a
test — record both numbers in the report.

## Done criteria

ALL must hold:

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0
- [ ] `backend/analysis/calibration_report.py` exists with pure
      `reliability_bins` and `brier_score`, plus a DB-backed `evaluate`
- [ ] The out-of-sample test passes and the Step 5 comparison shows in-sample
      scoring better than out-of-sample
- [ ] The CLI runs against the real DB and its **actual output** is in the report
- [ ] Effective sample size is stated alongside the numbers
- [ ] `git diff --name-only` contains no file outside the In-scope list
- [ ] `select count(*) from picks` is still 708
- [ ] `plans/README.md` status row for 007 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suite is not `481 passed`.
- You cannot construct an out-of-sample split without modifying
  `CalibratedModel` or a strategy — report the obstacle rather than editing
  out-of-scope files or quietly evaluating in-sample.
- The in-sample and out-of-sample results are indistinguishable in Step 5.
- The evaluation set for `nba` has fewer than ~200 games after the split — say
  so; a reliability curve on thin data is worse than none.
- You find yourself wanting to adjust a weight, a clamp or `min_edge` to
  improve a number. That is the next decision, not this plan, and doing it here
  destroys the baseline.

## The decision this plan feeds — deliberately not specified here

Once the reliability table exists, three questions become answerable with data
rather than intuition. **They are not part of this plan** because their right
answers depend on numbers nobody has yet:

1. **Shrinkage.** If the top bins are overconfident by a large margin, the model's
   probabilities should be shrunk toward the market or toward a base rate before
   an edge is computed. The table says by how much, and whether it varies by
   sport.
2. **The ranking key.** Raw `edge_pct` ranks by model-market disagreement, which
   is model error when the model is the miscalibrated party. Candidates once
   calibration is known: edge discounted by that bin's measured gap, or a
   Kelly-sized stake, which naturally penalises extreme probabilities.
3. **`min_edge`.** Only meaningful after 1 and 2. Retuning it first would be
   fitting a threshold to a miscalibrated signal.

A fourth, independent of the above: **LightGBM is trained nightly and
discarded**, so the model that was supposed to be making NBA predictions never
runs. That is its own plan, and the calibration baseline from this one is what
would let you tell whether wiring it in actually helps.

## Maintenance notes

- The report is read-only by design. If someone later makes it write rows,
  the "writes no picks" done-criterion should become a test.
- `picks.model_prob` is now populated going forward. In a few weeks there will
  be enough *live* predictions to run the same reliability analysis on real
  picks rather than replayed historical games — which also captures the
  schedule adjustments applied in `_calibrated_probability`. Worth re-running
  then and comparing; a gap between replayed and live calibration would point
  at those adjustments.
- Games from the same team and season are correlated, so the naive n
  overstates precision. Whoever acts on these numbers should treat the
  effective sample size, not the raw count, as the basis for confidence.

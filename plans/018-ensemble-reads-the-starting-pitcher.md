# Plan 018: The active MLB model reads the starting pitcher

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 84dc78c..HEAD -- backend/analysis/variants/ensemble.py backend/tests/test_strategy_factor_codes.py backend/tests/test_ensemble_pitcher.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none (plan 019 makes this visible in the morning digest)
- **Category**: bug
- **Planned at**: commit `84dc78c`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). The constant, helpers and insertion are literal, but the eight tests are specified in prose with numeric expectations the executor must turn into assertions, and the mutation check has a nuance (test 4 passes trivially at weight 0 and must be reported as such). Mid-tier is the floor for work from prose. Do not use `haiku` here.

## Why this matters

The only active game strategy in production is `ensemble` (row 1 of the
`strategies` table, `is_active=1`). It prices every MLB game from a logistic
regression over Elo difference, rolling run differential, and three
features that are zero for baseball. **It never reads the starting
pitcher.** Meanwhile the scheduler fetches probable-pitcher stats from the
MLB Stats API every game window, converts them to a skill score, attaches
them to the game data — and the ensemble discards them. Zero of the 50 MLB
picks in the database carry a pitcher factor. MLB moneyline is 10-18.

Quantitatively, the pooled model's slope on run differential is 0.0119 per
run and the typical MLB feature gap is 2.15 runs, so recent form moves an
MLB probability by about half a percentage point. Elo (slope 0.0039 per
point) does the rest. In baseball the starter is the single largest
game-to-game variable, and the model is blind to it.

After this plan the ensemble applies a pitcher adjustment for MLB games
when both starters are known, emits the `pitcher_edge` rationale factor so
the email can say so, and leaves every other sport untouched.

## Current state

- `backend/analysis/variants/ensemble.py` — the active strategy.
  `_calibrated_probability` (line 368) returns P(home win); it applies
  fatigue and lookahead adjustments to a base probability and clamps.
- `backend/analysis/strategy.py` — base class. `_build_factors`
  (lines ~140-190) already emits `pitcher_edge` when both
  `pitcher_skill_score` values are present and differ, **filtered by the
  subclass's `FACTOR_CODES`**.
- `backend/analysis/pitcher.py` — `pitcher_skill_score(era, k9)` → [0, 1],
  0.5 is league average; ERA 2.50 ≈ 0.76, ERA 5.50 ≈ 0.26.
- `backend/data_types.py:22` — `TeamStats.pitcher_skill_score: float | None`,
  MLB-only, populated by `pick_generator._build_game_data` (lines 414-418)
  only when the caller supplied `pitcher_scores`.
- `backend/tests/test_strategy_factor_codes.py` — asserts each strategy's
  `FACTOR_CODES` exactly; the ensemble entry (lines 77-78) must change.
- `backend/tests/test_ensemble.py` — the `_stats(**kw)` fixture pattern.
- `backend/tests/test_max_edge_ceiling.py:68` — how tests pin the base
  probability: `monkeypatch.setattr(EnsembleStrategy, "_calibrated_probability", fn)`.
  This plan's tests must NOT use that, since the method under test is that one;
  pin `_legacy_calibrated_probability` instead (see Step 3).

`_calibrated_probability` today, `ensemble.py:368-387`:

```python
    def _calibrated_probability(self, game: GameData) -> float:
        """Get calibrated P(home win), preferring LightGBM for NBA."""
        if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
            ...
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

`FACTOR_CODES` today, `ensemble.py:170-188` (comment abridged):

```python
    # ... Pitcher scores are never read on any of those paths.
    FACTOR_CODES = frozenset({
        "rating_gap", "recent_form", "net_rating",
        "schedule_fatigue", "lookahead_spot",
    })
```

The sport-specific variant's pitcher term, `sport_specific.py:150-160`, is
**not** the model to copy: it weights a sigmoid of the score difference at
45%, which moves P(home) by ~17 points for an ace against an average
starter. That is far more than the market prices a starter at, and that
strategy has no `max_edge` guard. This plan uses a small logit shift.

**Why a logit shift and not a trained feature.** No table stores historical
pitcher scores, so the logistic regression cannot be trained on them. The
weight below is therefore a prior, not a measurement, and the plan says so
in code. Persisting the scores so the weight can be fitted later is
deferred (see Maintenance notes).

Conventions: constants are documented with the measurement or reasoning
behind them (see `MARGIN_SHRINKAGE_K`, `ensemble.py:71-86`). Adjustments in
`_calibrated_probability` are applied to the probability directly; this
one is applied in logit space so it cannot push past the clamp
asymmetrically. Match the existing comment style: explain what was wrong
and what the number means.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| New tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble_pitcher.py -q` | all pass |
| Factor-code guard | `.venv/Scripts/python.exe -m pytest backend/tests/test_strategy_factor_codes.py -q` | all pass |
| Ensemble suite | `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble.py backend/tests/test_ensemble_edges.py backend/tests/test_max_edge_ceiling.py backend/tests/test_strategy_factor_codes.py -q` | all pass |
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | all pass |

Run from the repo root with the Bash tool (git-bash). No ruff/mypy here.
`conftest.py` pins an untrained `CalibratedModel` for every test, so the
base probability in tests is the heuristic fallback unless you pin it.

## Scope

**In scope** (the only files you should modify):
- `backend/analysis/variants/ensemble.py`
- `backend/tests/test_strategy_factor_codes.py` (one expected set)
- `backend/tests/test_ensemble_pitcher.py` (create)

**Out of scope** (do NOT touch):
- `backend/analysis/variants/sport_specific.py` — a different, inactive
  strategy with its own tests.
- `backend/analysis/strategy.py` — `_build_factors` already does the right
  thing once `FACTOR_CODES` includes the code.
- `backend/pipeline/scheduler.py` and `pick_generator.py` — the morning
  slate wiring is plan 019.
- `backend/analysis/calibrated_model.py` — no new training feature; see
  "Why a logit shift".
- `backend/analysis/pitcher.py` — the score is fine; the problem is that
  nothing reads it.

## Git workflow

- Branch: `advisor/018-ensemble-reads-the-starting-pitcher`
- Conventional commits, e.g. `feat(mlb): the ensemble reads the starting pitcher`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add the constant and the helper

In `ensemble.py`, near `MARGIN_SHRINKAGE_K`, add:

```python
#: Logit shift per unit of starting-pitcher skill difference, MLB only.
#:
#: `pitcher_skill_score` maps ERA/K9 to [0, 1] with 0.5 at league average:
#: an ace (ERA 2.50) sits near 0.76, a replacement starter (ERA 5.50) near
#: 0.26. At weight 1.0 an ace against an average starter (+0.26) moves
#: P(home) from 0.50 to about 0.565, and ace against replacement (+0.50)
#: to about 0.62. Those are the right order of magnitude for how the
#: market prices a starter, and deliberately far smaller than the 45%
#: weight `SportSpecificStrategy` gives the same signal.
#:
#: A PRIOR, not a measurement. No table stores historical pitcher scores,
#: so this cannot be fitted from results yet. Re-fit it once they are
#: persisted; until then do not tune it by hand against the live book.
PITCHER_LOGIT_WEIGHT = 1.0
```

Add two module-level helpers next to it:

```python
def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def pitcher_logit_shift(game: GameData) -> float:
    """Logit adjustment to P(home win) from the two starting pitchers.

    Zero unless the game is MLB and BOTH scores are known. One missing
    starter is unknown, not average: shifting on half the matchup would
    price an announced ace against a phantom.
    """
    if game.sport != "mlb":
        return 0.0
    h = game.home_stats.pitcher_skill_score
    a = game.away_stats.pitcher_skill_score
    if h is None or a is None:
        return 0.0
    return PITCHER_LOGIT_WEIGHT * (h - a)
```

Add `import math` to the imports at the top of the file.

**Verify**: `.venv/Scripts/python.exe -c "from backend.analysis.variants.ensemble import pitcher_logit_shift, PITCHER_LOGIT_WEIGHT; print(PITCHER_LOGIT_WEIGHT)"` → `1.0`

### Step 2: Apply it in `_calibrated_probability`

Between the base-probability branch and the `# Schedule adjustments`
comment, insert:

```python
        # Starting pitcher, MLB only. Applied in logit space so the shift is
        # symmetric around the base probability and cannot be flattened by
        # the clamp below on one side only. The base model never sees the
        # pitcher: nothing stores historical scores to train on, so this is
        # a post-hoc term like the two schedule adjustments that follow.
        shift = pitcher_logit_shift(game)
        if shift:
            home_prob = _sigmoid(_logit(home_prob) + shift)
```

Then extend `FACTOR_CODES` to include `"pitcher_edge"` and update its
comment: replace the sentence `Pitcher scores are never read on any of
those paths.` with a sentence saying the MLB pitcher shift in
`_calibrated_probability` reads them, and that `_build_factors` only emits
the code when both scores are present, which is the same condition
`pitcher_logit_shift` requires.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble.py backend/tests/test_ensemble_edges.py backend/tests/test_max_edge_ceiling.py -q` → all pass (none of those fixtures set a pitcher score).

### Step 3: Update the factor-code guard

In `backend/tests/test_strategy_factor_codes.py:77-78`, change the ensemble
expectation to:

```python
    (EnsembleStrategy, frozenset({"rating_gap", "recent_form", "net_rating",
                                  "schedule_fatigue", "lookahead_spot",
                                  "pitcher_edge"})),
```

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_strategy_factor_codes.py -q` → all pass.

### Step 4: Write `backend/tests/test_ensemble_pitcher.py`

Module docstring: state that the ensemble is the active strategy, that it
ignored the pitcher until this change, and that zero of 50 MLB picks
carried the factor.

Fixture helpers (copy `_stats` from `test_ensemble.py`, plus):

```python
def _mlb_game(home_pitcher, away_pitcher, sport="mlb"):
    return GameData(game_id=1, sport=sport, date=date(2026, 6, 1),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(pitcher_skill_score=home_pitcher),
        away_stats=_stats(pitcher_skill_score=away_pitcher),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                           spread_home=-1.5, spread_away=1.5, over_under=8.5)])


@pytest.fixture
def base_half(monkeypatch):
    """Pin the base model at exactly 0.5 so every test measures only the
    pitcher term. Pins the LEGACY method, not `_calibrated_probability`,
    because the latter is what is under test."""
    monkeypatch.setattr(EnsembleStrategy, "_legacy_calibrated_probability",
                        lambda self, game: 0.5)
```

Tests (names as given):

1. `test_no_pitchers_no_shift` — `_mlb_game(None, None)`; with `base_half`,
   `_calibrated_probability` returns `0.5` exactly.
2. `test_one_missing_pitcher_no_shift` — `_mlb_game(0.8, None)` and
   `_mlb_game(None, 0.8)` both return `0.5`.
3. `test_home_ace_raises_home_probability` — `_mlb_game(0.76, 0.50)`;
   result strictly greater than `0.5` and within `[0.55, 0.60]`
   (sigmoid(0.26) = 0.5646).
4. `test_shift_is_antisymmetric` — `p1 = _mlb_game(0.76, 0.26)`,
   `p2 = _mlb_game(0.26, 0.76)`; assert `abs((p1 - 0.5) + (p2 - 0.5)) < 1e-9`.
5. `test_non_mlb_ignores_pitcher_scores` — `_mlb_game(0.9, 0.1, sport="nfl")`
   returns `0.5`.
6. `test_pitcher_edge_factor_is_emitted_on_the_pick` — with `base_half`,
   `EnsembleStrategy("ensemble", {"min_edge": 1.0, "max_edge": 100.0})`
   `.predict(_mlb_game(0.80, 0.30))`; assert at least one pick, its
   `pick_value == "HOME ML"`, and `"pitcher_edge" in {f.code for f in picks[0].factors}`.
7. `test_no_pitcher_edge_factor_when_scores_missing` — same as 6 with
   `_mlb_game(None, None)` but pin the legacy probability at `0.6` so a pick
   still fires; assert `"pitcher_edge"` is not among the factor codes.
8. `test_pitcher_shift_respects_the_clamp` — pin legacy at `0.985`,
   `_mlb_game(1.0, 0.0)`; result `<= 0.99`.

**Mutation check, required**: set `PITCHER_LOGIT_WEIGHT = 0.0` temporarily
and run the file; tests 3, 4 (trivially passes — note it) and 6 must fail,
test 3 in particular. Restore. Then change `if h is None or a is None` to
`if h is None and a is None` and confirm test 2 fails. Restore. Report both.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_ensemble_pitcher.py -q` → 8 passed.

### Step 5: Full suite

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass.

## Test plan

- New file `backend/tests/test_ensemble_pitcher.py` with the eight tests in
  Step 4, modeled on `test_ensemble.py` fixtures and the monkeypatch
  pattern in `test_max_edge_ceiling.py`.
- Modified: one expected set in `test_strategy_factor_codes.py`.
- Verification: the Ensemble suite command → all pass.

## Done criteria

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0
- [ ] `grep -n "pitcher_logit_shift\|PITCHER_LOGIT_WEIGHT" backend/analysis/variants/ensemble.py` shows the constant, the helper and one call site inside `_calibrated_probability`
- [ ] `grep -n "pitcher_edge" backend/analysis/variants/ensemble.py` finds it inside `FACTOR_CODES`
- [ ] Both mutation checks performed and reported
- [ ] `git diff --stat 84dc78c..HEAD -- backend/analysis/variants/sport_specific.py backend/analysis/strategy.py backend/pipeline/` is empty
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back if:

- `_calibrated_probability` no longer matches the excerpt (someone moved
  the schedule adjustments or added a sport branch).
- `TeamStats.pitcher_skill_score` is not a field in `backend/data_types.py`.
- `test_strategy_factor_codes.py`'s fixture no longer fires `pitcher_edge`
  (its own `test_the_fixture_really_fires_every_code` fails) — the base
  class changed and the emission condition may differ from the shift's.
- Any pre-existing test in `test_ensemble*.py` fails after Step 2. Those
  fixtures do not set pitcher scores, so a failure means the shift fired
  where it should not.

## Maintenance notes

- **The weight is a prior.** To make it measurable, a follow-up should
  persist the two pitcher scores per game (a `TeamStat` row with
  `stat_type="pitcher_skill_score"` written by `_build_game_data` when it
  attaches them is the smallest change) so that after ~150 graded MLB picks
  the shift can be fitted as a feature of `CalibratedModel` and this
  post-hoc term retired.
- Plan 019 is what gets scores into the *morning* picks; without it this
  term only fires on the two-hour-before-first-pitch window refresh, after
  the 11 ET email has gone out.
- A reviewer should confirm the shift is applied before the schedule
  adjustments and before the clamp, and that no non-MLB fixture changed
  its output.
- If MLB is ever routed to `SportSpecificStrategy` the way combat sports
  are routed, delete this term first; two pitcher adjustments would double
  count.

# Plan 021: Make per-sport model signal measurable, and record why per-sport slopes were refused

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat a6f342e..HEAD -- backend/analysis/calibrated_model.py backend/pipeline/scheduler.py backend/pipeline/team_stats.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans 018 and 019 (both merged; this plan persists what 019 fetches and 018 consumes)
- **Category**: tests
- **Planned at**: commit `a6f342e`, 2026-09-23
- **Executor model**: `sonnet` (Agent tool `model` value). A new analysis module and its tests are written from prose, and Task 1 needs a judgment call about where a side-effecting write belongs. Mid-tier is the floor. Not `haiku`.

## Why this matters

This plan replaces a proposed fix that measurement refused.

**The proposal.** `CalibratedModel` fits one slope per feature across every
sport, on data whose scales differ by a factor of eight: the mean absolute
home-minus-away rolling margin is 2.15 runs in baseball and 16.58 points in
college football. The proposal was to give each sport its own slopes, or to
standardize each feature within sport.

**The measurement.** Both variants were fitted against the production
database on a 70/30 time split taken *within* each sport, with the per-sport
home-advantage one-hot left in place so that slopes were the only thing under
test. Out-of-sample Brier, lower is better:

| sport | eval n | effective n | pooled (today) | standardized | per-sport slopes |
|---|---|---|---|---|---|
| mlb | 45 | 29.8 | **0.2494** | 0.2784 | too few to fit |
| nba | 375 | 291.9 | **0.1692** | 0.1704 | 0.1736 |
| ncaaf | 79 | 40.8 | 0.1729 | **0.1635** | 0.1772 |
| nfl | 352 | 302.2 | **0.2305** | 0.2313 | 0.2341 |

Per-sport slopes are worse in every sport that could support them.
Standardization is worse in three of four and better only in college
football, on 79 games whose effective n is 40.8. **The pooled fit stays.**

**What the measurement did reveal**, against the two trivial nulls:

| sport | effective n | model | always the base rate | always 0.5 |
|---|---|---|---|---|
| mlb | 29.8 | 0.2494 | 0.2532 | 0.2500 |
| nba | 291.9 | **0.1692** | 0.2449 | 0.2500 |
| ncaaf | 40.8 | 0.1729 | 0.1894 | 0.2500 |
| nfl | 302.2 | 0.2305 | 0.2477 | 0.2500 |

Basketball is the only sport where the model clearly works. **Baseball's
model is indistinguishable from a coin flip** — 0.2494 against 0.2500 — on an
effective sample of 30. Football beats the base rate by 0.017 at effective n
302, which is real but small.

**Why this is a plan and not a verdict.** That baseball figure is fitted on
features that do not include the starting pitcher. The pitcher term shipped
on 2026-09-23 and **cannot be evaluated at all**, because nothing stores the
pitcher scores that were used: they are fetched, attached to an in-memory
`TeamStats`, and discarded. So the honest position is that baseball's model
had no signal *before* the pitcher term, and whether it has any *now* is
unanswerable by construction.

After this plan: the pitcher scores are persisted point-in-time, a repeatable
report answers "does this sport's model beat its nulls" for every sport at
once, and the refused slopes experiment is recorded in code so nobody
re-runs it.

## Current state

- `backend/analysis/calibrated_model.py` — `CalibratedModel.train_from_db`
  fits one `LogisticRegression` over all sports on five difference features
  plus a per-sport home-advantage one-hot. `shrink_sport_coefficients`
  partial-pools the one-hot slots only. The five slopes are shared.
- `backend/pipeline/scheduler.py` — `mlb_pitcher_scores(session, today)`
  (added by plan 019) is the single place the pitcher fetch is wired. It
  returns `{game_id: {"home": float, "away": float}}` or `None`, and already
  holds a session. **Nothing persists what it returns.**
- `backend/pipeline/team_stats.py` — `_upsert_stats(session, game_id, team_id, stats)`
  writes a `{stat_type: value}` dict for one (game, team), updating rows that
  exist. `COMPUTED_STAT_TYPES` is the vocabulary emitted by
  `compute_team_stats`; a new type written by a *different* module is not part
  of it and must not be added to it.
- `backend/analysis/calibration_report.py` — the existing per-sport report.
  It is deep (full reliability curve, one sport per invocation) and refuses
  below `MIN_EVAL_GAMES = 200`, so it cannot answer the cross-sport question
  this plan needs. It also documents the `do_orm_execute` +
  `with_loader_criteria` trick used to fit on a date-bounded subset without
  modifying `train_from_db`, and `effective_sample_size(n, n_clusters, icc)`.
  Reuse both; do not reimplement them.
- `backend/models.py:67` — `TeamStat(team_id, game_id, stat_type, value)`,
  `value` is a non-null Float.

`mlb_pitcher_scores` today, `scheduler.py:176-192`:

```python
def mlb_pitcher_scores(session, today) -> dict[int, dict[str, float]] | None:
    """Today's probable-pitcher skill scores keyed by MLB game id, or None.
    ...
    """
    try:
        scores_by_abbr = asyncio.run(fetch_pitcher_scores_for_date(today))
        return _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
    except Exception as exc:
        logger.warning(
            "MLB pitcher fetch failed (%s); proceeding with neutral pitcher scores", exc)
        return None
```

Conventions to match:
- A constant carries the measurement behind it. See `MARGIN_SHRINKAGE_K`
  (`ensemble.py:70-83`) and `SPREAD_VALIDATED_SPORTS` (`ensemble.py`), which
  records a refused change with its numbers rather than deleting the idea.
  Task 3 follows that exact shape.
- A report module is **read-only**: "This module measures; it never tunes and
  it writes no rows" (`calibration_report.py:3`). Keep that contract.
- Any statistical claim states effective sample size, never the raw count.
- A missing input is absent, never defaulted to a neutral value.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| New report tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_sport_signal_report.py -q` | all pass |
| Pitcher persistence tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py backend/tests/test_pitcher_persistence.py -q` | all pass |
| Model tests | `.venv/Scripts/python.exe -m pytest backend/tests/test_calibrated_model.py backend/tests/test_calibration_report.py -q` | all pass |
| Full suite | `.venv/Scripts/python.exe -m pytest backend/tests -q` | all pass (1461 at `a6f342e`) |
| Run the report | `.venv/Scripts/python.exe -m backend.analysis.sport_signal_report --db <abs windows path>` | prints the table, writes nothing |

Run from the repo root with the Bash tool (git-bash). No ruff or mypy here.
`--db` takes a **Windows** path; a git-bash `/c/...` path fails to open.

## Scope

**In scope**:
- `backend/pipeline/scheduler.py` (persist what `mlb_pitcher_scores` returns)
- `backend/analysis/sport_signal_report.py` (create)
- `backend/analysis/calibrated_model.py` (docstring only — record the refusal)
- `backend/tests/test_pitcher_persistence.py` (create)
- `backend/tests/test_sport_signal_report.py` (create)

**Out of scope**:
- **Any change to how `CalibratedModel` fits.** That is the whole point: the
  measurement said leave it alone. Do not add per-sport slopes, do not
  standardize features, do not touch `shrink_sport_coefficients`.
- `backend/analysis/calibration_report.py` — reuse its helpers by import;
  do not edit it.
- `COMPUTED_STAT_TYPES` in `team_stats.py` — the new stat type is written by
  the scheduler, not by `compute_team_stats`, and adding it there would break
  the vocabulary test's meaning.
- `backend/analysis/variants/ensemble.py` — the pitcher term itself is
  correct and shipped; this plan only makes it measurable later.
- Any gate that stops a sport's picks being generated. See "Maintenance
  notes" — that is a decision for after the first honest measurement, not now.

## Git workflow

- Branch: `advisor/021-make-per-sport-signal-measurable`
- Conventional commits, e.g. `feat(mlb): persist the pitcher scores a pick was made on`
- Do NOT push or open a PR unless the operator instructed it.
- End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## Steps

### Step 1: Persist the pitcher scores a pick was priced on

In `scheduler.py`, after `mlb_pitcher_scores` obtains its mapping, write each
game's two scores as `TeamStat` rows before returning. Add a module-level
constant and a helper beside it:

```python
#: Stat type under which a start's pitcher skill score is stored.
#:
#: Written here rather than by `pipeline.team_stats.compute_team_stats`,
#: which derives its stats from completed games. This one is only knowable
#: BEFORE the game, from the probable-pitcher feed, so it is recorded at the
#: moment a pick is priced on it. Deliberately NOT added to
#: COMPUTED_STAT_TYPES: that tuple is the vocabulary `compute_team_stats`
#: emits, and this is not one of them.
#:
#: Why it exists at all: the pitcher term shipped on 2026-09-23 shifts every
#: MLB probability, and until these rows exist its effect cannot be measured
#: -- the score was fetched, used, and thrown away, so no past pick can be
#: re-scored against the starter it was priced on.
PITCHER_STAT_TYPE = "pitcher_skill_score"


def _persist_pitcher_scores(session, scores: dict[int, dict[str, float]]) -> int:
    """Record each game's two starter scores. Returns rows written.

    Best-effort and never raises: failing to record a measurement input must
    not cost the day's picks, which is what the caller is really doing.
    """
    from backend.models import Game
    from backend.pipeline.team_stats import _upsert_stats

    written = 0
    try:
        for game_id, sides in scores.items():
            game = session.get(Game, game_id)
            if game is None:
                continue
            for side, team_id in (("home", game.home_team_id),
                                  ("away", game.away_team_id)):
                value = sides.get(side)
                if value is None:
                    continue
                written += _upsert_stats(session, game_id, team_id,
                                         {PITCHER_STAT_TYPE: float(value)})
        session.commit()
    except Exception:
        logger.exception("Could not persist pitcher scores; picks continue")
        session.rollback()
    return written
```

Call it from `mlb_pitcher_scores` on the success path only, logging the count
unconditionally (a zero is the normal answer on a day with no announced
starters, and a silent path is indistinguishable from an unwired one):

```python
        scores = _remap_pitcher_scores_to_game_ids(session, scores_by_abbr, today)
        logger.info("MLB pitcher scores: %d game(s), %d stat row(s) recorded",
                    len(scores), _persist_pitcher_scores(session, scores))
        return scores
```

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_morning_slate.py -q` → all pass (its `pitcher_fetch` fixture monkeypatches `_remap_pitcher_scores_to_game_ids`, so the persistence path runs against a fake session; if that fixture's session cannot satisfy `session.get`, the `except` swallows it and the test still passes — confirm by reading the log line, not by assuming).

### Step 2: Tests for persistence

Create `backend/tests/test_pitcher_persistence.py`. Use a real in-memory
database, as `test_digest_selector.py` does (`get_engine(":memory:")`,
`Base.metadata.create_all`, seed `Team` and `Game` rows).

Tests (names as given):

1. `test_scores_are_written_for_both_sides` — one MLB game, scores
   `{"home": 0.72, "away": 0.41}`; after `_persist_pitcher_scores`, query
   `TeamStat` for `stat_type="pitcher_skill_score"` and assert two rows with
   the right team ids and values.
2. `test_rerunning_updates_rather_than_duplicates` — call twice, the second
   time with `{"home": 0.80, "away": 0.41}`; assert still exactly two rows
   and the home value is `0.80`.
3. `test_a_missing_side_writes_nothing_for_that_side` — `{"home": 0.72}`
   only; assert exactly one row, for the home team. (Absent is not neutral.)
4. `test_an_unknown_game_id_is_skipped` — `{9999: {...}}`; assert zero rows
   and no exception.
5. `test_a_write_failure_does_not_raise` — monkeypatch
   `backend.pipeline.team_stats._upsert_stats` to raise; assert the call
   returns an int and does not propagate.

**Mutation check, required** (repo rule: a test that still passes when the
implementation is broken is not a test): change `if value is None: continue`
to `value = sides.get(side, 0.5)`; test 3 must fail. Restore and report.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_pitcher_persistence.py -q` → 5 passed.

### Step 3: Ship the per-sport signal report

Create `backend/analysis/sport_signal_report.py`. Read-only: it measures, it
writes no rows, it generates no picks. Module docstring must state that, and
must carry the table from "Why this matters" as the reading this tool
produced when it was written.

Behaviour:

- Load every `status='final'` game with both scores and
  `season_type NOT IN NON_COMPETITIVE_PHASES`, exactly as
  `CalibratedModel.train_from_db` selects them.
- Split **within each sport** by date, `--train-frac` (default 0.7), so every
  sport appears in both halves. A global split would put a whole sport on one
  side of the line.
- Fit the production `CalibratedModel` on the train half only, via the
  `do_orm_execute` + `with_loader_criteria` approach
  `calibration_report.py` already uses. Do **not** reimplement the feature
  build; a second copy is how train and serve drift.

  **The existing helper takes one date; this report needs one per sport.**
  `calibration_report._games_before(session, split_date)` and `_fit_model`
  bound the fit with `with_loader_criteria(Game, Game.date < split_date)`,
  a single global cutoff. A global cutoff would put whole sports on one side
  of the line, which is the thing this report exists to avoid. Write a
  sibling context manager in **this** module that takes a
  `{sport: split_date}` mapping and builds a compound criterion:

  ```python
  from sqlalchemy import and_, or_
  from sqlalchemy.orm import with_loader_criteria

  criterion = or_(*[and_(Game.sport == sport, Game.date < cutoff)
                    for sport, cutoff in splits.items()])
  # then, inside a do_orm_execute event handler, for the duration of the fit:
  #   with_loader_criteria(Game, criterion, include_aliases=True)
  ```

  Model the handler's registration, teardown and the
  `EnsembleStrategy._calibrated` save/restore on `_games_before`
  (`calibration_report.py:259-281`) — read it and follow its shape rather
  than inventing one. A sport absent from `splits` contributes **no** games
  to the fit, which is correct: a sport with too few games to split must not
  leak its whole history into training.
- Score the eval half per sport and report, per sport: eval n, effective n,
  model Brier, base-rate Brier (the train half's home-win rate), and 0.25.
- `--min-eval` (default 40) below which a sport prints "not enough evaluation
  games" instead of a number.
- Report effective n from `calibration_report.effective_sample_size`, with
  the ICC stated as an assumption, and print a line saying to read the
  effective n rather than the raw count.
- Print, for each sport, whether the model beats the base-rate null, and say
  plainly in the footer that beating the base rate is a low bar and is not
  the same as beating the market price.
- `main(argv)` returning an int exit code, `--db` and `--sport` (optional
  filter) arguments, in the shape `calibration_report.main` uses.

**Verify**: `.venv/Scripts/python.exe -m backend.analysis.sport_signal_report --db C:\Users\mwill\Documents\mwilliams2733\sports_picks\sports_picks.db` → prints a table including nba and nfl rows; exits 0. Paste the output into your report.

### Step 4: Tests for the report

Create `backend/tests/test_sport_signal_report.py`. Seed a small in-memory
database; no network (the suite blocks sockets).

Tests (names as given):

1. `test_split_is_within_sport` — two sports with different date ranges;
   assert each sport has rows in both halves.
2. `test_a_sport_below_min_eval_is_not_scored` — a sport with 3 eval games
   and `--min-eval 40`; assert its row reports the refusal and carries no
   Brier number.
3. `test_brier_of_a_perfect_predictor_is_zero` — a direct unit test of the
   Brier helper on `[(1.0, 1), (0.0, 0)]` → `0.0`.
4. `test_base_rate_null_uses_the_train_half_only` — construct a fixture whose
   train-half home rate differs from the eval half's; assert the reported
   base-rate Brier matches the train-half rate. (This is the leakage guard.)
5. `test_report_writes_no_rows` — snapshot `TeamStat`, `PickModel` and
   `CalibrationHistory` counts before and after a full run; assert unchanged.

**Mutation check, required**: in the base-rate computation, use the eval half's
rate instead of the train half's; test 4 must fail. Restore and report.

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests/test_sport_signal_report.py -q` → 5 passed.

### Step 5: Record the refusal in the model

In `backend/analysis/calibrated_model.py`, add to the `CalibratedModel`
class docstring (or immediately above `train_from_db`) a block in the shape
`SPREAD_VALIDATED_SPORTS` uses — a refused change kept with its numbers so
nobody re-runs it:

- state that one slope per feature is shared across sports **by measurement,
  not by oversight**;
- reproduce the first table from "Why this matters" (pooled vs standardized
  vs per-sport, with eval n and effective n);
- say that per-sport slopes were worse in every sport that could support
  them, and standardization better only in ncaaf at effective n 40.8;
- name `backend.analysis.sport_signal_report` as the tool to re-run before
  revisiting this, and say to update the numbers here if it is.

Change no code in that file. This is a comment-only edit.

**Verify**: `git diff a6f342e..HEAD -- backend/analysis/calibrated_model.py | grep "^[-+]" | grep -v "^[-+][-+]" | grep -vE "^\+\s*#|^\+\s*$|^\+\s+[A-Za-z(\"']" | head` → shows no executable-looking change; and `.venv/Scripts/python.exe -m pytest backend/tests/test_calibrated_model.py -q` → all pass.

### Step 6: Full suite

**Verify**: `.venv/Scripts/python.exe -m pytest backend/tests -q` → all pass. Report exact counts against the 1461 baseline.

## Test plan

- New: `test_pitcher_persistence.py` (5 tests), `test_sport_signal_report.py`
  (5 tests), modeled on `test_digest_selector.py`'s in-memory database setup
  and `test_calibration_report.py`'s report-harness patterns.
- Two mutation checks, in Steps 2 and 4.
- Verification: the Full suite command.

## Done criteria

- [ ] `.venv/Scripts/python.exe -m pytest backend/tests -q` exits 0, count ≥ 1471
- [ ] `grep -n "PITCHER_STAT_TYPE\|_persist_pitcher_scores" backend/pipeline/scheduler.py` shows the constant, the helper and one call site
- [ ] `grep -rn "pitcher_skill_score" backend/pipeline/team_stats.py` → no matches (the vocabulary was not touched)
- [ ] `backend/analysis/sport_signal_report.py` runs against the production database and exits 0, output pasted in the report
- [ ] Both mutation checks performed and reported
- [ ] `git diff a6f342e..HEAD -- backend/analysis/calibrated_model.py` contains comment lines only
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- `mlb_pitcher_scores` is not present in `scheduler.py`, or does not match the
  excerpt — plan 019 may have been reverted.
- `_upsert_stats` has moved or changed signature in `team_stats.py`.
- `calibration_report.py` no longer exposes `effective_sample_size` or the
  `with_loader_criteria` fitting helper you intended to reuse.
- The report's own run shows nba Brier **above** 0.20 — that contradicts the
  0.1692 recorded here by a wide margin and means the fit or the split is
  wrong, not that the model changed.
- You find yourself editing how `CalibratedModel` fits anything.

## Maintenance notes

- **The decision this plan sets up, but does not make.** Once enough MLB picks
  carry persisted pitcher scores, re-run the report. If baseball's model still
  does not beat its base-rate null at a respectable effective n, the honest
  response is the pattern this repo already uses for spreads and totals: a
  validated-sports frozenset that refuses to generate moneyline picks for a
  sport whose model has no measured signal. That would currently exclude
  baseball. It is deliberately **not** in this plan, because the pitcher term
  is three days old and unmeasured, and killing the sport before measuring it
  would destroy the evidence needed to judge it.
- Roughly 150 graded MLB picks carrying pitcher scores is the point at which
  the question becomes answerable; at 12 picks a day that is about two weeks.
- The pooled-slope refusal recorded in Step 5 rests on one database snapshot
  at one split. Re-run before trusting it a season from now.
- A reviewer should check that the report writes nothing (test 5) and that
  `calibrated_model.py`'s diff is comments only.

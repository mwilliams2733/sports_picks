# Sport-Aware Home Advantage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `CalibratedModel` learn a different home-win baseline per sport,
so ncaab stops being scored with an intercept fitted almost entirely on nba.

**Architecture:** Add a fixed-vocabulary one-hot sport encoding to the
logistic regression's feature vector. One builder function produces the
vector for both training and prediction, so the two cannot drift. The
acceptance test is behavioural, not structural: with every difference feature
zeroed, the fitted model's P(home win) must land near each sport's empirical
home rate.

**Tech Stack:** Python 3.11+ (production runs 3.12), scikit-learn
`LogisticRegression`, SQLAlchemy 2.0, pytest.

**Spec:** No separate spec doc. The measured basis is in `plans/HANDOFF.md`
under "Why the moneyline picks lose — 2026-09-19", reproduced below.

---

## Global Constraints

- **This changes future pick generation only.** It does not rewrite existing
  picks, results or ROI. Nothing in `picks` or `pick_results` is touched.
- **No network in unit tests.** Training runs against an in-memory database
  the test seeds itself.
- `MIN_TRAINING_GAMES = 30` stays. A sport below that contributes rows but
  gets a coefficient shrunk toward the pooled mean by L2 — which is the
  desired behaviour, not a bug to work around.
- **`train_from_db` and `predict_home_win_prob` must build their feature
  vector through the same function.** They currently duplicate the legacy
  5-feature list in two places (`calibrated_model.py:192` and `:219`); that
  duplication is what this plan must not extend.
- Secrets live in `C:\Users\mwill\.secrets\shared.env`; reference by name,
  never hardcode.
- Backup `sports_picks.db` before any run that writes. This plan's tasks are
  read-only except Task 4, which only re-reads to measure.

## Measured starting state

`CalibratedModel.train_from_db` (`backend/analysis/calibrated_model.py:97`)
selects every `status='final'` game with **no sport filter** and fits one
logistic regression on five features:

```python
features = [elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff]
```

There is **no home term among them**. Home advantage lives entirely in the
single intercept. (`FEATURE_ORDER` contains `home_flag`, but that list feeds
the LightGBM path; `CalibratedModel` does not use it. `net_rating_diff` and
`pace_diff` are constant 0 — the dead features the calibration report already
names — so three features are live.)

| sport | final games | actual home win% |
|---|---|---|
| nba | 1248 | 0.554 |
| **ncaab** | **72** | **0.708** |
| mlb | 54 | 0.519 |
| **POOLED** | **1374** | **0.561** ← the single intercept |

nba is 91% of the pool. **LightGBM is used only for nba**
(`ensemble.py:149`: `if game.sport == "nba" and ...`), so every other sport —
all of ncaab — is scored by this pooled logistic regression.

Consequence, from 142 graded moneyline picks across 54 games:

```
  within big underdogs, price roughly constant:
  edge  0-20%   n=6    50.0% win   avg price +787   roi +3.213
  edge 20-35%   n=24   20.8% win   avg price +359   roi -0.123
  edge 35-50%   n=36    0.0% win   avg price +907   roi -1.000

  big-dog AWAY  n=61   6.6% win        big-dog HOME  n=5   80% win
  nba moneyline +0.795 roi            ncaab moneyline -0.457 roi
```

Pick generation tests home first (`if home_edge >= min_edge: ... elif
away_edge >= min_edge:`), yet away picks outnumber home 112 to 30 — the
fingerprint of a missing home term.

**Do not re-tune `min_edge` as a response.** The edge estimate is inverted
above ~10%, so raising the threshold selects harder for the defect.

## File Structure

| File | Responsibility |
|---|---|
| `backend/analysis/calibrated_model.py` (modify) | `SPORT_VOCAB`, `build_feature_row()`, and both call sites routed through it |
| `tests/test_calibrated_model_sport.py` (create) | Feature encoding and the fitted-baseline acceptance test |
| `backend/analysis/calibration_report.py` (modify, Task 4) | Report the fitted per-sport baseline alongside the Brier score |

## Out of scope

- The three dead features (`offensive_rating`, `defensive_rating`, `pace`)
  and the unused `home_flag`. They are real, already documented, and
  independent of this fix.
- The LightGBM path. It pools too, but is applied only to nba where the pool
  is 91% nba, so it is not currently mispriced.
- Backfilling or re-grading existing picks.

---

### Task 1: One feature builder, used by training and prediction

**Files:**
- Modify: `backend/analysis/calibrated_model.py`
- Test: `tests/test_calibrated_model_sport.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `SPORT_VOCAB: tuple[str, ...]` — the fixed sport ordering.
  - `build_feature_row(elo_diff: float, point_diff: float,
    net_rating_diff: float, rest_days_diff: float, pace_diff: float,
    sport: str) -> list[float]` — the 5 legacy features followed by one
    one-hot slot per entry in `SPORT_VOCAB`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calibrated_model_sport.py`:

```python
"""The feature row must carry sport, with a stable vocabulary."""

import pytest

from backend.analysis.calibrated_model import SPORT_VOCAB, build_feature_row


def _row(sport):
    return build_feature_row(1.0, 2.0, 3.0, 4.0, 5.0, sport)


def test_legacy_features_come_first_and_unchanged():
    row = _row("nba")
    assert row[:5] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_row_length_is_five_plus_the_vocabulary():
    assert len(_row("nba")) == 5 + len(SPORT_VOCAB)


def test_exactly_one_sport_slot_is_set():
    row = _row("ncaab")
    assert sum(row[5:]) == 1.0
    assert row[5 + SPORT_VOCAB.index("ncaab")] == 1.0


def test_an_unknown_sport_sets_no_slot_rather_than_raising():
    """Prediction must not crash on a sport the vocabulary omits.

    All-zero sport slots fall back to the shared intercept, which is exactly
    the old behaviour -- a safe degradation rather than an exception in the
    middle of pick generation.
    """
    row = _row("curling")
    assert sum(row[5:]) == 0.0
    assert len(row) == 5 + len(SPORT_VOCAB)


def test_vocabulary_is_fixed_not_derived_from_data():
    """Feature positions must not move when the database changes.

    A vocabulary built from whatever sports happen to be in the training set
    would silently reassign column meanings between one training run and the
    next.
    """
    assert isinstance(SPORT_VOCAB, tuple)
    assert "ncaab" in SPORT_VOCAB and "nba" in SPORT_VOCAB
    assert SPORT_VOCAB == tuple(sorted(SPORT_VOCAB)), "order must be deterministic"


def test_two_sports_produce_different_rows():
    assert _row("nba") != _row("ncaab")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibrated_model_sport.py -v`
Expected: FAIL — `ImportError: cannot import name 'SPORT_VOCAB'`.

- [ ] **Step 3: Implement**

In `backend/analysis/calibrated_model.py`, after `MIN_TRAINING_GAMES = 30`:

```python
#: Fixed sport ordering for the one-hot slots. Deliberately a constant and
#: not derived from the training data: a vocabulary built from whatever
#: sports happen to be present would reassign column meanings between runs,
#: so a model fitted one night would be read wrong the next.
#:
#: A sport absent from this tuple sets no slot and falls back to the shared
#: intercept -- the old behaviour, which is a safe degradation.
SPORT_VOCAB: tuple[str, ...] = (
    "boxing", "mlb", "mma", "nba", "ncaab", "ncaaf", "nfl",
)


def build_feature_row(
    elo_diff: float,
    point_diff: float,
    net_rating_diff: float,
    rest_days_diff: float,
    pace_diff: float,
    sport: str,
) -> list[float]:
    """The feature vector for CalibratedModel, for training AND prediction.

    Five legacy difference features, then one one-hot slot per sport.

    The sport slots exist because home advantage has no other representation
    in this model: there is no home feature, so it is carried entirely by the
    intercept. One intercept across a pool that is 91% nba scored ncaab -- a
    0.708 home-win sport -- as though it were 0.561.

    Both call sites route through here. They previously duplicated the
    five-element list, which is how the two could have drifted.
    """
    row = [elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff]
    row.extend(1.0 if sport == s else 0.0 for s in SPORT_VOCAB)
    return row
```

Then replace the training row (`calibrated_model.py`, in `train_from_db`):

```python
            # Legacy 5-feature format for logistic regression
            features = [elo_diff, point_diff, net_rating_diff, rest_days_diff, pace_diff]
```

with:

```python
            features = build_feature_row(
                elo_diff, point_diff, net_rating_diff, rest_days_diff,
                pace_diff, game.sport,
            )
```

And in `predict_home_win_prob`, replace:

```python
        feature_dict = extract_features(game)
        # CalibratedModel trains on legacy 5-feature format
        legacy_features = [
            feature_dict["elo_diff"],
            feature_dict["point_diff"],
            feature_dict["net_rating_diff"],
            feature_dict["home_rest_days"] - feature_dict["away_rest_days"],  # rest_days_diff
            feature_dict["pace_diff"],
        ]
        feature_arr = np.array([legacy_features], dtype=np.float64)
```

with:

```python
        feature_dict = extract_features(game)
        feature_arr = np.array([build_feature_row(
            feature_dict["elo_diff"],
            feature_dict["point_diff"],
            feature_dict["net_rating_diff"],
            feature_dict["home_rest_days"] - feature_dict["away_rest_days"],
            feature_dict["pace_diff"],
            game.sport,
        )], dtype=np.float64)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibrated_model_sport.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Prove the tests bite**

Three mutations, each restored afterwards:

1. Make `SPORT_VOCAB` derived: `tuple(sorted({"nba"}))`. Expected:
   `test_vocabulary_is_fixed_not_derived_from_data` and
   `test_exactly_one_sport_slot_is_set` FAIL.
2. Raise on an unknown sport (`row.extend(...)` → `SPORT_VOCAB.index(sport)`
   without a guard). Expected:
   `test_an_unknown_sport_sets_no_slot_rather_than_raising` FAILS.
3. Append the sport slots *before* the legacy features. Expected:
   `test_legacy_features_come_first_and_unchanged` FAILS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 716 passed (the current baseline) plus the new tests. This changes
the feature width of a model several strategies call, so a green full suite
is the point of this step.

- [ ] **Step 7: Commit**

```bash
git add backend/analysis/calibrated_model.py tests/test_calibrated_model_sport.py
git commit -m "feat(model): carry sport into the calibrated model's features"
```

---

### Task 2: The acceptance test — does the fitted baseline actually move?

**Files:**
- Test: `tests/test_calibrated_model_sport.py` (extend)

**Interfaces:**
- Consumes: `build_feature_row`, `SPORT_VOCAB`, `CalibratedModel` from Task 1.
- Produces: no new names.

Task 1 proves the *shape* changed. This proves the *behaviour* changed, which
is the only thing that matters. Without this task the plan is structural
rearrangement with no evidence it fixed anything.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calibrated_model_sport.py`:

```python
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.analysis.calibrated_model import CalibratedModel
from backend.data_types import GameData, TeamStats
from backend.models import Base, Game, Team


def _seed(session, sport, n_games, home_win_rate, start_team_id):
    """n_games of one sport where the home side wins home_win_rate of them.

    Every difference feature is zero -- identical team strength on both
    sides -- so the only thing distinguishing the sports is the label rate.
    Any probability difference the model then shows is attributable to the
    sport encoding and nothing else.
    """
    session.add_all([
        Team(id=start_team_id, name=f"{sport}H", abbreviation=f"{sport[:2]}H",
             sport=sport),
        Team(id=start_team_id + 1, name=f"{sport}A", abbreviation=f"{sport[:2]}A",
             sport=sport),
    ])
    wins = round(n_games * home_win_rate)
    for i in range(n_games):
        home_won = i < wins
        session.add(Game(
            sport=sport, season="2026", date=date(2026, 1, 1) + timedelta(days=i),
            home_team_id=start_team_id, away_team_id=start_team_id + 1,
            home_score=101 if home_won else 99,
            away_score=99 if home_won else 101,
            status="final",
        ))


def _flat_game(sport):
    """A GameData with every difference feature at zero."""
    # TeamStats has no defaults for home_record/away_record/last_n_record/
    # strength_of_schedule -- omitting them raises TypeError.
    stats = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.0, elo_rating=1500.0, rest_days=1,
    )
    return GameData(
        game_id=1, sport=sport, date=date(2026, 6, 1),
        home_team_id=1, away_team_id=2,
        home_stats=stats, away_stats=stats, odds=[],
    )


@pytest.fixture()
def trained_model():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        # Deliberately lopsided, the way production is: one sport dominates.
        _seed(s, "nba", 400, 0.55, 100)
        _seed(s, "ncaab", 80, 0.75, 200)
        s.commit()
        m = CalibratedModel()
        m.train_from_db(s)
        assert m.trained, "model must train for this test to mean anything"
        yield m


def test_the_fitted_baseline_differs_by_sport(trained_model):
    """The whole point of the change.

    With identical zeroed features, the only input differing is the sport, so
    the predictions must differ. Before this change they were identical by
    construction.
    """
    nba = trained_model.predict_home_win_prob(_flat_game("nba"))
    ncaab = trained_model.predict_home_win_prob(_flat_game("ncaab"))
    assert ncaab > nba + 0.08, (
        f"ncaab={ncaab:.3f} nba={nba:.3f}: the sport encoding is not moving "
        "the baseline"
    )


def test_each_baseline_lands_near_its_own_home_rate(trained_model):
    """Not merely different -- directionally right.

    L2 shrinks the minority sport toward the pool, so the tolerance is wide.
    The pooled rate here is ~0.583; ncaab must sit clearly above it.
    """
    nba = trained_model.predict_home_win_prob(_flat_game("nba"))
    ncaab = trained_model.predict_home_win_prob(_flat_game("ncaab"))
    assert 0.48 < nba < 0.64, f"nba baseline {nba:.3f} not near its 0.55"
    assert ncaab > 0.64, f"ncaab baseline {ncaab:.3f} not above the pooled rate"


def test_an_unseen_sport_still_predicts(trained_model):
    """mlb is in the vocabulary but absent from this training set."""
    p = trained_model.predict_home_win_prob(_flat_game("mlb"))
    assert 0.0 < p < 1.0
```

- [ ] **Step 2: Run to verify it fails on the OLD code**

Stash Task 1's change to `train_from_db` and `predict_home_win_prob` (keep
`build_feature_row` so the import resolves), then run:

`.venv/Scripts/python.exe -m pytest tests/test_calibrated_model_sport.py -k baseline -v`

Expected: `test_the_fitted_baseline_differs_by_sport` FAILS with the two
probabilities equal — the old model cannot tell the sports apart. **If it
passes on the old code, the test is not measuring what it claims.** Restore.

- [ ] **Step 3: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calibrated_model_sport.py -v`
Expected: PASS, 9 tests.

If `test_each_baseline_lands_near_its_own_home_rate` fails while
`test_the_fitted_baseline_differs_by_sport` passes, the encoding works but
regularisation is shrinking too hard. Do **not** widen the tolerance to make
it green — that discards the signal the test exists to detect. Reduce the
shrinkage instead (`LogisticRegression(max_iter=1000, C=10.0)`) and re-run,
and record the change in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/test_calibrated_model_sport.py
git commit -m "test(model): assert the fitted home baseline differs by sport"
```

---

### Task 3: Check it against the real database

**Files:** none changed. This task measures.

**Interfaces:**
- Consumes: the model from Tasks 1-2.
- Produces: numbers for Task 4's write-up.

- [ ] **Step 1: Fit on production data and read the baselines**

Read-only; open the database with `mode=ro`. Write this as a throwaway script
in the session scratchpad, not in the repo:

```python
import sqlite3
from datetime import date

from backend.analysis.calibrated_model import CalibratedModel
from backend.data_types import GameData, TeamStats
from backend.database import get_engine, get_session

DB = r"<abs path to sports_picks.db>"
session = get_session(get_engine(DB))
m = CalibratedModel()
m.train_from_db(session)
print("trained:", m.trained, "on", m.n_training_games, "games")

flat = TeamStats(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
                 last_n_record=(0, 0), offensive_rating=100.0,
                 defensive_rating=100.0, pace=100.0,
                 strength_of_schedule=0.0, elo_rating=1500.0, rest_days=1)
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
for sport, n, actual in con.execute(
    "SELECT sport, COUNT(*), "
    "       ROUND(1.0*SUM(CASE WHEN home_score > away_score THEN 1 ELSE 0 END)"
    "             /COUNT(*), 3) "
    "FROM games WHERE status='final' AND home_score IS NOT NULL "
    "GROUP BY sport ORDER BY 2 DESC"
):
    g = GameData(game_id=1, sport=sport, date=date(2026, 6, 1),
                 home_team_id=1, away_team_id=2,
                 home_stats=flat, away_stats=flat, odds=[])
    print(f"  {sport:<7} n={n:<6} actual={actual:<6} fitted="
          f"{m.predict_home_win_prob(g):.3f}")
```

Expected direction: ncaab's fitted baseline moves from ~0.56 up toward its
actual ~0.71, and nba stays near 0.55. **Record the actual numbers**; do not
assume they match the synthetic test.

- [ ] **Step 2: State the sample honestly**

ncaab has **72** final games. Its coefficient is estimated from those rows
and shrunk toward the pool. If the fitted baseline lands at, say, 0.63 rather
than 0.71, that is L2 doing its job on a thin sample, not a failure. Report
the number reached, the games behind it, and do not tune `C` to hit a target.

- [ ] **Step 3: Re-run the calibration report for both sports**

```bash
.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport ncaab --db "<abs path>"
.venv/Scripts/python.exe -m backend.analysis.calibration_report --sport nba --db "<abs path>"
```

Record the Brier score and effective n before and after. The pre-change ncaab
figure is **Brier 0.2116 on 37 games**, with observed above predicted in
every populated bin — the underconfidence this fix should reduce.

**nba must not get worse.** It is 91% of the pool and currently well served;
a regression there means the encoding is hurting the majority sport to help
the minority.

---

### Task 4: Surface the per-sport baseline in the report, and write it up

**Files:**
- Modify: `backend/analysis/calibration_report.py`
- Modify: `plans/HANDOFF.md`

**Interfaces:**
- Consumes: `CalibratedModel.predict_home_win_prob`, `SPORT_VOCAB`.
- Produces: no new names.

The report already carries a caveat about dead features. A fitted baseline
that silently drifts away from a sport's real home rate is the same class of
problem, and belongs in the same place.

- [ ] **Step 1: Carry the baseline on the Report, then render it**

`model` is **not** in scope where the Brier line is built: that function
renders strings from a frozen-ish `Report` dataclass (`calibration_report.py:182`),
while the model is fitted around line 320. Computing the probe there would
not compile. Add two fields to `Report` instead, populate them where the
model exists, and render from `r` like every other line.

Add to the `Report` dataclass:

```python
    #: P(home win) the fitted model gives when every difference feature is
    #: zero -- i.e. the baseline it has learned for this sport. Compared
    #: against ``actual_home_rate`` below, this is what exposes a pooled
    #: intercept being applied to a sport it does not fit.
    fitted_home_baseline: float | None = None
    actual_home_rate: float | None = None
```

Where the model is fitted (near `model = _fit_model(...)`), build the probe
from the games the report has **already loaded** — do not issue a second
query, or the two numbers will describe different populations:

```python
    from backend.analysis.calibrated_model import SPORT_VOCAB
    from backend.data_types import GameData, TeamStats

    flat = TeamStats(
        point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.0, elo_rating=1500.0, rest_days=1,
    )
    probe = GameData(
        game_id=0, sport=sport, date=split_date,
        home_team_id=0, away_team_id=0,
        home_stats=flat, away_stats=flat, odds=[],
    )
    fitted_home_baseline = model.predict_home_win_prob(probe)
    # `fit_games` is whatever local already holds the fitted window's games.
    # Read the surrounding code and use its real name.
    home_wins = sum(1 for g in fit_games if g.home_score > g.away_score)
    actual_home_rate = home_wins / len(fit_games) if fit_games else None
```

Then in the rendering function, after the Brier line:

```python
    if r.fitted_home_baseline is not None:
        lines.append("")
        lines.append(
            f"  Fitted home baseline : {r.fitted_home_baseline:.4f}"
        )
        if r.actual_home_rate is not None:
            lines.append(
                f"  Actual home win rate : {r.actual_home_rate:.4f}  "
                f"(gap {r.fitted_home_baseline - r.actual_home_rate:+.4f})"
            )
        if r.sport not in SPORT_VOCAB:
            lines.append(
                f"  WARNING: {r.sport!r} is absent from SPORT_VOCAB, so it "
                "shares the pooled"
            )
            lines.append(
                "  intercept and its baseline cannot differ from the pool."
            )
```

`SPORT_VOCAB` must be imported at module scope in the rendering function's
file for that check to work.

- [ ] **Step 2: Verify the warning path fires**

Temporarily pass `--sport hurling`. Expected: the WARNING prints. Restore.

If the report refuses an unknown sport before reaching this code, that is
fine — note it and skip the step rather than loosening the validation.

- [ ] **Step 3: Update the handoff**

Append to `plans/HANDOFF.md`: the before/after fitted baselines per sport from
Task 3, the Brier scores for ncaab and nba, and this explicit statement:

> This changes future pick generation only. The 348 graded ncaab picks and
> their -73.33 units are unaffected, and re-measuring ROI will not show an
> improvement until new picks are generated and settled.

Without that sentence the next reader will look for a ROI change that cannot
be there.

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/calibration_report.py plans/HANDOFF.md
git commit -m "feat(report): show the fitted home baseline against the real rate"
```

---

## Notes for whoever executes this

- **Task 2 Step 2 is the one that must not be skipped.** Confirming the
  acceptance test fails on the old code is the only evidence it measures the
  fix rather than restating it. A test asserting `ncaab > nba` that would
  have passed anyway proves nothing.
- **Do not widen a tolerance to get green.** If the baselines do not separate,
  the encoding or the shrinkage is wrong. Widening the assertion deletes the
  signal.
- **Watch nba.** It is 91% of the training pool. If its Brier or its fitted
  baseline degrades, the sport encoding is stealing from the majority to pay
  the minority, and the trade needs arguing rather than assuming.
- **`min_edge` stays where it is** until this lands and new picks settle. The
  edge estimate is currently inverted above ~10%; tuning the threshold
  against inverted edges optimises the wrong thing.

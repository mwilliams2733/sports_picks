# MMA Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MMA model's hand-set 70/20/10 blend and K=24 Elo — which cannot rate any fighter much beyond ~64% and so backs every underdog — with parameters fitted and measured on the 8,867 UFC bouts now in the db, without leaking results or the CSV's winner-first ordering; then measure the result against the market on the 115 priced bouts.

**Architecture:** A leak-free replay (`backend/analysis/combat_history.py`) walks every final MMA bout in date order and records, for each bout, the features the live pick generator would have seen *before* it (Elo, last-5 form, opponents' rating, fight count), updating Elo with the grader's own arithmetic (extracted to `backend/analysis/combat_elo.py`). A calibration module (`backend/analysis/combat_calibration.py`) fits a logistic model on *difference* features with no intercept, on data mirrored in both corners, choosing K on a validation window and reporting a held-out test window and the 115 market-priced bouts. The chosen K and coefficients become constants in `CombatSportsStrategy` for MMA (boxing keeps its blend), and MMA Elo is replayed with the new K at merge.

**Tech Stack:** Python 3.11, SQLAlchemy/SQLite, numpy, scikit-learn (already used by `calibrated_model`), pytest.

**Spec:** No separate spec. Design source: `docs/FINDINGS.md` 2026-10-10 (UFC history import; the underdog finding and the reviewer's note that the blend itself compresses toward 0.5) and the owner's 2026-10-10 instruction "write the MMA recalibration plan". The decisions below are binding.

## Design decisions

- **Symmetric by construction.** UFCStats lists the winner first: in the imported history the first-listed ("home") fighter won 5,601 of 8,814 decided bouts (64%), against 26-27 in odds-feed bouts. So the model uses only *differences* between the two fighters, has **no intercept**, and is fitted on every bout twice (as stored and mirrored, features negated, outcome flipped). P(A beats B) = 1 − P(B beats A) exactly.
- **Leak-free features.** Each bout's features come only from bouts before it, in (date, id) order, mirroring `pick_generator._build_fighter_stats` (last-5 form = wins/len, draws are not wins; opponent quality = the *current* rating of the last-5 opponents at that moment, as live; fight count capped at 5). Elo updates use the grader's arithmetic, extracted, never copied.
- **Only bouts the live model would price:** both fighters with ≥1 earlier bout (the MMA gate), decided (draws excluded from evaluation; they still update Elo as 0.5).
- **Time split:** fit on bouts before 2021-01-01, choose K on 2021-01-01..2023-12-31 (validation log-loss), report 2024-01-01 onward (test) once, after choosing. Candidate K: 16, 24, 32, 48, 64, 96.
- **Features (fixed list):** `elo_diff/400`, `form_diff`, `quality_diff/400` (0 when either side has no opponents), `log1p(fights_a) − log1p(fights_b)`.
- **Baseline:** the current blend at K=24, scored on the same bouts. The new model must beat it on test log-loss to be wired in; otherwise stop and report.
- **Market comparison** on the 115 MMA finals with prices (2026-03-21..2026-10-06): model vs the books' no-vig probability (latest stored price per book, averaged) — log-loss, Brier, and the share of simulated picks at the strategy's `min_edge` that are underdogs. n≈115 (effective n smaller: several bouts per card) cannot show an edge; it can show whether the "always the dog" pattern is gone.
- **Publishing stays off.** `TRACKING_ONLY_SPORTS = ("mma",)` is NOT changed by this plan. The final report gives the numbers; re-publishing MMA is the owner's decision.
- Boxing keeps the current blend and K=24 (no history to fit it on).

## Global Constraints

- One Elo arithmetic: the grader and the replay both call `combat_elo.elo_delta`; no second copy.
- Nothing here writes to the live db except the merge-time `rebuild_combat_elo("mma")` with the new K (scheduler and uvicorn stopped, `sqlite3.backup` first).
- Statistical claims state n and the window; correlated observations (one card) are noted.
- Backend tests: from the worktree root with the MAIN venv `/c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python -m pytest <file> -q -p no:cacheprovider`. Delete `__pycache__` after each mutation write and restore.
- Write regex/backslash code with Write/Edit, never bash heredocs. Commit only named files; never `config.yaml`. Trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Corner/orientation leak:** a dataset where the first-listed fighter always wins regardless of features must produce a model at ~0.5, not "first-listed wins". Pinned in Task 3.
2. **Result leak:** a bout's own result must not change its own features (and Elo must be read before the update). Pinned in Task 2.
3. **Replay ≠ live:** the replay's features for a bout must equal what `_build_fighter_stats` returns for that bout on a db built to that date. Pinned in Task 2.
4. **Selection on the test set:** K and coefficients are chosen without looking at the test window. Pinned by structure (Task 3) and review.
5. **Boxing unchanged / MMA symmetric live:** boxing probabilities identical to before; live MMA P(home) = 1 − P(away) when the fighters are swapped. Pinned in Task 5.

---

### Task 1: One Elo arithmetic for combat

**Files:**
- Create: `backend/analysis/combat_elo.py`
- Modify: `backend/pipeline/grader.py` (`_apply_combat_elo_update`, ~lines 252-262)
- Create: `backend/tests/test_combat_elo.py`

**Interfaces:**
- Produces: `elo_delta(home_rating: float, away_rating: float, actual_home: float, k: float) -> float` (home gains `delta`, away loses it); `actual_score(home_score, away_score) -> float` (1, 0, 0.5).

- [ ] **Step 1: Failing test** — `backend/tests/test_combat_elo.py`:

```python
from backend.analysis.combat_elo import actual_score, elo_delta


def test_equal_ratings_win_moves_half_k():
    assert elo_delta(1500, 1500, 1.0, 24) == 12.0
    assert elo_delta(1500, 1500, 0.5, 24) == 0.0


def test_upset_moves_more_than_expected_win():
    assert elo_delta(1400, 1600, 1.0, 24) > elo_delta(1600, 1400, 1.0, 24) > 0


def test_actual_score():
    assert (actual_score(1, 0), actual_score(0, 1), actual_score(1, 1)) == (1.0, 0.0, 0.5)
```

Run → FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement** — `backend/analysis/combat_elo.py`:

```python
"""The combat-sport Elo update, in one place: the grader applies it after each
bout and the calibration replay (combat_history) re-runs it over history."""


def actual_score(home_score, away_score) -> float:
    if home_score == away_score:
        return 0.5
    return 1.0 if (home_score or 0) > (away_score or 0) else 0.0


def elo_delta(home_rating: float, away_rating: float, actual_home: float, k: float) -> float:
    expected_home = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
    return k * (actual_home - expected_home)
```

In `grader._apply_combat_elo_update`, replace the lines from `h, a = home_elo_row.rating, away_elo_row.rating` through `delta = K * (actual_home - expected_home)` with:

```python
    from backend.analysis.combat_elo import actual_score, elo_delta
    delta = elo_delta(home_elo_row.rating, away_elo_row.rating,
                      actual_score(game.home_score, game.away_score), K)
```

- [ ] **Step 3: Run** `test_combat_elo.py`, `test_combat_integration.py`, `test_dedupe_combat_games.py`, `test_import_ufc_history.py` → PASS (the grader's behaviour is unchanged; those suites guard it).
- [ ] **Step 4: Mutation** — `elo_delta` with `/ 400` → `/ 200`: `test_combat_integration` or `test_combat_elo` FAILS. Restore.
- [ ] **Step 5: Commit** `refactor(combat): one Elo arithmetic for the grader and the replay`.

---

### Task 2: Leak-free replay of MMA history

**Files:**
- Create: `backend/analysis/combat_history.py`
- Create: `backend/tests/test_combat_history.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Bout(game_id, date, a, b, a_score: float)`; `load_bouts(session, sport="mma") -> list[Bout]` (final games with scores, ordered (date, id)); `@dataclass(frozen=True) BoutFeatures(game_id, date, a, b, elo_a, elo_b, form_a, form_b, quality_a, quality_b, fights_a, fights_b, outcome)`; `replay(bouts, k: float, last_n: int = 5) -> list[BoutFeatures]`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_combat_history.py`:

```python
from datetime import date

from backend.analysis.combat_history import Bout, replay


def _b(i, d, a, b, s):
    return Bout(game_id=i, date=date(2020, 1, d), a=a, b=b, a_score=s)


def test_features_are_taken_before_the_bout():                  # Review Focus 2
    bouts = [_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 3, 1.0)]
    f = replay(bouts, k=24)
    assert (f[0].elo_a, f[0].elo_b, f[0].fights_a, f[0].form_a) == (1500, 1500, 0, 0.5)
    assert f[1].elo_a == 1512 and f[1].fights_a == 1 and f[1].form_a == 1.0
    assert f[1].quality_a == 1488          # opponent 2's CURRENT rating (live rule)
    assert f[1].fights_b == 0 and f[1].quality_b is None


def test_a_bouts_own_result_never_changes_its_features():         # Review Focus 2
    win = replay([_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 2, 1.0)], k=24)[1]
    loss = replay([_b(1, 1, 1, 2, 1.0), _b(2, 2, 1, 2, 0.0)], k=24)[1]
    assert (win.elo_a, win.form_a, win.quality_a) == (loss.elo_a, loss.form_a, loss.quality_a)
    assert (win.outcome, loss.outcome) == (1.0, 0.0)


def test_form_and_count_use_the_last_five():
    bouts = [_b(i, i, 1, 10 + i, 1.0 if i <= 3 else 0.0) for i in range(1, 8)] + [_b(99, 20, 1, 50, 1.0)]
    last = replay(bouts, k=24)[-1]
    assert last.fights_a == 5 and last.form_a == 1 / 5      # last five: W L L L L
```

and a live-equivalence test (Review Focus 3) that builds a db with the same bouts as `Game` rows and checks `pick_generator._build_fighter_stats` matches `replay`'s features for the last bout:

```python
def test_replay_matches_the_live_feature_builder(db_engine, db_session):   # Review Focus 3
    from backend.analysis.combat_elo import elo_delta  # noqa: F401  (same arithmetic)
    from backend.models import Base, Game, Team
    from backend.pipeline.pick_generator import _build_fighter_stats
    from backend.scripts.dedupe_combat_games import rebuild_combat_elo
    Base.metadata.create_all(db_engine)
    for tid in range(1, 5):
        db_session.add(Team(id=tid, name=f"F{tid}", abbreviation=f"F{tid}", sport="mma"))
    db_session.flush()
    spec = [(1, 1, 1, 2, 1, 0), (2, 2, 3, 1, 1, 0), (3, 3, 2, 4, 1, 0)]
    for gid, d, h, a, hs, as_ in spec:
        db_session.add(Game(id=gid, sport="mma", season="2020", date=date(2020, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=hs, away_score=as_))
    db_session.commit()
    rebuild_combat_elo(db_session, "mma")
    live = _build_fighter_stats(db_session, 1, "mma", date(2020, 1, 30))
    bouts = [_b(g, d, h, a, 1.0 if hs > as_ else 0.0) for g, d, h, a, hs, as_ in spec] + [_b(9, 30, 1, 4, 1.0)]
    f = replay(bouts, k=24)[-1]
    assert (f.elo_a, f.form_a, f.fights_a) == (live.elo_rating, live.recent_form_score, live.fights_count)
    assert abs(f.quality_a - live.opponent_avg_elo) < 1e-9
```

(The live builder reads CURRENT Elo, which after `rebuild_combat_elo` equals the replay's pre-bout-9 state because bout 9 is not in the db.)

Run → FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement** — `backend/analysis/combat_history.py`:

```python
"""Leak-free replay of combat history for calibration.

For every final bout, in (date, id) order, the features the live pick
generator would have seen BEFORE it (`pick_generator._build_fighter_stats`):
current Elo, last-n form (wins / bouts; a draw is not a win), the CURRENT
rating of the last-n opponents, and the bout count capped at n. Elo is
updated after the features are taken, with the grader's arithmetic
(`combat_elo`), so no bout's result reaches its own features.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date

from backend.analysis.combat_elo import elo_delta
from backend.models import Game

SEED = 1500.0


@dataclass(frozen=True)
class Bout:
    game_id: int
    date: date
    a: int
    b: int
    a_score: float           # 1 win, 0 loss, 0.5 draw -- for fighter a


@dataclass(frozen=True)
class BoutFeatures:
    game_id: int
    date: date
    a: int
    b: int
    elo_a: float
    elo_b: float
    form_a: float
    form_b: float
    quality_a: float | None
    quality_b: float | None
    fights_a: int
    fights_b: int
    outcome: float


def load_bouts(session, sport: str = "mma") -> list[Bout]:
    from backend.analysis.combat_elo import actual_score
    rows = (session.query(Game)
            .filter(Game.sport == sport, Game.status == "final",
                    Game.home_score.isnot(None), Game.away_score.isnot(None))
            .order_by(Game.date, Game.id).all())
    return [Bout(g.id, g.date, g.home_team_id, g.away_team_id,
                 actual_score(g.home_score, g.away_score)) for g in rows]


def replay(bouts: list[Bout], k: float, last_n: int = 5) -> list[BoutFeatures]:
    rating: dict[int, float] = defaultdict(lambda: SEED)
    recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=last_n))   # (won, opponent)
    out: list[BoutFeatures] = []

    def side(fid: int) -> tuple[float, float, float | None, int]:
        past = recent[fid]
        if not past:
            return rating[fid], 0.5, None, 0
        wins = sum(1 for won, _ in past if won)
        quality = sum(rating[opp] for _, opp in past) / len(past)
        return rating[fid], wins / len(past), quality, len(past)

    for bout in bouts:
        ea, fa, qa, na = side(bout.a)
        eb, fb, qb, nb = side(bout.b)
        out.append(BoutFeatures(bout.game_id, bout.date, bout.a, bout.b, ea, eb, fa, fb,
                                qa, qb, na, nb, bout.a_score))
        delta = elo_delta(rating[bout.a], rating[bout.b], bout.a_score, k)
        rating[bout.a] += delta
        rating[bout.b] -= delta
        recent[bout.a].appendleft((bout.a_score == 1.0, bout.b))
        recent[bout.b].appendleft((bout.a_score == 0.0, bout.a))
    return out
```

Note `deque.appendleft` keeps the most recent first, matching the live builder's `order_by(date desc).limit(5)`; both read the opponents' rating as of NOW (the replay's current state).

- [ ] **Step 3: Run** `test_combat_history.py` → PASS (4). If the live-equivalence test differs, the replay is wrong, not the test — fix the replay to mirror `_build_fighter_stats` and ledger what differed.
- [ ] **Step 4: Mutations** — (a) take the features AFTER the Elo update → Review Focus 2 tests FAIL; (b) `deque(maxlen=last_n)` → unbounded → the last-five test FAILS. Restore each.
- [ ] **Step 5: Commit** `feat(mma): leak-free replay of combat history for calibration`.

---

### Task 3: Fit, choose K on validation, report the test window

**Files:**
- Create: `backend/analysis/combat_calibration.py`
- Create: `backend/tests/test_combat_calibration.py`

**Interfaces:**
- Consumes: `replay`, `load_bouts`, `BoutFeatures`; `CombatSportsStrategy._model_probability(home, away)` (baseline, called, not copied); `FighterStats`.
- Produces: `FEATURES = ("elo_diff", "form_diff", "quality_diff", "experience_diff")`; `design(f) -> np.ndarray` (shape (4,)); `eligible(f) -> bool`; `fit(features) -> np.ndarray` (coefficients); `probability(coef, f) -> float`; `baseline_probability(f) -> float`; `scores(probs, outcomes) -> dict(log_loss, brier, n, mean_abs_from_half)`; `evaluate(session, ks=(16, 24, 32, 48, 64, 96), fit_end=date(2021,1,1), val_end=date(2024,1,1)) -> dict`; CLI `python -m backend.analysis.combat_calibration --db <path>` printing the report as JSON.

- [ ] **Step 1: Failing tests** — `backend/tests/test_combat_calibration.py`:

```python
from datetime import date

import numpy as np

from backend.analysis.combat_calibration import design, eligible, fit, probability
from backend.analysis.combat_history import BoutFeatures


def _f(elo_a, elo_b, outcome, form_a=0.5, form_b=0.5, n_a=3, n_b=3):
    return BoutFeatures(1, date(2020, 1, 1), 1, 2, elo_a, elo_b, form_a, form_b,
                        1500.0, 1500.0, n_a, n_b, outcome)


def test_probability_is_symmetric_in_the_corners():               # Review Focus 1
    coef = np.array([1.2, 0.8, 0.3, 0.1])
    f = _f(1600, 1500, 1.0, form_a=0.8, form_b=0.4)
    g = BoutFeatures(1, f.date, 2, 1, f.elo_b, f.elo_a, f.form_b, f.form_a,
                     f.quality_b, f.quality_a, f.fights_b, f.fights_a, 0.0)
    assert abs(probability(coef, f) + probability(coef, g) - 1.0) < 1e-12


def test_first_listed_always_winning_is_not_learned():             # Review Focus 1
    # The CSV's artifact in its purest form: the first-listed fighter ALWAYS
    # wins, and the features are symmetric (each pairing appears in both
    # orders). Any corner term would learn "first wins"; this model must not.
    rng = np.random.default_rng(0)
    data = []
    for _ in range(1000):
        ea, eb = 1500 + rng.normal(0, 80), 1500 + rng.normal(0, 80)
        data += [_f(ea, eb, 1.0), _f(eb, ea, 1.0)]
    coef = fit(data)
    assert abs(probability(coef, _f(1500, 1500, 1.0)) - 0.5) < 1e-9   # no intercept, mirrored
    assert np.all(np.abs(coef) < 1e-3)                                 # nothing to learn but the order


def test_a_real_rating_signal_is_recovered():
    rng = np.random.default_rng(1)
    data = []
    for _ in range(4000):
        ea, eb = 1500 + rng.normal(0, 120), 1500 + rng.normal(0, 120)
        p = 1 / (1 + 10 ** (-(ea - eb) / 400))
        data.append(_f(ea, eb, float(rng.random() < p)))
    coef = fit(data)
    assert coef[0] > 1.5                     # elo_diff/400 coefficient near ln(10)=2.30


def test_eligibility_matches_the_live_gate():
    assert eligible(_f(1500, 1500, 1.0)) and not eligible(_f(1500, 1500, 0.5))
    assert not eligible(_f(1500, 1500, 1.0, n_a=0))
```

Run → FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement** — `backend/analysis/combat_calibration.py`:

```python
"""Fit and measure the MMA win-probability model on history (no leakage).

Symmetric by construction: difference features, no intercept, every bout
fitted as stored AND mirrored -- UFCStats lists the winner first (the
first-listed fighter won 64% of imported bouts), so any corner term would
learn the CSV's ordering, not fighting.

Time split: fit < fit_end, choose K on [fit_end, val_end), report >= val_end
once. Only bouts the live model would price: both fighters with >= 1 earlier
bout, decided (draws still move Elo, as 0.5, in the replay).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date

import numpy as np
from sklearn.linear_model import LogisticRegression

from backend.analysis.combat_history import BoutFeatures, load_bouts, replay
from backend.data_types import FighterStats

FEATURES = ("elo_diff", "form_diff", "quality_diff", "experience_diff")
BASELINE_K = 24


def design(f: BoutFeatures) -> np.ndarray:
    quality = 0.0 if f.quality_a is None or f.quality_b is None else (f.quality_a - f.quality_b) / 400
    return np.array([(f.elo_a - f.elo_b) / 400, f.form_a - f.form_b, quality,
                     math.log1p(f.fights_a) - math.log1p(f.fights_b)])


def eligible(f: BoutFeatures) -> bool:
    return f.fights_a > 0 and f.fights_b > 0 and f.outcome in (0.0, 1.0)


def fit(features: list[BoutFeatures]) -> np.ndarray:
    X = np.array([design(f) for f in features])
    y = np.array([f.outcome for f in features])
    model = LogisticRegression(fit_intercept=False, C=1.0, max_iter=1000)
    model.fit(np.vstack([X, -X]), np.concatenate([y, 1 - y]))
    return model.coef_[0]


def probability(coef: np.ndarray, f: BoutFeatures) -> float:
    return float(1 / (1 + math.exp(-float(design(f) @ coef))))


def _stats(elo, form, quality, n) -> FighterStats:
    return FighterStats(elo_rating=elo, recent_form_score=form, opponent_avg_elo=quality,
                        fights_count=n, days_since_last_fight=None)


def baseline_probability(f: BoutFeatures) -> float:
    """The live blend (CombatSportsStrategy._model_probability), called, not copied."""
    from backend.analysis.variants.combat_sports import CombatSportsStrategy
    strat = CombatSportsStrategy(name="baseline", config={})
    return strat._model_probability(_stats(f.elo_a, f.form_a, f.quality_a, f.fights_a),
                                    _stats(f.elo_b, f.form_b, f.quality_b, f.fights_b))


def scores(probs: list[float], outcomes: list[float]) -> dict:
    p = np.clip(np.array(probs), 1e-6, 1 - 1e-6)
    y = np.array(outcomes)
    return {"n": int(len(y)),
            "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "brier": round(float(np.mean((p - y) ** 2)), 4),
            "mean_abs_from_half": round(float(np.mean(np.abs(p - 0.5))), 4)}


def _window(features, start, end):
    return [f for f in features if eligible(f) and (start is None or f.date >= start)
            and (end is None or f.date < end)]


def evaluate(session, ks=(16, 24, 32, 48, 64, 96), fit_end=date(2021, 1, 1),
             val_end=date(2024, 1, 1)) -> dict:
    bouts = load_bouts(session, "mma")
    by_k = {}
    for k in ks:
        feats = replay(bouts, k)
        train, val = _window(feats, None, fit_end), _window(feats, fit_end, val_end)
        coef = fit(train)
        by_k[k] = {"coef": coef, "feats": feats,
                   "val": scores([probability(coef, f) for f in val], [f.outcome for f in val])}
    best = min(ks, key=lambda k: by_k[k]["val"]["log_loss"])
    # Refit on everything before the test window with the chosen K, then score
    # the test window ONCE.
    feats = by_k[best]["feats"]
    coef = fit(_window(feats, None, val_end))
    test = _window(feats, val_end, None)
    base_feats = by_k[BASELINE_K]["feats"] if BASELINE_K in by_k else replay(bouts, BASELINE_K)
    base_test = _window(base_feats, val_end, None)
    return {
        "bouts": len(bouts), "chosen_k": best, "features": list(FEATURES),
        "coef": [round(float(c), 4) for c in coef],
        "validation_by_k": {k: by_k[k]["val"] for k in ks},
        "test_new": scores([probability(coef, f) for f in test], [f.outcome for f in test]),
        "test_baseline_k24_blend": scores([baseline_probability(f) for f in base_test],
                                          [f.outcome for f in base_test]),
        "windows": {"fit_end": str(fit_end), "val_end": str(val_end)},
    }


def main(argv: list[str]) -> int:
    from backend.config import load_config
    from backend.database import get_engine, get_session
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    db = args.db or load_config("config.yaml")["database_path"]
    session = get_session(get_engine(db))
    try:
        print(json.dumps(evaluate(session), indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 3: Run** `test_combat_calibration.py` → PASS (4).
- [ ] **Step 4: Mutations** — (a) `fit_intercept=True` and no mirroring → the first-listed test FAILS; (b) drop the `-X` mirror only → FAILS. Restore.
- [ ] **Step 5: Run it on a snapshot of the live db** (read-only use): `python -m backend.analysis.combat_calibration --db <snapshot>`. Ledger the full JSON. **Gate:** if `test_new.log_loss >= test_baseline_k24_blend.log_loss`, STOP and report — the plan does not wire a model that is not better. Otherwise continue.
- [ ] **Step 6: Commit** `feat(mma): fit and measure the MMA win model on history (symmetric, time-split)`.

---

### Task 4: Against the market on the 115 priced bouts

**Files:**
- Modify: `backend/analysis/combat_calibration.py` (add `market_report`)
- Modify: `backend/tests/test_combat_calibration.py`

**Interfaces:**
- Produces: `market_report(session, coef, k, min_edge: float) -> dict` with `n`, `model`/`market` scores (`scores`), `simulated_picks`, `underdog_share`, `mean_model_on_picks`, `mean_market_on_picks`.

- [ ] **Step 1: Failing test** — append to `backend/tests/test_combat_calibration.py`:

```python
def test_market_report_scores_priced_eligible_bouts_and_counts_dog_picks(db_engine, db_session):
    from datetime import datetime
    from backend.analysis.combat_calibration import market_report
    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
    from backend.models import Base, Game, Odds, Team
    Base.metadata.create_all(db_engine)
    for tid in range(1, 5):
        db_session.add(Team(id=tid, name=f"F{tid}", abbreviation=f"F{tid}", sport="mma"))
    db_session.flush()
    # History so both fighters of bouts 3 and 4 have >= 1 earlier bout.
    for gid, d, h, a in [(1, 1, 1, 3), (2, 2, 2, 4)]:
        db_session.add(Game(id=gid, sport="mma", season="2026", date=date(2026, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=1, away_score=0))
    for gid, d, h, a in [(3, 10, 1, 2), (4, 11, 3, 4)]:
        db_session.add(Game(id=gid, sport="mma", season="2026", date=date(2026, 1, d),
                            home_team_id=h, away_team_id=a, status="final", home_score=1, away_score=0))
    db_session.flush()
    db_session.add_all([
        Odds(game_id=3, bookmaker="dk", moneyline_home=-200, moneyline_away=170,
             spread_home=0.0, spread_away=0.0, over_under=0.0, timestamp=datetime(2026, 1, 10)),
        Odds(game_id=4, bookmaker="dk", moneyline_home=150, moneyline_away=-180,
             spread_home=0.0, spread_away=0.0, over_under=0.0, timestamp=datetime(2026, 1, 11)),
    ])
    db_session.commit()
    coef = np.array([0.0, 0.0, 0.0, 0.0])         # model says 50% every time
    rep = market_report(db_session, coef, k=24, min_edge=5.0)
    assert rep["n"] == 2
    expected_home = remove_vig(american_to_implied_prob(-200), american_to_implied_prob(170))[0]
    assert abs(rep["market_probs"][0] - expected_home) < 1e-9
    # 50% vs a +170 dog and a +150 dog: both picks are the underdog.
    assert rep["simulated_picks"] == 2 and rep["underdog_share"] == 1.0
```

Run → FAIL (`ImportError: market_report`).

- [ ] **Step 2: Implement** — append to `backend/analysis/combat_calibration.py` (first read `Strategy._average_odds` in `backend/analysis/strategy.py`: if its signature is not `(self, game)` reading `game.odds` as `OddsSnapshot`s, adapt the `SimpleNamespace` below to it and ledger the change -- the point is to price exactly as the strategy does, not to re-derive an average):

```python
def market_report(session, coef, k: float, min_edge: float) -> dict:
    """The fitted model vs the books on MMA finals that have prices, and the
    picks the strategy's rule would have made from it. n is small and bouts
    on one card are correlated: this can show the underdog pattern, not an edge."""
    from collections import defaultdict
    from types import SimpleNamespace
    from backend.analysis.odds_utils import american_to_implied_prob, remove_vig, value_edge
    from backend.analysis.variants.combat_sports import CombatSportsStrategy
    from backend.data_types import OddsSnapshot
    from backend.models import Game, Odds

    feats = {f.game_id: f for f in replay(load_bouts(session, "mma"), k)}
    by_game = defaultdict(list)
    for o in (session.query(Odds).join(Game, Game.id == Odds.game_id)
              .filter(Game.sport == "mma", Game.status == "final")):
        if o.moneyline_home and o.moneyline_away:
            by_game[o.game_id].append(o)
    strat = CombatSportsStrategy(name="market_report", config={})
    model_p, market_p, ys = [], [], []
    picks = dogs = 0
    on_picks_model, on_picks_market = [], []
    for gid in sorted(by_game):
        f = feats.get(gid)
        if f is None or not eligible(f):
            continue
        rows = by_game[gid]
        nv = [remove_vig(american_to_implied_prob(o.moneyline_home),
                         american_to_implied_prob(o.moneyline_away))[0] for o in rows]
        m, p = sum(nv) / len(nv), probability(coef, f)
        model_p.append(p); market_p.append(m); ys.append(f.outcome)
        snaps = [OddsSnapshot(bookmaker=o.bookmaker, moneyline_home=o.moneyline_home,
                              moneyline_away=o.moneyline_away, spread_home=0.0,
                              spread_away=0.0, over_under=0.0) for o in rows]
        avg = strat._average_odds(SimpleNamespace(odds=snaps))
        if avg is None:
            continue
        for prob, price, mk in ((p, avg["moneyline_home"], m), (1 - p, avg["moneyline_away"], 1 - m)):
            if value_edge(prob, price) >= min_edge:      # home first, as predict() does
                picks += 1
                dogs += int(price > 0)
                on_picks_model.append(prob); on_picks_market.append(mk)
                break
    return {"n": len(ys), "model": scores(model_p, ys), "market": scores(market_p, ys),
            "market_probs": market_p, "simulated_picks": picks,
            "underdog_share": round(dogs / picks, 4) if picks else None,
            "mean_model_on_picks": round(float(np.mean(on_picks_model)), 4) if picks else None,
            "mean_market_on_picks": round(float(np.mean(on_picks_market)), 4) if picks else None}
```
- [ ] **Step 3: Run** the test → PASS. Then on the snapshot with the Task 3 `chosen_k`/`coef` and the live strategy's `min_edge` (read the `combat_sports` row's `config_json`). Ledger the JSON. State n and that it is ~10 cards (correlated), so it cannot show an edge.
- [ ] **Step 4: Commit** `feat(mma): compare the fitted MMA model with the market on priced bouts`.

---

### Task 5: Use the fitted model for MMA

**Files:**
- Modify: `backend/analysis/variants/combat_sports.py`
- Modify: `backend/analysis/elo.py` (`K_FACTORS["mma"]`)
- Modify: `backend/tests/test_combat_sports_strategy.py`

**Interfaces:**
- Produces: `MMA_COEF: tuple[float, float, float, float]` (Task 3's `coef`, in `FEATURES` order); `_model_probability(home, away, sport="mma")`; `K_FACTORS["mma"] = <chosen_k>`.

- [ ] **Step 1: Failing tests** in `test_combat_sports_strategy.py`:

```python
def test_mma_probability_is_symmetric_in_the_corners():           # Review Focus 5
    strat = CombatSportsStrategy(name="combat", config={})
    a = FighterStats(elo_rating=1650, recent_form_score=0.8, opponent_avg_elo=1550,
                     fights_count=5, days_since_last_fight=120)
    b = FighterStats(elo_rating=1500, recent_form_score=0.4, opponent_avg_elo=1480,
                     fights_count=3, days_since_last_fight=200)
    assert abs(strat._model_probability(a, b, "mma") + strat._model_probability(b, a, "mma") - 1) < 1e-9


def test_mma_uses_the_fitted_coefficients_and_boxing_keeps_the_blend():   # Review Focus 5
    from backend.analysis.variants import combat_sports as cs
    strat = CombatSportsStrategy(name="combat", config={})
    a = FighterStats(elo_rating=1600, recent_form_score=0.6, opponent_avg_elo=None,
                     fights_count=2, days_since_last_fight=None)
    b = FighterStats(elo_rating=1500, recent_form_score=0.5, opponent_avg_elo=None,
                     fights_count=2, days_since_last_fight=None)
    z = cs.MMA_COEF[0] * 100 / 400 + cs.MMA_COEF[1] * 0.1
    assert abs(strat._model_probability(a, b, "mma") - 1 / (1 + math.exp(-z))) < 1e-9
    blend = 0.78 / (1 + 10 ** (-100 / 400)) + 0.22 / (1 + 10 ** (-0.1 / 2.0))
    assert abs(strat._model_probability(a, b, "boxing") - blend) < 1e-9
```

(add `import math` at the top of the test file). Run → FAIL.

- [ ] **Step 2: Implement.** In `combat_sports.py` add, with the numbers from the Task 3 ledger entry:

```python
import math

#: MMA win model fitted 2026-10-<dd> on UFC history (backend/analysis/
#: combat_calibration.py; docs/FINDINGS.md): logistic on
#: (elo_diff/400, form_diff, quality_diff/400, log1p(fights) diff), no
#: intercept, fitted symmetric. Test window 2024+: <log-loss new vs baseline>.
MMA_COEF = (<c_elo>, <c_form>, <c_quality>, <c_experience>)
```

and make `_model_probability(self, home, away, sport="mma")` return, for `sport == "mma"`:

```python
            quality = (0.0 if home.opponent_avg_elo is None or away.opponent_avg_elo is None
                       else (home.opponent_avg_elo - away.opponent_avg_elo) / 400)
            z = (MMA_COEF[0] * (home.elo_rating - away.elo_rating) / 400
                 + MMA_COEF[1] * (home.recent_form_score - away.recent_form_score)
                 + MMA_COEF[2] * quality
                 + MMA_COEF[3] * (math.log1p(home.fights_count) - math.log1p(away.fights_count)))
            return max(0.05, min(0.95, 1 / (1 + math.exp(-z))))
```

keeping the existing blend for any other sport; `predict` passes `game.sport`. The live formula and `combat_calibration.design` must agree; pin it (add to the Step 1 tests, `import numpy as np` and `from datetime import date` at the top):

```python
def test_live_mma_formula_agrees_with_the_calibration():
    from backend.analysis.combat_calibration import probability
    from backend.analysis.combat_history import BoutFeatures
    from backend.analysis.variants import combat_sports as cs
    f = BoutFeatures(1, date(2026, 1, 1), 1, 2, 1620.0, 1540.0, 0.8, 0.4, 1560.0, 1500.0, 5, 3, 1.0)
    a = FighterStats(elo_rating=1620.0, recent_form_score=0.8, opponent_avg_elo=1560.0,
                     fights_count=5, days_since_last_fight=None)
    b = FighterStats(elo_rating=1540.0, recent_form_score=0.4, opponent_avg_elo=1500.0,
                     fights_count=3, days_since_last_fight=None)
    strat = CombatSportsStrategy(name="combat", config={})
    assert abs(probability(np.array(cs.MMA_COEF), f) - strat._model_probability(a, b, "mma")) < 1e-9
``` In `elo.py` set `K_FACTORS["mma"]` to `chosen_k` with a dated comment pointing to FINDINGS.

- [ ] **Step 3: Run** the strategy, calibration, integration and pick-generator tests, then the full suite → all pass (record count). Existing tests that assert the old MMA blend's numbers are updated with a ledgered ruling naming each (the owner asked for this recalibration).
- [ ] **Step 4: Mutation** — swap `MMA_COEF[0]` sign → the agreement and symmetry/coef tests FAIL. Restore.
- [ ] **Step 5: Commit** `feat(mma): fitted win model and K for MMA (boxing unchanged); picks stay tracking-only`.

---

### Task 6: Snapshot check and documentation

**Files:**
- Modify: `docs/FINDINGS.md` (new dated section), `docs/data-dictionary.md`

- [ ] **Step 1:** On a fresh `sqlite3.backup` snapshot: `rebuild_combat_elo(session, "mma")` (new K), then regenerate picks for the next three MMA card dates as the scheduler does (`generate_and_store_picks(session, <active game strategy id>, day, sports=("mma",))`), roll back. Record: picks, underdog share, mean model vs mean market no-vig, model-probability spread (min/max/sd), top-10 Elo names (still recognisable?).
- [ ] **Step 2:** FINDINGS: Task 3 JSON summary (chosen K, coefficients, validation log-loss by K, test new vs baseline with n and windows), Task 4 market comparison (n, scores, underdog share, the correlation caveat), Step 1 numbers, and a plain recommendation on re-publishing for the owner — including that 115 bouts cannot establish an edge. Data dictionary: MMA probabilities from the fitted model from the merge date; `K_FACTORS["mma"]` changed (Elo replayed at merge); MMA still tracking-only.
- [ ] **Step 3:** Commit `docs: MMA recalibration results`.

---

## Merge notes

1. Stop scheduler and uvicorn; `sqlite3.backup` -> `sports_picks.backup-<stamp>-pre-mma-recal.db`.
2. `git merge --no-ff feat/mma-recalibration`.
3. `python -c` (or a tiny script) running `rebuild_combat_elo(session, "mma")` with the new K, commit; verify the top-10 and the MMA Elo sd vs before (31.6).
4. Restart both (detached). MMA stays tracking-only; the owner decides publishing from the FINDINGS numbers.

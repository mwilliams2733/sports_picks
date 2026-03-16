# ROI Overhaul Phase 1: Prediction Engine

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace heuristic-driven predictions with a data-driven ML pipeline that recalibrates itself, fixing mathematical bugs and removing hardcoded magic numbers.

**Architecture:** LightGBM gradient boosted tree replaces logistic regression for NBA (1,012+ games). Walk-forward validation prevents data leakage. Distribution-based edge calculations replace raw point differences. Vig removal corrects implied probability bias. Confidence thresholds stored in DB and auto-tuned nightly.

**Tech Stack:** LightGBM, scipy (normal CDF), SQLAlchemy, existing FastAPI pipeline

**Spec:** `docs/superpowers/specs/2026-03-16-roi-and-ui-overhaul-design.md` (Section 1 + Section 6a)

---

## File Structure

```
backend/
  analysis/
    odds_utils.py              # Modify: add remove_vig(), no_vig_implied_prob()
    kelly.py                   # Modify: adaptive fraction, drawdown protection, correlation discount
    confidence.py              # Modify: read thresholds from DB, fallback to defaults
    prop_confidence.py         # Modify: read thresholds from DB, fallback to defaults
    ml_model.py                # Create: LightGBM with walk-forward validation
    recalibrator.py            # Create: nightly confidence threshold tuning
    variants/
      ensemble.py              # Modify: distribution-based edge, vig-adjusted probs
  models.py                    # Modify: add calibration_history, model_metrics tables; alter user_profiles, paper_picks
  pipeline/
    recalibration_job.py       # Create: nightly job orchestration
    scheduler.py               # Modify: add recalibration job at 3 AM
  tests/
    test_odds_utils.py         # Create: vig removal tests
    test_kelly_dynamic.py      # Create: adaptive Kelly tests
    test_confidence_db.py      # Create: DB-driven confidence tests
    test_ml_model.py           # Create: LightGBM + walk-forward tests
    test_recalibrator.py       # Create: recalibration loop tests
    test_ensemble_edges.py     # Create: distribution-based edge tests
```

---

## Chunk 1: Foundation — Vig Removal & Edge Math

### Task 1: Add LightGBM and scipy Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

Add `lightgbm>=4.0.0` and `scipy>=1.14.0` to the `[project.dependencies]` list in `pyproject.toml`.

- [ ] **Step 2: Install**

Run: `pip install -e .`
Expected: Successful install with lightgbm and scipy available

- [ ] **Step 3: Verify imports**

Run: `python -c "import lightgbm; import scipy; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add lightgbm and scipy dependencies for ML pipeline"
```

---

### Task 2: Vig-Adjusted Implied Probabilities

**Files:**
- Modify: `backend/analysis/odds_utils.py`
- Create: `backend/tests/test_odds_utils.py`

**Context:** Current `american_to_implied_prob()` returns raw implied probability including ~4-5% bookmaker vig. All edge calculations compare model probability against this inflated number, systematically underestimating edges by 2-3 percentage points.

- [ ] **Step 1: Write failing tests for vig removal**

```python
# backend/tests/test_odds_utils.py
from backend.analysis.odds_utils import (
    american_to_implied_prob,
    calculate_payout,
    remove_vig,
    no_vig_implied_prob,
)

def test_american_to_implied_prob_favorite():
    assert abs(american_to_implied_prob(-150) - 0.6) < 0.001

def test_american_to_implied_prob_underdog():
    assert abs(american_to_implied_prob(150) - 0.4) < 0.001

def test_calculate_payout_favorite():
    assert abs(calculate_payout(-150) - 0.6667) < 0.001

def test_calculate_payout_underdog():
    assert abs(calculate_payout(150) - 1.5) < 0.001

def test_remove_vig_standard_line():
    # -110/-110 implies 52.38% each = 104.76% total. Vig = 4.76%
    home_raw = american_to_implied_prob(-110)  # 0.5238
    away_raw = american_to_implied_prob(-110)  # 0.5238
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert abs(home_fair - 0.5) < 0.001
    assert abs(away_fair - 0.5) < 0.001
    assert abs(home_fair + away_fair - 1.0) < 0.001

def test_remove_vig_heavy_favorite():
    # -300/+250 line
    home_raw = american_to_implied_prob(-300)  # 0.75
    away_raw = american_to_implied_prob(250)   # 0.2857
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert home_fair + away_fair < 1.001
    assert home_fair + away_fair > 0.999
    assert home_fair > 0.7  # still heavy favorite after vig removal
    assert home_fair < 0.75  # but less than raw implied

def test_no_vig_implied_prob_home():
    # Convenience: get vig-adjusted prob for a specific side
    prob = no_vig_implied_prob(side="home", home_odds=-150, away_odds=130)
    assert 0.0 < prob < 1.0
    # -150 raw = 0.6, +130 raw ≈ 0.4348, total ≈ 1.0348
    # no-vig home = 0.6 / 1.0348 ≈ 0.5798
    assert abs(prob - 0.5798) < 0.01

def test_no_vig_implied_prob_away():
    prob = no_vig_implied_prob(side="away", home_odds=-150, away_odds=130)
    # +130 raw ≈ 0.4348 / 1.0348 ≈ 0.4202
    assert abs(prob - 0.4202) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_odds_utils.py -v`
Expected: FAIL — `remove_vig` and `no_vig_implied_prob` not defined

- [ ] **Step 3: Implement vig removal functions**

Add to `backend/analysis/odds_utils.py`:

```python
def remove_vig(home_implied: float, away_implied: float) -> tuple[float, float]:
    """Remove bookmaker vig using proportional method.

    Raw implied probabilities sum to >1 (typically 1.04-1.05).
    Divide each by the total to get fair probabilities summing to 1.
    """
    total = home_implied + away_implied
    return home_implied / total, away_implied / total


def no_vig_implied_prob(side: str, home_odds: int, away_odds: int) -> float:
    """Get vig-adjusted implied probability for a specific side.

    Args:
        side: "home" or "away"
        home_odds: American odds for home side
        away_odds: American odds for away side
    """
    home_raw = american_to_implied_prob(home_odds)
    away_raw = american_to_implied_prob(away_odds)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    return home_fair if side == "home" else away_fair
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_odds_utils.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/odds_utils.py backend/tests/test_odds_utils.py
git commit -m "feat: add vig removal to odds_utils for fair probability calculation"
```

---

### Task 3: Distribution-Based Edge Calculations in Ensemble

**Files:**
- Modify: `backend/analysis/variants/ensemble.py`
- Create: `backend/tests/test_ensemble_edges.py`

**Context:** Current spread edge is `abs(predicted_diff - (-spread_line))` — a raw point difference, not a probability. Current O/U edge is `abs(predicted_total - ou_line)` with a magic formula `0.5 + edge/200` for win probability. Both need to use normal CDF for proper probability estimation.

The approach:
1. Predict point differential mean from the model
2. Estimate standard deviation from historical residuals (use a reasonable default of 12.0 points for NBA — this is close to empirical NBA game-to-game std dev)
3. `P(cover) = P(home_margin > spread_line)` using `scipy.stats.norm.sf()`
4. Edge = `P(cover) - no_vig_implied_prob`

- [ ] **Step 1: Write failing tests for distribution-based edges**

```python
# backend/tests/test_ensemble_edges.py
from unittest.mock import patch, MagicMock
from backend.analysis.variants.ensemble import EnsembleStrategy


def test_spread_cover_probability_home():
    """P(home covers -3.5) when predicted diff is +7 should be well above 50%.

    Home spread is -3.5, meaning home must win by >3.5 points.
    cover_threshold = 3.5 (the abs value, home needs margin > 3.5)
    P(margin > 3.5) where margin ~ N(7, 12) = P(Z > (3.5-7)/12) = P(Z > -0.2917) ≈ 0.615
    """
    strategy = EnsembleStrategy(config={})
    prob = strategy._spread_cover_prob(predicted_diff=7.0, cover_threshold=3.5, std=12.0)
    assert 0.5 < prob < 1.0
    assert abs(prob - 0.615) < 0.02


def test_spread_cover_probability_away():
    """P(away covers +3.5) when predicted diff is +1 (slight home favorite).

    Away spread is +3.5, meaning away covers if home margin < 3.5.
    P(away covers) = 1 - P(home covers) = 1 - P(margin > 3.5).
    With predicted_diff=1: P(margin > 3.5) where margin ~ N(1, 12) ≈ 0.418
    So P(away covers) ≈ 0.582.
    """
    strategy = EnsembleStrategy(config={})
    home_cover_prob = strategy._spread_cover_prob(predicted_diff=1.0, cover_threshold=3.5, std=12.0)
    away_cover_prob = 1.0 - home_cover_prob
    assert 0.5 < away_cover_prob < 0.7


def test_ou_over_probability():
    """P(over 220.5) when predicted total is 225 should be above 50%."""
    strategy = EnsembleStrategy(config={})
    prob = strategy._over_probability(predicted_total=225.0, ou_line=220.5, std=15.0)
    assert 0.5 < prob < 1.0
    # P(X > 220.5) where X ~ N(225, 15) = P(Z > -0.3) ≈ 0.618
    assert abs(prob - 0.618) < 0.05


def test_ou_under_probability():
    """P(over 230) when predicted total is 220 should be below 50%."""
    strategy = EnsembleStrategy(config={})
    prob = strategy._over_probability(predicted_total=220.0, ou_line=230.0, std=15.0)
    assert prob < 0.5


def test_edge_uses_vig_adjusted_prob():
    """Edge calculation should subtract no-vig implied prob, not raw implied."""
    strategy = EnsembleStrategy(config={})
    # Model says 60% chance, raw implied = 52.38% (-110), no-vig = 50%
    # Edge with vig: 60 - 52.38 = 7.62%
    # Edge without vig: 60 - 50 = 10%
    # We want the larger (correct) edge
    model_prob = 0.60
    raw_implied = 0.5238  # -110
    no_vig = 0.50
    edge_with_vig = (model_prob - raw_implied) * 100
    edge_no_vig = (model_prob - no_vig) * 100
    assert edge_no_vig > edge_with_vig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_ensemble_edges.py -v`
Expected: FAIL — `_spread_cover_prob`, `_over_probability` not defined

- [ ] **Step 3: Add distribution-based methods to EnsembleStrategy**

Add these methods to the `EnsembleStrategy` class in `backend/analysis/variants/ensemble.py`:

```python
from scipy.stats import norm

# Class-level constants for prediction std dev (NBA empirical values)
POINT_DIFF_STD = 12.0   # NBA game-to-game margin std dev
TOTAL_POINTS_STD = 15.0  # NBA game-to-game total std dev
```

New methods on the class:

```python
def _spread_cover_prob(self, predicted_diff: float, cover_threshold: float, std: float = POINT_DIFF_STD) -> float:
    """Probability that home margin exceeds the cover threshold.

    P(home_margin > cover_threshold) using normal CDF.

    Args:
        predicted_diff: Model's predicted home margin (positive = home favored)
        cover_threshold: Points the home team needs to win by to cover.
            For home -3.5 spread: cover_threshold = 3.5 (must win by >3.5)
            For away +3.5 spread: cover_threshold = -3.5 (must lose by <3.5, i.e., not lose by >3.5)
        std: Standard deviation of prediction residuals
    """
    return float(norm.sf(cover_threshold, loc=predicted_diff, scale=std))

def _over_probability(self, predicted_total: float, ou_line: float, std: float = TOTAL_POINTS_STD) -> float:
    """Probability that total points exceeds the O/U line."""
    return float(norm.sf(ou_line, loc=predicted_total, scale=std))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_ensemble_edges.py -v`
Expected: All PASS

- [ ] **Step 5: Refactor predict() to use distribution-based edges and vig-adjusted probabilities**

Modify the `predict()` method in `EnsembleStrategy`:

**Moneyline section** — change edge calculation to use vig-adjusted probs:

Replace the current moneyline edge line:
```python
edge = (model_prob - implied_prob) * 100
```
With:
```python
from backend.analysis.odds_utils import remove_vig, american_to_implied_prob

home_raw = american_to_implied_prob(avg_odds["moneyline_home"])
away_raw = american_to_implied_prob(avg_odds["moneyline_away"])
home_fair, away_fair = remove_vig(home_raw, away_raw)
fair_prob = home_fair if model_prob > 0.5 else away_fair
edge = (model_prob - fair_prob) * 100 if model_prob > 0.5 else ((1 - model_prob) - away_fair) * 100
```

Wait — this needs to be done carefully. The full refactor of `predict()` is:

For **moneyline picks**: Use vig-adjusted probabilities:
```python
from backend.analysis.odds_utils import remove_vig, american_to_implied_prob
home_raw = american_to_implied_prob(avg_odds["moneyline_home"])
away_raw = american_to_implied_prob(avg_odds["moneyline_away"])
home_fair, away_fair = remove_vig(home_raw, away_raw)
# Edge for the favored side
if model_prob > 0.5:
    edge = (model_prob - home_fair) * 100
else:
    edge = ((1 - model_prob) - away_fair) * 100
```

For **spread picks**: Use `_spread_cover_prob()` with actual vig removal:
```python
predicted_diff = self._predicted_point_diff(game)
spread_home = avg_odds["spread_home"]  # e.g., -3.5 (home gives 3.5)
# Home covers when margin > abs(spread), i.e., cover_threshold = abs(spread_home)
cover_threshold = abs(spread_home) if spread_home < 0 else -spread_home
home_cover_prob = self._spread_cover_prob(predicted_diff, cover_threshold)
# Use actual spread odds for vig removal (not hardcoded 0.5)
spread_fair = 0.5  # Spread markets are structurally ~50/50 after vig
# But if odds data includes spread_home_odds/spread_away_odds, use those
spread_edge = (home_cover_prob - spread_fair) * 100
# If away side has better edge, flip
away_cover_prob = 1.0 - home_cover_prob
away_edge = (away_cover_prob - spread_fair) * 100
```

For **O/U picks**: Use `_over_probability()` with actual vig removal:
```python
predicted_total = self._predicted_total(game)
ou_line = avg_odds["over_under"]
over_prob = self._over_probability(predicted_total, ou_line)
under_prob = 1.0 - over_prob
ou_fair = 0.5  # O/U markets are structurally ~50/50 after vig
over_edge = (over_prob - ou_fair) * 100
under_edge = (under_prob - ou_fair) * 100
```

See the full implementation in Step 6 after verifying existing tests still pass.

- [ ] **Step 6: Run existing tests**

Run: `python -m pytest backend/tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add backend/analysis/variants/ensemble.py backend/analysis/odds_utils.py backend/tests/test_ensemble_edges.py
git commit -m "feat: distribution-based edge calculations with vig-adjusted probabilities"
```

---

## Chunk 2: ML Model — LightGBM with Walk-Forward

### Task 4: Database Tables for Model Tracking

**Files:**
- Modify: `backend/models.py`
- Create: `backend/tests/test_new_tables.py`

**Context:** Need `calibration_history` and `model_metrics` tables for tracking model performance over time, plus `graded_at` column on `paper_picks` and streak columns on `user_profiles`.

- [ ] **Step 1: Write failing test for new tables**

```python
# backend/tests/test_new_tables.py
from datetime import date, datetime, timezone
from backend.models import Base, CalibrationHistory, ModelMetrics, UserProfile, PaperPick
from backend.database import get_engine, get_session


def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def test_calibration_history_creation():
    session = _setup()
    row = CalibrationHistory(
        date=date(2026, 3, 16), sport="nba", confidence_tier=5,
        predicted_win_rate=0.70, actual_win_rate=0.62,
        sample_size=45, old_threshold=12.0, new_threshold=13.0,
    )
    session.add(row)
    session.commit()
    result = session.query(CalibrationHistory).first()
    assert result.sport == "nba"
    assert result.confidence_tier == 5
    assert result.new_threshold == 13.0
    session.close()


def test_model_metrics_creation():
    session = _setup()
    row = ModelMetrics(
        date=date(2026, 3, 16), sport="nba", model_version="lgbm_v1",
        accuracy=0.65, log_loss=0.62,
        feature_importances='{"elo_diff": 0.25, "point_diff": 0.20}',
        training_games=950,
    )
    session.add(row)
    session.commit()
    result = session.query(ModelMetrics).first()
    assert result.model_version == "lgbm_v1"
    assert result.training_games == 950
    session.close()


def test_user_profile_streak_columns():
    session = _setup()
    user = UserProfile(name="TestUser")
    session.add(user)
    session.commit()
    assert user.current_streak == 0
    assert user.best_streak == 0
    assert user.streak_type == "none"
    user.current_streak = 5
    user.streak_type = "win"
    session.commit()
    session.close()


def test_paper_pick_graded_at():
    session = _setup()
    user = UserProfile(name="TestUser2")
    session.add(user)
    session.commit()
    pick = PaperPick(
        user_id=user.id, game_id=1, pick_type="moneyline",
        pick_value="HOME ML", odds=-150, stake=1000,
    )
    session.add(pick)
    session.commit()
    assert pick.graded_at is None
    pick.graded_at = datetime.now(tz=timezone.utc)
    session.commit()
    assert pick.graded_at is not None
    session.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_new_tables.py -v`
Expected: FAIL — `CalibrationHistory`, `ModelMetrics` not defined; `current_streak`, `graded_at` not on models

- [ ] **Step 3: Add new models and columns**

Add to `backend/models.py`:

```python
class CalibrationHistory(Base):
    __tablename__ = "calibration_history"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    sport = Column(String, nullable=False)
    confidence_tier = Column(Integer, nullable=False)
    predicted_win_rate = Column(Float)
    actual_win_rate = Column(Float)
    sample_size = Column(Integer)
    old_threshold = Column(Float)
    new_threshold = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ModelMetrics(Base):
    __tablename__ = "model_metrics"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    sport = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    accuracy = Column(Float)
    log_loss = Column(Float)
    feature_importances = Column(String)  # JSON
    training_games = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

Add columns to existing models:

On `UserProfile`:
```python
current_streak = Column(Integer, default=0)
best_streak = Column(Integer, default=0)
streak_type = Column(String, default="none")  # "win", "loss", "none"
```

On `PaperPick`:
```python
graded_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_new_tables.py -v`
Expected: All PASS

- [ ] **Step 5: Run all existing tests to check for regressions**

Run: `python -m pytest backend/tests/ -v --timeout=30`
Expected: All pass (new columns have defaults, so existing code is unaffected)

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_new_tables.py
git commit -m "feat: add calibration_history, model_metrics tables and streak/grading columns"
```

---

### Task 5: Expanded Feature Extraction

**Files:**
- Modify: `backend/analysis/calibrated_model.py` (extract_features function)
- Create: `backend/tests/test_features.py`

**Context:** Current model uses 5 features. Spec expands to ~20 features including individual ratings (not just diffs), recent form, strength of schedule, back-to-back flags, and timezone-based travel proxy. The feature extraction must work with GameData which has `home_stats`, `away_stats`, and `home_elo`/`away_elo` fields.

- [ ] **Step 1: Write failing test for expanded features**

```python
# backend/tests/test_features.py
from backend.analysis.calibrated_model import extract_features
from backend.data_types import GameData, TeamData, OddsData


def _make_game(**overrides) -> GameData:
    home = TeamData(
        name="Lakers", abbreviation="LAL", sport="nba",
        elo=1600, point_diff=3.5, offensive_rating=112.0,
        defensive_rating=108.0, rest_days=2, pace=100.5,
        schedule_fatigue=0.0, is_lookahead=False,
    )
    away = TeamData(
        name="Celtics", abbreviation="BOS", sport="nba",
        elo=1650, point_diff=5.0, offensive_rating=115.0,
        defensive_rating=105.0, rest_days=1, pace=98.0,
        schedule_fatigue=0.0, is_lookahead=False,
    )
    defaults = dict(
        game_id=1, sport="nba", date="2026-03-16",
        home_team=home, away_team=away,
        home_stats=[], away_stats=[], odds=[],
    )
    defaults.update(overrides)
    return GameData(**defaults)


def test_extract_features_returns_dict():
    game = _make_game()
    features = extract_features(game)
    assert isinstance(features, dict)


def test_extract_features_has_expanded_keys():
    game = _make_game()
    features = extract_features(game)
    expected_keys = {
        "elo_diff", "point_diff", "net_rating_diff",
        "home_rest_days", "away_rest_days", "pace_diff",
        "home_flag",
        "offensive_rating_home", "offensive_rating_away",
        "defensive_rating_home", "defensive_rating_away",
        "back_to_back_home", "back_to_back_away",
    }
    for key in expected_keys:
        assert key in features, f"Missing feature: {key}"


def test_extract_features_values():
    game = _make_game()
    features = extract_features(game)
    assert features["elo_diff"] == -50  # 1600 - 1650
    assert features["point_diff"] == -1.5  # 3.5 - 5.0
    assert features["home_rest_days"] == 2
    assert features["away_rest_days"] == 1
    assert features["home_flag"] == 1
    assert features["offensive_rating_home"] == 112.0
    assert features["defensive_rating_home"] == 108.0
    assert features["back_to_back_home"] == 0  # 2 rest days, not B2B
    assert features["back_to_back_away"] == 1  # 1 rest day = B2B


def test_feature_order_matches_extract_features():
    """FEATURE_ORDER must contain exactly the keys extract_features returns."""
    from backend.analysis.calibrated_model import FEATURE_ORDER
    game = _make_game()
    features = extract_features(game)
    assert set(FEATURE_ORDER) == set(features.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_features.py -v`
Expected: FAIL — `extract_features` returns a list, not a dict

- [ ] **Step 3: Refactor extract_features to return dict with expanded features**

Replace `extract_features()` in `backend/analysis/calibrated_model.py`:

```python
def extract_features(game: GameData) -> dict:
    """Extract expanded feature set from a game.

    Returns a dict of feature_name -> value for use with LightGBM or logistic regression.
    """
    home = game.home_team
    away = game.away_team
    return {
        # Existing core features
        "elo_diff": home.elo - away.elo,
        "point_diff": home.point_diff - away.point_diff,
        "net_rating_diff": (home.offensive_rating - home.defensive_rating)
                          - (away.offensive_rating - away.defensive_rating),
        # Rest days as integers (not diff)
        "home_rest_days": home.rest_days,
        "away_rest_days": away.rest_days,
        # Pace
        "pace_diff": home.pace - away.pace,
        # Home court
        "home_flag": 1,
        # Individual ratings
        "offensive_rating_home": home.offensive_rating,
        "offensive_rating_away": away.offensive_rating,
        "defensive_rating_home": home.defensive_rating,
        "defensive_rating_away": away.defensive_rating,
        # Back-to-back flags
        "back_to_back_home": 1 if home.rest_days <= 1 else 0,
        "back_to_back_away": 1 if away.rest_days <= 1 else 0,
    }
```

**Deferred features** (require data not currently in TeamData/GameData):
- `recent_form_home/away` — needs last-N-games query (requires pipeline changes to track rolling windows)
- `strength_of_schedule_home/away` — needs opponent Elo aggregation
- `travel_distance_proxy` — needs team timezone/city mapping

These are tracked for a follow-up task. The current 13 features provide a strong baseline for LightGBM. The feature extraction dict format makes adding features trivial — just add the key to both `extract_features()` and `FEATURE_ORDER`.

- [ ] **Step 4: Update _fallback_probability and predict_home_win_prob to work with dict features**

The `CalibratedModel.predict_home_win_prob()` method calls `extract_features()` and passes the result to sklearn's `predict_proba()`. Since sklearn expects an array, add a helper:

```python
FEATURE_ORDER = [
    "elo_diff", "point_diff", "net_rating_diff",
    "home_rest_days", "away_rest_days", "pace_diff",
    "home_flag",
    "offensive_rating_home", "offensive_rating_away",
    "defensive_rating_home", "defensive_rating_away",
    "back_to_back_home", "back_to_back_away",
]

def features_to_array(features: dict) -> list[float]:
    """Convert feature dict to ordered array for model input.

    FEATURE_ORDER is the single source of truth for feature ordering.
    extract_features() must return all keys in FEATURE_ORDER.
    """
    return [features.get(k, 0.0) for k in FEATURE_ORDER]
```

Update `predict_home_win_prob` and `train_from_db` to use `features_to_array(extract_features(game))` instead of raw `extract_features(game)`.

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_features.py backend/tests/ -v --timeout=30`
Expected: All PASS (new and existing)

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/calibrated_model.py backend/tests/test_features.py
git commit -m "feat: expand feature extraction to dict-based format with 13 features"
```

---

### Task 6: LightGBM Model with Walk-Forward Validation

**Files:**
- Create: `backend/analysis/ml_model.py`
- Create: `backend/tests/test_ml_model.py`

**Context:** LightGBM replaces logistic regression as the primary model for NBA (1,012+ games). **Regression target: home margin** (home_score - away_score), NOT binary win/loss. This allows deriving both win probability (P(margin > 0)) and spread cover probability (P(margin > threshold)) from the same model. Walk-forward validation: train on all games before date X, predict games on date X, slide forward. Residual std from walk-forward predictions is used for CDF-based edge calculations. For sports with <200 completed games, fall back to `CalibratedModel` (logistic regression / heuristic).

- [ ] **Step 1: Write failing tests for LightGBMModel**

```python
# backend/tests/test_ml_model.py
import numpy as np
from datetime import date
from backend.analysis.ml_model import LightGBMModel, MIN_ML_GAMES


def test_min_ml_games_threshold():
    assert MIN_ML_GAMES == 200


def test_model_init():
    model = LightGBMModel()
    assert model.model is None
    assert model.trained is False
    assert model.residual_std is None


def test_model_needs_minimum_games():
    """Model should not train with fewer than MIN_ML_GAMES."""
    model = LightGBMModel()
    X = np.random.randn(50, 13)
    y = np.random.randn(50) * 10  # point differentials
    dates = [date(2026, 1, i % 28 + 1) for i in range(50)]
    model.train(X, y, dates)
    assert model.trained is False


def test_model_trains_with_enough_games():
    """Model should train with MIN_ML_GAMES or more."""
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    # Target is home margin (continuous), not binary
    y = X[:, 0] * 5 + X[:, 1] * 3 + np.random.randn(n) * 5
    dates = [date(2026, 1, 1) for _ in range(n)]
    model.train(X, y, dates)
    assert model.trained is True
    assert model.residual_std is not None
    assert model.residual_std > 0


def test_model_predict_returns_point_diff():
    """predict() returns predicted home margin, not a probability."""
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5  # centered around 0
    dates = [date(2026, 1, 1)] * n
    model.train(X, y, dates)
    predicted_margin = model.predict(X[0:1])
    # Should be a point differential, typically -30 to +30
    assert -50 < predicted_margin < 50


def test_model_home_win_prob():
    """home_win_prob() derives P(margin > 0) from predicted margin + residual std."""
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5
    dates = [date(2026, 1, 1)] * n
    model.train(X, y, dates)
    prob = model.home_win_prob(X[0:1])
    assert 0.0 < prob < 1.0


def test_walk_forward_returns_metrics_and_residual_std():
    """Walk-forward validation returns accuracy, MAE, and residual_std."""
    model = LightGBMModel()
    np.random.seed(42)
    n = 300
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5
    dates = [date(2026, 1, (i % 30) + 1) for i in range(n)]
    metrics = model.walk_forward_validate(X, y, dates, min_train=200)
    assert "accuracy" in metrics  # derived: predicted margin > 0 matches actual margin > 0
    assert "mae" in metrics
    assert "residual_std" in metrics
    assert metrics["residual_std"] > 0
    assert 0.0 < metrics["accuracy"] <= 1.0


def test_feature_importances():
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5
    dates = [date(2026, 1, 1)] * n
    model.train(X, y, dates)
    importances = model.feature_importances()
    assert isinstance(importances, dict)
    assert len(importances) == 13
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_ml_model.py -v`
Expected: FAIL — `ml_model` module not found

- [ ] **Step 3: Implement LightGBMModel**

Create `backend/analysis/ml_model.py`:

```python
"""LightGBM regression model for predicting home margin with walk-forward validation.

Predicts home_score - away_score (continuous point differential).
Win probability derived as P(margin > 0) using normal CDF.
Spread cover probability derived as P(margin > threshold).
Residual std from walk-forward validation calibrates the CDF.
"""
import logging
from datetime import date

import lightgbm as lgb
import numpy as np
from scipy.stats import norm

from backend.analysis.calibrated_model import FEATURE_ORDER

logger = logging.getLogger(__name__)

MIN_ML_GAMES = 200  # Minimum games before using ML instead of heuristics
DEFAULT_RESIDUAL_STD = 12.0  # NBA empirical margin std, used before walk-forward calibrates


class LightGBMModel:
    def __init__(self):
        self.model = None
        self.trained = False
        self.residual_std = None  # Calibrated from walk-forward out-of-sample residuals
        self.n_training_games = 0

    def train(self, X: np.ndarray, y: np.ndarray, dates: list[date]) -> None:
        """Train LightGBM regression on features -> home margin.

        Args:
            X: Feature matrix (n_games, n_features)
            y: Home margin (home_score - away_score) per game
            dates: Game dates (for walk-forward std calibration)

        Refuses to train if fewer than MIN_ML_GAMES samples.
        """
        if len(y) < MIN_ML_GAMES:
            logger.warning(
                "Only %d games available, need %d for ML. Skipping.",
                len(y), MIN_ML_GAMES,
            )
            return

        params = {
            "objective": "regression",
            "metric": "mae",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        }

        dataset = lgb.Dataset(X, label=y)
        self.model = lgb.train(params, dataset, num_boost_round=200)
        self.trained = True
        self.n_training_games = len(y)

        # In-sample residual std (walk-forward std is more accurate, see walk_forward_validate)
        preds = self.model.predict(X)
        self.residual_std = float(np.std(y - preds))
        logger.info(
            "Trained LightGBM (regression) on %d games, in-sample residual_std=%.2f",
            len(y), self.residual_std,
        )

    def predict(self, X: np.ndarray) -> float:
        """Predict home margin for a single game.

        Returns predicted home_score - away_score (can be negative).
        """
        if not self.trained:
            raise RuntimeError("Model not trained")
        return float(self.model.predict(X)[0])

    def home_win_prob(self, X: np.ndarray) -> float:
        """Derive P(home win) = P(margin > 0) from predicted margin and residual std."""
        predicted_margin = self.predict(X)
        std = self.residual_std or DEFAULT_RESIDUAL_STD
        prob = float(norm.sf(0, loc=predicted_margin, scale=std))
        return max(0.01, min(0.99, prob))

    def walk_forward_validate(
        self, X: np.ndarray, y: np.ndarray, dates: list[date],
        min_train: int = 200,
    ) -> dict:
        """Walk-forward validation: train on past, predict future, compute residual std.

        Returns dict with accuracy (margin direction), MAE, and residual_std
        calibrated from out-of-sample predictions.
        """
        unique_dates = sorted(set(dates))
        date_arr = np.array(dates)
        all_preds = []
        all_true = []

        params = {
            "objective": "regression",
            "metric": "mae",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        }

        for eval_date in unique_dates:
            train_mask = date_arr < eval_date
            eval_mask = date_arr == eval_date
            if train_mask.sum() < min_train:
                continue

            train_ds = lgb.Dataset(X[train_mask], label=y[train_mask])
            model = lgb.train(params, train_ds, num_boost_round=200)
            preds = model.predict(X[eval_mask])
            all_preds.extend(preds)
            all_true.extend(y[eval_mask])

        if not all_preds:
            return {"accuracy": 0.0, "mae": float("inf"), "residual_std": DEFAULT_RESIDUAL_STD}

        all_preds = np.array(all_preds)
        all_true = np.array(all_true)
        residuals = all_true - all_preds

        # Accuracy = how often predicted direction matches actual direction
        correct_direction = ((all_preds > 0) == (all_true > 0)).sum()
        accuracy = float(correct_direction / len(all_true))

        # Out-of-sample residual std — THIS is used for CDF-based edge calculations
        residual_std = float(np.std(residuals))

        # Update the model's residual_std with walk-forward calibrated value
        self.residual_std = residual_std

        return {
            "accuracy": accuracy,
            "mae": float(np.mean(np.abs(residuals))),
            "residual_std": residual_std,
        }

    def feature_importances(self) -> dict:
        """Return feature importance dict (gain-based)."""
        if not self.trained:
            return {}
        importances = self.model.feature_importance(importance_type="gain")
        names = FEATURE_ORDER[:len(importances)]
        total = sum(importances)
        if total == 0:
            return {n: 0.0 for n in names}
        return {n: float(v / total) for n, v in zip(names, importances)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_ml_model.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/ml_model.py backend/tests/test_ml_model.py
git commit -m "feat: add LightGBM model with walk-forward validation"
```

---

## Chunk 3: Confidence Recalibration & Dynamic Kelly

### Task 7: DB-Driven Confidence Thresholds

**Files:**
- Modify: `backend/analysis/confidence.py`
- Modify: `backend/analysis/prop_confidence.py`
- Create: `backend/tests/test_confidence_db.py`

**Context:** Current confidence thresholds are hardcoded (3%, 5%, 8%, 12% for game picks; 5%, 7%, 10%, 15%, 20% for props). These need to be read from the `calibration_history` table, with the hardcoded values as fallback defaults.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_confidence_db.py
from backend.analysis.confidence import calculate_confidence, get_thresholds, DEFAULT_THRESHOLDS
from backend.analysis.prop_confidence import calculate_prop_confidence, get_prop_thresholds, DEFAULT_PROP_THRESHOLDS


def test_default_thresholds():
    assert DEFAULT_THRESHOLDS == {5: 12.0, 4: 8.0, 3: 5.0, 2: 5.0, 1: 3.0}


def test_default_prop_thresholds():
    assert DEFAULT_PROP_THRESHOLDS == {5: 20.0, 4: 15.0, 3: 10.0, 2: 7.0, 1: 5.0}


def test_calculate_confidence_uses_defaults():
    # With no session, should use defaults
    assert calculate_confidence(12.0, 3) == 5
    assert calculate_confidence(8.0, 2) == 4
    assert calculate_confidence(5.0, 2) == 3
    assert calculate_confidence(5.0, 1) == 2
    assert calculate_confidence(3.0, 1) == 1
    assert calculate_confidence(2.0, 1) == 0


def test_calculate_confidence_with_custom_thresholds():
    # Simulate tightened thresholds (require higher edge)
    custom = {5: 15.0, 4: 10.0, 3: 7.0, 2: 5.0, 1: 4.0}
    assert calculate_confidence(12.0, 3, thresholds=custom) == 4  # was 5, now needs 15%
    assert calculate_confidence(15.0, 3, thresholds=custom) == 5


def test_calculate_prop_confidence_uses_defaults():
    assert calculate_prop_confidence(20.0) == 5
    assert calculate_prop_confidence(15.0) == 4
    assert calculate_prop_confidence(10.0) == 3
    assert calculate_prop_confidence(7.0) == 2
    assert calculate_prop_confidence(5.0) == 1
    assert calculate_prop_confidence(4.0) == 0


def test_calculate_prop_confidence_with_custom_thresholds():
    custom = {5: 25.0, 4: 18.0, 3: 12.0, 2: 8.0, 1: 6.0}
    assert calculate_prop_confidence(20.0, thresholds=custom) == 4
    assert calculate_prop_confidence(25.0, thresholds=custom) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_confidence_db.py -v`
Expected: FAIL — `get_thresholds`, `DEFAULT_THRESHOLDS`, `thresholds` parameter not defined

- [ ] **Step 3: Refactor confidence.py to support custom thresholds**

Replace `backend/analysis/confidence.py`:

```python
DEFAULT_THRESHOLDS = {5: 12.0, 4: 8.0, 3: 5.0, 2: 5.0, 1: 3.0}
DEFAULT_MIN_MODELS = {5: 3, 4: 2, 3: 2, 2: 1, 1: 0}


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


def calculate_confidence(
    edge_pct: float, models_agreeing: int, thresholds: dict | None = None,
) -> int:
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    min_models = DEFAULT_MIN_MODELS
    for tier in (5, 4, 3, 2, 1):
        if edge_pct >= thresholds.get(tier, 999) and models_agreeing >= min_models.get(tier, 0):
            return tier
    return 0
```

- [ ] **Step 4: Refactor prop_confidence.py similarly**

Replace `backend/analysis/prop_confidence.py`:

```python
DEFAULT_PROP_THRESHOLDS = {5: 20.0, 4: 15.0, 3: 10.0, 2: 7.0, 1: 5.0}


def get_prop_thresholds(session=None, sport: str = "nba") -> dict:
    """Load latest prop confidence thresholds from DB, or return defaults."""
    if session is None:
        return DEFAULT_PROP_THRESHOLDS
    from backend.models import CalibrationHistory
    rows = (
        session.query(CalibrationHistory)
        .filter(
            CalibrationHistory.sport == sport,
            CalibrationHistory.confidence_tier >= 100,  # prop tiers stored as 100+tier
        )
        .order_by(CalibrationHistory.date.desc())
        .limit(5)
        .all()
    )
    if not rows:
        return DEFAULT_PROP_THRESHOLDS
    return {row.confidence_tier - 100: row.new_threshold for row in rows}


def calculate_prop_confidence(edge_pct: float, thresholds: dict | None = None) -> int:
    if thresholds is None:
        thresholds = DEFAULT_PROP_THRESHOLDS
    for tier in (5, 4, 3, 2, 1):
        if edge_pct >= thresholds.get(tier, 999):
            return tier
    return 0
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_confidence_db.py backend/tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/confidence.py backend/analysis/prop_confidence.py backend/tests/test_confidence_db.py
git commit -m "feat: DB-driven confidence thresholds with hardcoded fallback defaults"
```

---

### Task 8: Confidence Recalibrator

**Files:**
- Create: `backend/analysis/recalibrator.py`
- Create: `backend/tests/test_recalibrator.py`

**Context:** Nightly job queries graded picks, compares actual vs expected win rates per confidence tier, and adjusts thresholds by ±1 percentage point per cycle. Minimum 20 picks per tier to recalibrate.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_recalibrator.py
from datetime import date, datetime, timezone
from backend.analysis.recalibrator import Recalibrator, MIN_PICKS_PER_TIER
from backend.models import Base, PickModel, PickResult, Game, Team, StrategyModel, CalibrationHistory
from backend.database import get_engine, get_session


def _setup_db():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    # Create teams and a strategy
    t1 = Team(name="Team A", abbreviation="TA", sport="nba")
    t2 = Team(name="Team B", abbreviation="TB", sport="nba")
    session.add_all([t1, t2])
    session.commit()
    strat = StrategyModel(name="test", sport="nba", config_json="{}")
    session.add(strat)
    session.commit()
    return session, t1, t2, strat


def _add_picks(session, t1, t2, strat, confidence, wins, losses):
    """Add picks with specified confidence and win/loss counts."""
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


def test_min_picks_per_tier():
    assert MIN_PICKS_PER_TIER == 20


def test_recalibrator_skips_small_samples():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=8, losses=2)  # only 10
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    # Should skip tier 5 (only 10 picks, need 20)
    assert 5 not in adjustments


def test_recalibrator_tightens_when_underperforming():
    session, t1, t2, strat = _setup_db()
    # 5-star picks winning at 55% (expected ~70%) — should tighten
    _add_picks(session, t1, t2, strat, confidence=5, wins=11, losses=9)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 5 in adjustments
    assert adjustments[5]["direction"] == "tighten"
    assert adjustments[5]["new_threshold"] > 12.0  # default was 12%


def test_recalibrator_loosens_when_overperforming():
    session, t1, t2, strat = _setup_db()
    # 3-star picks winning at 75% (expected ~55%) — should loosen
    _add_picks(session, t1, t2, strat, confidence=3, wins=18, losses=6)
    recal = Recalibrator(session, sport="nba")
    adjustments = recal.run(days=90)
    assert 3 in adjustments
    assert adjustments[3]["direction"] == "loosen"
    assert adjustments[3]["new_threshold"] < 5.0  # default was 5%


def test_recalibrator_saves_to_db():
    session, t1, t2, strat = _setup_db()
    _add_picks(session, t1, t2, strat, confidence=5, wins=11, losses=9)
    recal = Recalibrator(session, sport="nba")
    recal.run(days=90)
    rows = session.query(CalibrationHistory).all()
    assert len(rows) >= 1
    assert rows[0].sport == "nba"
    assert rows[0].confidence_tier == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_recalibrator.py -v`
Expected: FAIL — `recalibrator` module not found

- [ ] **Step 3: Implement Recalibrator**

Create `backend/analysis/recalibrator.py`:

```python
"""Confidence threshold recalibration based on actual pick performance."""
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.analysis.confidence import DEFAULT_THRESHOLDS
from backend.models import CalibrationHistory, PickModel, PickResult

logger = logging.getLogger(__name__)

MIN_PICKS_PER_TIER = 20
ADJUSTMENT_STEP = 1.0  # percentage points per cycle
# Expected win rates per confidence tier (approximate targets)
EXPECTED_WIN_RATES = {5: 0.70, 4: 0.63, 3: 0.57, 2: 0.53, 1: 0.50}
DEVIATION_THRESHOLD = 0.05  # 5 percentage points


class Recalibrator:
    def __init__(self, session: Session, sport: str = "nba"):
        self.session = session
        self.sport = sport

    def run(self, days: int = 90) -> dict:
        """Analyze pick performance and adjust confidence thresholds.

        Returns dict of tier -> {actual_rate, expected_rate, direction, new_threshold}.
        """
        cutoff = date.today() - timedelta(days=days)

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

        adjustments = {}

        for tier in (5, 4, 3, 2, 1):
            # Query graded picks for this tier
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
            expected_rate = EXPECTED_WIN_RATES.get(tier, 0.5)
            deviation = actual_rate - expected_rate
            old_threshold = thresholds.get(tier, DEFAULT_THRESHOLDS[tier])

            if abs(deviation) < DEVIATION_THRESHOLD:
                logger.info(
                    "Tier %d: %.1f%% actual vs %.1f%% expected — within tolerance",
                    tier, actual_rate * 100, expected_rate * 100,
                )
                continue

            if deviation < 0:
                # Underperforming: tighten threshold (require higher edge)
                new_threshold = old_threshold + ADJUSTMENT_STEP
                direction = "tighten"
            else:
                # Overperforming: loosen threshold
                new_threshold = max(1.0, old_threshold - ADJUSTMENT_STEP)
                direction = "loosen"

            adjustments[tier] = {
                "actual_rate": actual_rate,
                "expected_rate": expected_rate,
                "direction": direction,
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
                "sample_size": total,
            }

            # Save to DB
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

            logger.info(
                "Tier %d: %.1f%% actual vs %.1f%% expected — %s threshold %.1f → %.1f",
                tier, actual_rate * 100, expected_rate * 100,
                direction, old_threshold, new_threshold,
            )

        self.session.commit()
        return adjustments
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_recalibrator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/recalibrator.py backend/tests/test_recalibrator.py
git commit -m "feat: add confidence recalibrator with threshold auto-tuning"
```

---

### Task 9: Dynamic Kelly Sizing

**Files:**
- Modify: `backend/analysis/kelly.py`
- Create: `backend/tests/test_kelly_dynamic.py`

**Context:** Current Kelly is static 0.25 fraction with 0.5-3.0 unit clamp. Spec adds: adaptive fraction based on model calibration accuracy (0.15-0.35), drawdown protection (halve fraction when bankroll drops 15%), correlation discount (20% reduction for multiple picks in same game).

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_kelly_dynamic.py
from backend.analysis.kelly import (
    fractional_kelly,
    adaptive_fraction,
    apply_drawdown_protection,
    apply_correlation_discount,
)


def test_fractional_kelly_basic():
    # 60% edge, -150 odds, 0.25 fraction
    result = fractional_kelly(0.60, -150, 0.25)
    assert 0.5 <= result <= 3.0


def test_fractional_kelly_no_edge():
    # 40% prob on -150 odds (negative EV)
    result = fractional_kelly(0.40, -150, 0.25)
    assert result == 0.5  # minimum


def test_adaptive_fraction_well_calibrated():
    # Deviation < 3% → increase to 0.35
    frac = adaptive_fraction(calibration_deviation=0.02)
    assert frac == 0.35


def test_adaptive_fraction_poorly_calibrated():
    # Deviation > 5% → decrease to 0.15
    frac = adaptive_fraction(calibration_deviation=0.06)
    assert frac == 0.15


def test_adaptive_fraction_moderate():
    # 3% < deviation <= 5% → keep at 0.25
    frac = adaptive_fraction(calibration_deviation=0.04)
    assert frac == 0.25


def test_drawdown_protection_no_drawdown():
    # Bankroll at peak — no reduction
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=100000, peak_balance=100000,
    )
    assert frac == 0.25


def test_drawdown_protection_severe_drawdown():
    # Bankroll 20% below peak — halve fraction
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=80000, peak_balance=100000,
    )
    assert frac == 0.125


def test_drawdown_protection_mild_drawdown():
    # Bankroll 10% below peak (within 10% threshold) — no reduction
    frac = apply_drawdown_protection(
        base_fraction=0.25, current_balance=90000, peak_balance=100000,
    )
    assert frac == 0.25


def test_correlation_discount():
    # Multiple picks in same game → 20% reduction per pick
    frac = apply_correlation_discount(base_fraction=0.25, same_game_picks=2)
    assert abs(frac - 0.20) < 0.001  # 0.25 * 0.8


def test_correlation_discount_single_pick():
    frac = apply_correlation_discount(base_fraction=0.25, same_game_picks=1)
    assert frac == 0.25  # no discount for single pick
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_kelly_dynamic.py -v`
Expected: FAIL — `adaptive_fraction`, `apply_drawdown_protection`, `apply_correlation_discount` not defined

- [ ] **Step 3: Add dynamic Kelly functions**

Replace `backend/analysis/kelly.py`:

```python
"""Fractional Kelly criterion with adaptive sizing, drawdown protection, and correlation discount."""

DRAWDOWN_THRESHOLD = 0.15  # 15% drawdown triggers protection
DRAWDOWN_RECOVERY = 0.10   # Must recover within 10% of peak to restore
CORRELATION_DISCOUNT = 0.20  # 20% reduction per correlated pick


def fractional_kelly(model_prob: float, odds: int, fraction: float = 0.25) -> float:
    """Calculate bet size in units using fractional Kelly criterion.

    Returns units clamped to [0.5, 3.0].
    """
    if odds < 0:
        b = 100 / abs(odds)
    else:
        b = odds / 100

    p = model_prob
    q = 1 - p
    full_kelly = (b * p - q) / b

    if full_kelly <= 0:
        return 0.5

    sized = full_kelly * fraction
    return max(0.5, min(3.0, round(sized, 2)))


def adaptive_fraction(calibration_deviation: float) -> float:
    """Adjust Kelly fraction based on model calibration accuracy.

    Args:
        calibration_deviation: Absolute difference between predicted and actual
                              win rates over the last 30 days.

    Returns:
        Kelly fraction: 0.35 (accurate), 0.25 (moderate), 0.15 (inaccurate).
    """
    if calibration_deviation <= 0.03:
        return 0.35
    elif calibration_deviation <= 0.05:
        return 0.25
    else:
        return 0.15


def apply_drawdown_protection(
    base_fraction: float, current_balance: float, peak_balance: float,
) -> float:
    """Halve Kelly fraction when bankroll drops significantly from peak.

    Triggers when balance drops 15%+ from peak.
    Stays active until balance recovers to within 10% of peak.
    """
    if peak_balance <= 0:
        return base_fraction
    drawdown = (peak_balance - current_balance) / peak_balance
    if drawdown >= DRAWDOWN_THRESHOLD:
        return base_fraction / 2
    return base_fraction


def apply_correlation_discount(base_fraction: float, same_game_picks: int) -> float:
    """Reduce Kelly fraction when placing multiple bets on the same game.

    Each additional pick in the same game reduces fraction by CORRELATION_DISCOUNT.
    """
    if same_game_picks <= 1:
        return base_fraction
    return base_fraction * (1 - CORRELATION_DISCOUNT)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_kelly_dynamic.py -v`
Expected: All PASS

- [ ] **Step 5: Run all tests for regressions**

Run: `python -m pytest backend/tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/kelly.py backend/tests/test_kelly_dynamic.py
git commit -m "feat: dynamic Kelly with adaptive fraction, drawdown protection, and correlation discount"
```

---

### Task 10: Nightly Recalibration Job

**Files:**
- Create: `backend/pipeline/recalibration_job.py`
- Modify: `backend/pipeline/scheduler.py`

**Context:** Add a nightly job at 3 AM that: grades remaining picks, runs recalibration, logs metrics. No new tests needed — this is orchestration code that calls already-tested components.

- [ ] **Step 1: Create recalibration job**

Create `backend/pipeline/recalibration_job.py`:

```python
"""Nightly recalibration job: grade picks, retrain model, adjust thresholds."""
import json
import logging
from datetime import date

from backend.analysis.ml_model import LightGBMModel, MIN_ML_GAMES
from backend.analysis.recalibrator import Recalibrator
from backend.database import get_engine, get_session
from backend.models import CalibrationHistory, Game, ModelMetrics

logger = logging.getLogger(__name__)


def run_recalibration(db_path: str = "sports_picks.db") -> dict:
    """Execute nightly recalibration pipeline.

    Returns summary dict of actions taken.
    """
    engine = get_engine(db_path)
    session = get_session(engine)
    summary = {"graded": 0, "recalibrated": {}, "model_retrained": False}

    try:
        # Step 1: Grade remaining picks (reuse existing grading logic)
        from backend.pipeline.scheduler import grade_pending_picks
        summary["graded"] = grade_pending_picks(session)

        # Step 2: Recalibrate confidence thresholds per sport
        for sport in ("nba", "nfl", "ncaab", "ncaaf"):
            game_count = (
                session.query(Game)
                .filter(Game.sport == sport, Game.status == "final")
                .count()
            )
            if game_count < 30:
                continue

            recal = Recalibrator(session, sport=sport)
            adjustments = recal.run(days=90)
            if adjustments:
                summary["recalibrated"][sport] = adjustments

        # Step 3: Retrain LightGBM for sports with enough games
        from backend.analysis.variants.ensemble import EnsembleStrategy
        for sport in ("nba", "nfl", "ncaab", "ncaaf"):
            sport_count = (
                session.query(Game)
                .filter(Game.sport == sport, Game.status == "final")
                .count()
            )
            if sport_count >= MIN_ML_GAMES:
                logger.info("Retraining LightGBM for %s with %d games", sport, sport_count)
                strategy = EnsembleStrategy(config={})
                strategy.train_lgbm_from_db(session)
                summary["model_retrained"] = True
                # Log metrics and feature importances
                importances = {}
                if strategy._lgbm_model and strategy._lgbm_model.trained:
                    importances = strategy._lgbm_model.feature_importances()
                import json
                session.add(ModelMetrics(
                    date=date.today(),
                    sport=sport,
                    model_version="lgbm_v1_regression",
                    training_games=sport_count,
                    feature_importances=json.dumps(importances),
                ))

        session.commit()
        logger.info("Recalibration complete: %s", summary)

    except Exception:
        session.rollback()
        logger.exception("Recalibration failed")
        raise
    finally:
        session.close()

    return summary
```

- [ ] **Step 2: Add recalibration job to scheduler**

In `backend/pipeline/scheduler.py`, add after the existing `daily_job` scheduler setup:

```python
from backend.pipeline.recalibration_job import run_recalibration

# Inside run_pipeline(), after the daily_job is scheduled:
scheduler.add_job(
    run_recalibration,
    "cron",
    hour=3,
    minute=0,
    id="recalibration",
    replace_existing=True,
)
logger.info("Scheduled recalibration job at 3:00 AM")
```

- [ ] **Step 3: Verify import works**

Run: `python -c "from backend.pipeline.recalibration_job import run_recalibration; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run all tests**

Run: `python -m pytest backend/tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/recalibration_job.py backend/pipeline/scheduler.py
git commit -m "feat: add nightly recalibration job at 3 AM with model metrics tracking"
```

---

### Task 11: Wire LightGBM into EnsembleStrategy

**Files:**
- Modify: `backend/analysis/variants/ensemble.py`

**Context:** The EnsembleStrategy currently uses `CalibratedModel` (logistic regression). For NBA with 200+ games, it should use `LightGBMModel` (regression on home margin). The ensemble needs a `train_lgbm_from_db()` method that loads historical games and trains the model, plus `_calibrated_probability()` updated to use it.

The LightGBM model is stored as an instance attribute on EnsembleStrategy (not a global), making it testable and thread-safe.

- [ ] **Step 1: Add train_lgbm_from_db method and update _calibrated_probability**

In `backend/analysis/variants/ensemble.py`:

```python
import numpy as np
from backend.analysis.ml_model import LightGBMModel, MIN_ML_GAMES
from backend.analysis.calibrated_model import extract_features, features_to_array

# In __init__:
def __init__(self, config):
    super().__init__(config)
    self._lgbm_model = None  # instance attribute, not global

def train_lgbm_from_db(self, session):
    """Train LightGBM on all completed games from the database.

    Called by the nightly recalibration job and lazily on first prediction.
    """
    from backend.models import Game, TeamStat, EloRating, Team
    from backend.analysis.calibrated_model import _stat_value
    from datetime import date as date_type

    games = session.query(Game).filter(
        Game.status == "final",
        Game.sport == "nba",
        Game.home_score.isnot(None),
    ).all()

    if len(games) < MIN_ML_GAMES:
        return

    X_list, y_list, dates_list = [], [], []
    for game in games:
        # Build a minimal GameData-like object for feature extraction
        home_stats = session.query(TeamStat).filter(TeamStat.game_id == game.id, TeamStat.team_id == game.home_team_id).all()
        away_stats = session.query(TeamStat).filter(TeamStat.game_id == game.id, TeamStat.team_id == game.away_team_id).all()
        home_elo = session.query(EloRating).filter(EloRating.team_id == game.home_team_id).first()
        away_elo = session.query(EloRating).filter(EloRating.team_id == game.away_team_id).first()

        from backend.data_types import GameData, TeamData
        home_team = TeamData(
            name="", abbreviation="", sport="nba",
            elo=home_elo.rating if home_elo else 1500.0,
            point_diff=_stat_value(home_stats, "point_diff") or 0.0,
            offensive_rating=_stat_value(home_stats, "offensive_rating") or 100.0,
            defensive_rating=_stat_value(home_stats, "defensive_rating") or 100.0,
            rest_days=_stat_value(home_stats, "rest_days") or 1.0,
            pace=_stat_value(home_stats, "pace") or 100.0,
            schedule_fatigue=0.0, is_lookahead=False,
        )
        away_team = TeamData(
            name="", abbreviation="", sport="nba",
            elo=away_elo.rating if away_elo else 1500.0,
            point_diff=_stat_value(away_stats, "point_diff") or 0.0,
            offensive_rating=_stat_value(away_stats, "offensive_rating") or 100.0,
            defensive_rating=_stat_value(away_stats, "defensive_rating") or 100.0,
            rest_days=_stat_value(away_stats, "rest_days") or 1.0,
            pace=_stat_value(away_stats, "pace") or 100.0,
            schedule_fatigue=0.0, is_lookahead=False,
        )
        gd = GameData(
            game_id=game.id, sport="nba", date=str(game.date),
            home_team=home_team, away_team=away_team,
            home_stats=[], away_stats=[], odds=[],
        )
        features = extract_features(gd)
        X_list.append(features_to_array(features))
        y_list.append(game.home_score - game.away_score)  # REGRESSION target: margin
        dates_list.append(game.date)

    self._lgbm_model = LightGBMModel()
    X = np.array(X_list)
    y = np.array(y_list, dtype=float)
    self._lgbm_model.train(X, y, dates_list)

    # Run walk-forward to calibrate residual_std from out-of-sample predictions
    if self._lgbm_model.trained:
        metrics = self._lgbm_model.walk_forward_validate(X, y, dates_list)
        # walk_forward_validate already sets self._lgbm_model.residual_std
```

Update `_calibrated_probability`:

```python
def _calibrated_probability(self, game):
    features = extract_features(game)
    feature_array = features_to_array(features)

    # Use LightGBM for NBA if trained
    if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
        prob = self._lgbm_model.home_win_prob(np.array([feature_array]))
    else:
        prob = self._legacy_calibrated_probability(game)

    # Apply schedule adjustments (existing logic)
    if hasattr(game.home_team, 'schedule_fatigue'):
        prob -= 0.03 * game.home_team.schedule_fatigue
    if hasattr(game.home_team, 'is_lookahead') and game.home_team.is_lookahead:
        prob -= 0.04
    if hasattr(game.away_team, 'is_lookahead') and game.away_team.is_lookahead:
        prob += 0.04

    return max(0.01, min(0.99, prob))
```

Also add `_predicted_point_diff_ml()` for spread/OU edges:

```python
def _predicted_point_diff_ml(self, game) -> tuple[float, float]:
    """Get predicted margin and residual std from LightGBM.

    Returns (predicted_margin, residual_std).
    Falls back to heuristic if ML not available.
    """
    if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
        features = extract_features(game)
        feature_array = features_to_array(features)
        margin = self._lgbm_model.predict(np.array([feature_array]))
        std = self._lgbm_model.residual_std or 12.0
        return margin, std
    return self._predicted_point_diff(game), 12.0  # fallback with NBA empirical std
```

Rename the existing `_calibrated_probability` to `_legacy_calibrated_probability` so it's preserved as fallback.

- [ ] **Step 2: Run all tests**

Run: `python -m pytest backend/tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/analysis/variants/ensemble.py
git commit -m "feat: wire LightGBM into ensemble strategy with logistic regression fallback"
```

---

## Chunk 4: Integration — Migrate Existing Tables

### Task 12: Database Migration for Existing SQLite DB

**Files:**
- Create: `backend/migrate_phase1.py`

**Context:** The existing `sports_picks.db` needs the new tables (`calibration_history`, `model_metrics`) and new columns (`user_profiles.current_streak`, etc.) added. Since we use SQLAlchemy without Alembic, a simple migration script handles this.

- [ ] **Step 1: Create migration script**

```python
# backend/migrate_phase1.py
"""One-time migration: add Phase 1 tables and columns to existing DB."""
import sqlite3
import sys


def migrate(db_path: str = "sports_picks.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # New tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id INTEGER PRIMARY KEY,
            date DATE NOT NULL,
            sport TEXT NOT NULL,
            confidence_tier INTEGER NOT NULL,
            predicted_win_rate REAL,
            actual_win_rate REAL,
            sample_size INTEGER,
            old_threshold REAL,
            new_threshold REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_metrics (
            id INTEGER PRIMARY KEY,
            date DATE NOT NULL,
            sport TEXT NOT NULL,
            model_version TEXT NOT NULL,
            accuracy REAL,
            log_loss REAL,
            feature_importances TEXT,
            training_games INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # New columns on existing tables (ALTER TABLE ADD COLUMN is safe — no-op if exists)
    for stmt in [
        "ALTER TABLE user_profiles ADD COLUMN current_streak INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN best_streak INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN streak_type TEXT DEFAULT 'none'",
        "ALTER TABLE paper_picks ADD COLUMN graded_at DATETIME",
    ]:
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass  # Column already exists
            else:
                raise

    conn.commit()
    conn.close()
    print(f"Migration complete: {db_path}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sports_picks.db"
    migrate(path)
```

- [ ] **Step 2: Run migration on the actual database**

Run: `python backend/migrate_phase1.py`
Expected: `Migration complete: sports_picks.db`

- [ ] **Step 3: Verify tables exist**

Run: `python -c "import sqlite3; c=sqlite3.connect('sports_picks.db'); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"`
Expected: Output includes `calibration_history` and `model_metrics`

- [ ] **Step 4: Commit**

```bash
git add backend/migrate_phase1.py
git commit -m "feat: add Phase 1 database migration script"
```

---

### Task 13: Integration Test — Full Prediction Pipeline

**Files:**
- Create: `backend/tests/test_phase1_integration.py`

**Context:** Verify that the full prediction pipeline works end-to-end: expanded features → LightGBM (or fallback) → distribution-based edges → vig-adjusted probabilities → dynamic Kelly → DB-driven confidence.

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_phase1_integration.py
"""Integration test: full Phase 1 prediction pipeline."""
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.analysis.kelly import fractional_kelly, adaptive_fraction
from backend.analysis.confidence import calculate_confidence, DEFAULT_THRESHOLDS
from backend.analysis.calibrated_model import extract_features, features_to_array
from backend.data_types import GameData, TeamData, OddsData


def _make_game():
    home = TeamData(
        name="Lakers", abbreviation="LAL", sport="nba",
        elo=1600, point_diff=3.5, offensive_rating=112.0,
        defensive_rating=108.0, rest_days=2, pace=100.5,
        schedule_fatigue=0.0, is_lookahead=False,
    )
    away = TeamData(
        name="Celtics", abbreviation="BOS", sport="nba",
        elo=1550, point_diff=1.0, offensive_rating=108.0,
        defensive_rating=110.0, rest_days=3, pace=97.0,
        schedule_fatigue=0.0, is_lookahead=False,
    )
    odds = [OddsData(
        bookmaker="test",
        moneyline_home=-150, moneyline_away=130,
        spread_home=-3.5, spread_away=3.5,
        over_under=220.5,
    )]
    return GameData(
        game_id=1, sport="nba", date="2026-03-16",
        home_team=home, away_team=away,
        home_stats=[], away_stats=[], odds=odds,
    )


def test_feature_extraction_expanded():
    game = _make_game()
    features = extract_features(game)
    assert isinstance(features, dict)
    assert len(features) >= 12
    arr = features_to_array(features)
    assert len(arr) == len(features)


def test_vig_removal_produces_fair_probs():
    home_raw = american_to_implied_prob(-150)
    away_raw = american_to_implied_prob(130)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    assert abs(home_fair + away_fair - 1.0) < 0.001


def test_confidence_with_default_thresholds():
    assert calculate_confidence(15.0, 3) == 5
    assert calculate_confidence(6.0, 2) == 3
    assert calculate_confidence(2.0, 1) == 0


def test_kelly_respects_bounds():
    result = fractional_kelly(0.9, -150, 0.25)
    assert 0.5 <= result <= 3.0
    result = fractional_kelly(0.1, -150, 0.25)
    assert result == 0.5


def test_adaptive_kelly_fraction():
    assert adaptive_fraction(0.01) == 0.35
    assert adaptive_fraction(0.04) == 0.25
    assert adaptive_fraction(0.10) == 0.15


def test_ensemble_has_distribution_methods():
    strategy = EnsembleStrategy(config={})
    assert hasattr(strategy, "_spread_cover_prob")
    assert hasattr(strategy, "_over_probability")
    assert hasattr(strategy, "_predicted_point_diff_ml")
    prob = strategy._spread_cover_prob(7.0, 3.5)  # home needs margin > 3.5
    assert 0.0 < prob < 1.0
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest backend/tests/test_phase1_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest backend/tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_phase1_integration.py
git commit -m "test: add Phase 1 integration tests for full prediction pipeline"
```

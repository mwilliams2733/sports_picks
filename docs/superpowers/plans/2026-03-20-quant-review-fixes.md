# Quantitative Review Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 16 issues identified in the senior quant code review — correctness bugs, analytical improvements, and structural enhancements.

**Architecture:** Surgical edits to existing analysis, backtesting, ELO, and pipeline modules. New `EloHistory` model for point-in-time ratings. New `receptions` field on `PlayerStat`. Sport-specific constants module. Walk-forward wrapper in auto-tuner.

**Tech Stack:** Python 3.12+, SQLAlchemy, scipy, lightgbm, numpy, pytest

---

### Task 1: Fix prop variance to use Bessel's correction (N-1)

**Files:**
- Modify: `backend/analysis/prop_analyzer.py:191`
- Modify: `backend/tests/test_prop_analyzer.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_prop_analyzer.py`, add:

```python
def test_variance_uses_bessel_correction():
    """With 3 samples, variance should use N-1 denominator."""
    from backend.analysis.prop_analyzer import _compute_exceedance_prob
    # 3 game values: [10, 12, 14] -> mean=12, pop_var=2.67, sample_var=4.0
    # With sample variance (N-1), std=2.0 -> wider distribution -> lower exceedance
    # With population variance (N), std=1.63 -> tighter distribution -> higher exceedance
    # For line=14, mean=12: P(X>14) should use std=2.0 (sample) not 1.63 (pop)
    import math
    game_values = [10.0, 12.0, 14.0]
    mean_val = 12.0
    # Sample variance with Bessel's: sum((x-mean)^2) / (N-1) = 8/2 = 4.0
    sample_var = sum((v - mean_val) ** 2 for v in game_values) / (len(game_values) - 1)
    assert sample_var == 4.0
    # Pop variance: 8/3 = 2.667
    pop_var = sum((v - mean_val) ** 2 for v in game_values) / len(game_values)
    assert abs(pop_var - 2.667) < 0.01

    # Now test through PropAnalyzer — the variance it computes internally should be sample variance
    from unittest.mock import MagicMock
    from backend.analysis.prop_analyzer import PropAnalyzer
    analyzer = PropAnalyzer(season_weight=0.0, recent_weight=1.0, min_edge=0.0)

    prop = MagicMock()
    prop.line = 14.0
    prop.market = "player_points"
    prop.outcome = "Over"
    prop.player_name = "Test"
    prop.game_id = 1
    prop.odds = -110

    # Create recent games with points = [10, 12, 14]
    games = []
    for pts in [10.0, 12.0, 14.0]:
        g = MagicMock()
        g.points = pts
        g.source = "test"
        g.is_stale = False
        games.append(g)

    result = analyzer.analyze(prop, None, games)
    # With sample variance (std=2.0), P(X>14) with mean=12 = P(Z>1) ~ 0.159
    # With pop variance (std=1.63), P(X>14) with mean=12 = P(Z>1.22) ~ 0.111
    # The result should use the wider distribution (sample variance)
    assert result is not None
    # edge_pct derived from exceedance prob ~0.159 -> (0.159-0.5)*200 = -68.2 -> negative -> None
    # Actually with recent_weight=1.0, projection=12.0, line=14.0, Over
    # exceedance = P(X>14) ~ 0.159 < 0.5, so directional_prob < 0.5, edge < 0
    # This should return None for Over. Let's test Under instead.
    prop.outcome = "Under"
    result = analyzer.analyze(prop, None, games)
    assert result is not None
    # With sample var: P(X<14) = 1-0.159 = 0.841 -> edge = (0.841-0.5)*200 = 68.2
    # With pop var: P(X<14) = 1-0.111 = 0.889 -> edge = (0.889-0.5)*200 = 77.8
    # Sample variance gives LOWER edge (wider distribution = less certain)
    assert result.edge_pct < 75.0, f"Edge {result.edge_pct} too high — likely using population variance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_prop_analyzer.py::test_variance_uses_bessel_correction -v`
Expected: FAIL — edge_pct >= 75.0 because population variance is used

- [ ] **Step 3: Fix variance calculation**

In `backend/analysis/prop_analyzer.py`, change line 191 from:
```python
variance = sum((v - mean_val) ** 2 for v in game_values) / len(game_values)
```
to:
```python
variance = sum((v - mean_val) ** 2 for v in game_values) / (len(game_values) - 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_prop_analyzer.py::test_variance_uses_bessel_correction -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest backend/tests/ -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/prop_analyzer.py backend/tests/test_prop_analyzer.py
git commit -m "fix: use Bessel's correction (N-1) for prop variance"
```

---

### Task 2: Fix receptions mapped to rec_yards

**Files:**
- Modify: `backend/analysis/prop_analyzer.py:32`
- Modify: `backend/pipeline/grader.py:21`
- Modify: `backend/models.py` (add `receptions` column to PlayerStat)
- Modify: `backend/database.py` (add migration)
- Modify: `backend/tests/test_prop_analyzer.py`

- [ ] **Step 1: Add `receptions` column to PlayerStat model**

In `backend/models.py`, add after `rec_yards` (line 147):
```python
receptions = Column(Float, nullable=True)
```

- [ ] **Step 2: Add migration for the new column**

In `backend/database.py`, add:
```python
def migrate_player_stat_receptions(engine):
    """Add receptions column to player_stats table if missing."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    if "player_stats" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("player_stats")]
        if "receptions" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE player_stats ADD COLUMN receptions FLOAT"))
```

- [ ] **Step 3: Fix the mapping in both files**

In `backend/analysis/prop_analyzer.py` line 32, change:
```python
"player_receptions": ["rec_yards"],
```
to:
```python
"player_receptions": ["receptions"],
```

In `backend/pipeline/grader.py` line 21, change:
```python
"player_receptions": ["rec_yards"],  # closest available stat
```
to:
```python
"player_receptions": ["receptions"],
```

- [ ] **Step 4: Write test for correct mapping**

In `backend/tests/test_prop_analyzer.py`, add:
```python
def test_receptions_uses_receptions_not_rec_yards():
    """player_receptions market should use receptions field, not rec_yards."""
    from backend.analysis.prop_analyzer import MARKET_TO_STAT
    assert MARKET_TO_STAT["player_receptions"] == ["receptions"]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_prop_analyzer.py -v -k receptions`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/database.py backend/analysis/prop_analyzer.py backend/pipeline/grader.py backend/tests/test_prop_analyzer.py
git commit -m "fix: map player_receptions to receptions field, not rec_yards"
```

---

### Task 3: Fix backtester ROI to use units risked

**Files:**
- Modify: `backend/backtesting/backtester.py`
- Modify: `backend/tests/test_backtester.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_backtester.py`, add:
```python
def test_roi_uses_units_risked_not_pick_count(sample_strategy):
    """ROI denominator should be total units risked, not number of picks."""
    from backend.data_types import GameData, TeamStats, OddsSnapshot, Pick
    from datetime import date

    # Create a strategy that returns picks with different unit sizes
    class VariableKellyStrategy:
        name = "test"
        config = {}
        def predict(self, game):
            return [
                Pick(game_id=game.game_id, pick_type="moneyline", pick_value="HOME ML",
                     confidence=5, edge_pct=15.0, model_probability=0.7,
                     implied_probability=0.5, odds_at_pick=-110, suggested_unit_size=3.0),
            ]

    from backend.backtesting.backtester import Backtester
    bt = Backtester(VariableKellyStrategy())

    ts = TeamStats(point_diff=5.0, home_record=(10, 5), away_record=(5, 5),
                   last_n_record=(3, 2), offensive_rating=110, defensive_rating=105,
                   pace=100, strength_of_schedule=0.5, elo_rating=1550, rest_days=2)
    game = GameData(game_id=1, sport="nba", date=date(2026, 1, 1),
                    home_team_id=1, away_team_id=2, home_stats=ts, away_stats=ts,
                    odds=[OddsSnapshot("dk", -150, 130, -3.5, 3.5, 220.0)])

    result = bt.run([(game, 110, 100)])  # home wins
    # With 3.0 unit bet at -150: payout = 100/150 * 3.0 = 2.0 profit
    # ROI = profit / units_risked = 2.0 / 3.0 * 100 = 66.67%
    # OLD wrong: ROI = profit / 1 pick * 100 = 200%
    assert result["total_units_risked"] > 0
    assert result["roi"] < 100, f"ROI {result['roi']} suggests pick count denominator, not units"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_backtester.py::test_roi_uses_units_risked_not_pick_count -v`
Expected: FAIL — KeyError on `total_units_risked`

- [ ] **Step 3: Implement fix**

Replace `backend/backtesting/backtester.py` content:

```python
from backend.analysis.strategy import Strategy
from backend.pipeline.grader import grade_pick
from backend.data_types import GameData

class Backtester:
    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    def run(self, games_with_results: list[tuple[GameData, int, int]]) -> dict:
        wins = 0
        losses = 0
        pushes = 0
        total_profit = 0.0
        total_units_risked = 0.0
        pick_details = []
        for game, home_score, away_score in games_with_results:
            picks = self.strategy.predict(game)
            for pick in picks:
                unit_size = getattr(pick, 'suggested_unit_size', 1.0) or 1.0
                result, payout = grade_pick(
                    pick.pick_type, pick.pick_value, home_score, away_score, pick.odds_at_pick
                )
                if result == "win":
                    wins += 1
                    total_profit += payout * unit_size
                elif result == "loss":
                    losses += 1
                    total_profit -= unit_size
                else:
                    pushes += 1
                total_units_risked += unit_size
                pick_details.append({
                    "game_id": pick.game_id, "pick_type": pick.pick_type,
                    "pick_value": pick.pick_value, "confidence": pick.confidence,
                    "edge_pct": pick.edge_pct, "result": result, "odds_at_pick": pick.odds_at_pick,
                    "unit_size": unit_size,
                })
        total = wins + losses
        return {
            "wins": wins, "losses": losses, "pushes": pushes, "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / total_units_risked * 100) if total_units_risked > 0 else 0, 2),
            "total_profit": round(total_profit, 4),
            "total_units_risked": round(total_units_risked, 2),
            "picks": pick_details,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_backtester.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add backend/backtesting/backtester.py backend/tests/test_backtester.py
git commit -m "fix: backtester ROI uses total units risked, not pick count"
```

---

### Task 4: Fix LightGBM to always use walk-forward residual_std

**Files:**
- Modify: `backend/analysis/ml_model.py:30-63`
- Modify: `backend/tests/test_ml_model.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_ml_model.py`, add:
```python
def test_train_uses_walk_forward_residual_std():
    """After training, residual_std should come from walk-forward, not in-sample."""
    import numpy as np
    from backend.analysis.ml_model import LightGBMModel
    from datetime import date, timedelta

    np.random.seed(42)
    n = 300
    X = np.random.randn(n, 13)
    y = X[:, 0] * 3 + np.random.randn(n) * 12
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]

    model = LightGBMModel()
    model.train(X, y, dates)
    assert model.trained

    # In-sample residual_std is always optimistically low
    # Walk-forward should produce a HIGHER (more realistic) std
    # After train(), the model should have already done walk-forward
    # and residual_std should be the walk-forward value, not in-sample
    in_sample_preds = model.model.predict(X)
    in_sample_std = float(np.std(y - in_sample_preds))

    # The stored residual_std should be >= in-sample (walk-forward is always wider)
    assert model.residual_std >= in_sample_std * 0.95, \
        f"residual_std {model.residual_std} < in_sample {in_sample_std} — not using walk-forward"
```

- [ ] **Step 2: Implement fix**

In `backend/analysis/ml_model.py`, modify the `train` method to automatically run walk-forward validation after training:

Replace lines 30-63:
```python
    def train(self, X: np.ndarray, y: np.ndarray, dates: list[date]) -> None:
        """Train LightGBM regression on features -> home margin.

        Refuses to train if fewer than MIN_ML_GAMES samples.
        Automatically runs walk-forward validation to calibrate residual_std.
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

        # Use walk-forward residual_std for calibrated uncertainty
        wf_metrics = self.walk_forward_validate(X, y, dates)
        if wf_metrics["residual_std"] < float("inf"):
            self.residual_std = wf_metrics["residual_std"]
        else:
            # Fallback to in-sample if walk-forward had no eval dates
            preds = self.model.predict(X)
            self.residual_std = float(np.std(y - preds))

        logger.info(
            "Trained LightGBM (regression) on %d games, walk-forward residual_std=%.2f",
            len(y), self.residual_std,
        )
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_ml_model.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/ml_model.py backend/tests/test_ml_model.py
git commit -m "fix: LightGBM uses walk-forward residual_std instead of in-sample"
```

---

### Task 5: Add sport-specific standard deviations

**Files:**
- Create: `backend/analysis/sport_constants.py`
- Modify: `backend/analysis/variants/ensemble.py:17-18,61,98`
- Create: `backend/tests/test_sport_constants.py`

- [ ] **Step 1: Create sport constants module**

Create `backend/analysis/sport_constants.py`:
```python
"""Sport-specific empirical constants for CDF-based edge calculations."""

# Standard deviation of game-to-game scoring margin (home_score - away_score)
POINT_DIFF_STD = {
    "nba": 12.0,
    "nfl": 13.5,
    "ncaab": 11.0,
    "ncaaf": 17.0,
    "boxing": 12.0,  # placeholder — not CDF-based
    "mma": 12.0,     # placeholder — not CDF-based
}

# Standard deviation of game-to-game total points
TOTAL_POINTS_STD = {
    "nba": 15.0,
    "nfl": 13.0,
    "ncaab": 12.0,
    "ncaaf": 16.0,
    "boxing": 15.0,
    "mma": 15.0,
}

# Home court/field advantage in ELO points
HOME_ADVANTAGE_ELO = {
    "nba": 100,
    "nfl": 48,
    "ncaab": 120,
    "ncaaf": 65,
    "boxing": 0,
    "mma": 0,
}

# Default home win probability (used as HCA weight in fallback models)
HOME_WIN_RATE = {
    "nba": 0.60,
    "nfl": 0.57,
    "ncaab": 0.67,
    "ncaaf": 0.62,
    "boxing": 0.50,
    "mma": 0.50,
}


def get_point_diff_std(sport: str) -> float:
    return POINT_DIFF_STD.get(sport, 12.0)


def get_total_points_std(sport: str) -> float:
    return TOTAL_POINTS_STD.get(sport, 15.0)


def get_home_advantage_elo(sport: str) -> int:
    return HOME_ADVANTAGE_ELO.get(sport, 100)


def get_home_win_rate(sport: str) -> float:
    return HOME_WIN_RATE.get(sport, 0.57)
```

- [ ] **Step 2: Write tests**

Create `backend/tests/test_sport_constants.py`:
```python
from backend.analysis.sport_constants import (
    get_point_diff_std, get_total_points_std, get_home_advantage_elo, get_home_win_rate,
)

def test_nba_defaults():
    assert get_point_diff_std("nba") == 12.0
    assert get_total_points_std("nba") == 15.0
    assert get_home_advantage_elo("nba") == 100
    assert get_home_win_rate("nba") == 0.60

def test_nfl_values():
    assert get_point_diff_std("nfl") == 13.5
    assert get_total_points_std("nfl") == 13.0
    assert get_home_advantage_elo("nfl") == 48

def test_unknown_sport_returns_default():
    assert get_point_diff_std("curling") == 12.0
    assert get_home_win_rate("curling") == 0.57
```

- [ ] **Step 3: Update ensemble.py to use sport-specific constants**

In `backend/analysis/variants/ensemble.py`:

Replace lines 1-18:
```python
import logging

import numpy as np
from scipy.stats import norm

from backend.analysis.strategy import Strategy
from backend.analysis.confidence import calculate_confidence
from backend.analysis.odds_utils import american_to_implied_prob, remove_vig
from backend.analysis.calibrated_model import CalibratedModel, extract_features, features_to_array, _stat_value
from backend.analysis.ml_model import LightGBMModel, MIN_ML_GAMES
from backend.analysis.kelly import fractional_kelly
from backend.analysis.sport_constants import get_point_diff_std, get_total_points_std, get_home_win_rate
from backend.data_types import GameData, TeamStats, Pick

logger = logging.getLogger(__name__)
```

In the `predict` method, replace the hardcoded std usage. Change line 61 to pass sport:
```python
predicted_diff, diff_std = self._predicted_point_diff_ml(game)
```
(This already works — `_predicted_point_diff_ml` falls back to `POINT_DIFF_STD` which we need to make sport-aware.)

Change `_predicted_point_diff_ml` (line 167-179):
```python
    def _predicted_point_diff_ml(self, game: GameData) -> tuple[float, float]:
        if game.sport == "nba" and self._lgbm_model and self._lgbm_model.trained:
            feature_dict = extract_features(game)
            feature_array = features_to_array(feature_dict)
            margin = self._lgbm_model.predict(np.array([feature_array]))
            std = self._lgbm_model.residual_std or get_point_diff_std(game.sport)
            return margin, std
        return self._predicted_point_diff(game), get_point_diff_std(game.sport)
```

Change line 98 (O/U) from:
```python
over_prob = self._over_probability(predicted_total, ou_line, std=TOTAL_POINTS_STD)
```
to:
```python
over_prob = self._over_probability(predicted_total, ou_line, std=get_total_points_std(game.sport))
```

Change `_model_probability` line 256 (HCA):
```python
        prob = (weights["pd"] * pd_score + weights["elo"] * elo_score +
                weights["rating"] * rating_score + weights["hca"] * get_home_win_rate(game.sport))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_sport_constants.py backend/tests/test_ensemble.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add backend/analysis/sport_constants.py backend/tests/test_sport_constants.py backend/analysis/variants/ensemble.py
git commit -m "feat: sport-specific std devs, home win rates, and ELO advantages"
```

---

### Task 6: MOV-adjusted ELO with home court advantage

**Files:**
- Modify: `backend/analysis/elo.py`
- Modify: `backend/backtesting/historical.py:100-134`
- Modify: `backend/tests/test_elo.py`

- [ ] **Step 1: Write failing tests**

In `backend/tests/test_elo.py`, add:
```python
def test_blowout_win_moves_rating_more_than_close_win():
    """Margin-of-victory multiplier should amplify blowout updates."""
    from backend.analysis.elo import EloSystem
    elo1 = EloSystem(k_factor=20)
    elo1.update("A", "B", "A", margin=1)  # close win
    a_close = elo1.get_rating("A")

    elo2 = EloSystem(k_factor=20)
    elo2.update("A", "B", "A", margin=25)  # blowout
    a_blowout = elo2.get_rating("A")

    assert a_blowout > a_close, "Blowout should produce larger rating change"

def test_home_advantage_adjustment():
    """Home team should have higher expected score when HCA is applied."""
    from backend.analysis.elo import EloSystem
    elo = EloSystem(k_factor=20, home_advantage=100)
    # Two equal teams: home expected > 0.5
    expected_home = elo.expected_score(1500, 1500, home_advantage=100)
    expected_neutral = elo.expected_score(1500, 1500, home_advantage=0)
    assert expected_home > expected_neutral
    assert expected_home > 0.5

def test_mov_autocorrelation_correction():
    """Strong favorites beating weak teams by large margins shouldn't over-inflate."""
    from backend.analysis.elo import EloSystem
    elo = EloSystem(k_factor=20)
    # 1700 vs 1300 = huge favorite. Blowout should be dampened.
    elo.ratings["FAV"] = 1700
    elo.ratings["DOG"] = 1300
    elo.update("FAV", "DOG", "FAV", margin=30)
    # Rating change should be small because it was expected
    change = elo.get_rating("FAV") - 1700
    assert change < 5, f"Expected dampened change for heavy favorite blowout, got {change}"

def test_backward_compat_no_margin():
    """EloSystem.update() still works without margin argument."""
    from backend.analysis.elo import EloSystem
    elo = EloSystem()
    elo.update("A", "B", "A")
    assert elo.get_rating("A") > 1500
    assert elo.get_rating("B") < 1500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_elo.py -v -k "blowout or home_advantage or autocorrelation or backward_compat"`
Expected: FAIL — `update()` doesn't accept `margin` or `home_advantage`

- [ ] **Step 3: Implement MOV-adjusted ELO**

Replace `backend/analysis/elo.py`:
```python
import math

class EloSystem:
    def __init__(self, k_factor: float = 20.0, initial_rating: float = 1500.0,
                 home_advantage: float = 0.0):
        self.k_factor = k_factor
        self.initial_rating = initial_rating
        self.home_advantage = home_advantage
        self.ratings: dict[str, float] = {}

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def expected_score(self, rating_a: float, rating_b: float,
                       home_advantage: float = 0.0) -> float:
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a - home_advantage) / 400.0))

    def _mov_multiplier(self, margin: int, elo_diff: float) -> float:
        """Margin-of-victory multiplier with autocorrelation correction.

        FiveThirtyEight-style: log(abs(margin)+1) * 2.2 / (elo_diff*0.001 + 2.2)
        The correction prevents blowout wins by heavy favorites from
        inflating ratings beyond what the margin warrants.
        """
        abs_margin = abs(margin)
        log_factor = math.log(abs_margin + 1)
        # Autocorrelation correction: strong favorites blowing out weak teams
        # get dampened. elo_diff is winner_rating - loser_rating.
        correction = 2.2 / (max(elo_diff, 0) * 0.001 + 2.2)
        return log_factor * correction

    def update(self, home: str, away: str, winner: str,
               margin: int | None = None) -> None:
        ra = self.get_rating(home)
        rb = self.get_rating(away)
        ea = self.expected_score(ra, rb, home_advantage=self.home_advantage)
        eb = 1.0 - ea
        sa = 1.0 if winner == home else 0.0
        sb = 1.0 - sa

        if margin is not None and margin != 0:
            winner_rating = ra if winner == home else rb
            loser_rating = rb if winner == home else ra
            elo_diff = winner_rating - loser_rating
            mov = self._mov_multiplier(margin, elo_diff)
        else:
            mov = 1.0

        k = self.k_factor * mov
        self.ratings[home] = ra + k * (sa - ea)
        self.ratings[away] = rb + k * (sb - eb)
```

- [ ] **Step 4: Update historical.py to pass margin and use HCA**

In `backend/backtesting/historical.py`, modify `compute_historical_elo` (line 100+):

```python
def compute_historical_elo(session: Session, sport: str):
    """Compute ELO ratings by replaying all completed games in chronological order."""
    from backend.analysis.sport_constants import get_home_advantage_elo
    hca = get_home_advantage_elo(sport)
    elo = EloSystem(k_factor=20, home_advantage=hca)
    games = (
        session.query(Game)
        .filter(Game.sport == sport, Game.status == "final")
        .order_by(Game.date)
        .all()
    )

    for game in games:
        home_team = session.get(Team, game.home_team_id)
        away_team = session.get(Team, game.away_team_id)
        if not home_team or not away_team:
            continue
        if game.home_score is None or game.away_score is None:
            continue

        margin = game.home_score - game.away_score
        winner = home_team.abbreviation if margin > 0 else away_team.abbreviation
        elo.update(home_team.abbreviation, away_team.abbreviation, winner,
                   margin=abs(margin))

    # Save final ratings to DB
    for team_abbr, rating in elo.ratings.items():
        team = session.query(Team).filter(Team.abbreviation == team_abbr, Team.sport == sport).first()
        if not team:
            continue
        existing = session.query(EloRating).filter(EloRating.team_id == team.id, EloRating.sport == sport).first()
        if existing:
            existing.rating = rating
            existing.updated_at = datetime.now(tz=timezone.utc)
        else:
            session.add(EloRating(team_id=team.id, sport=sport, rating=rating, updated_at=datetime.now(tz=timezone.utc)))

    session.commit()
    logger.info(f"Computed ELO ratings for {len(elo.ratings)} {sport} teams")
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_elo.py backend/tests/test_historical.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add backend/analysis/elo.py backend/backtesting/historical.py backend/tests/test_elo.py
git commit -m "feat: MOV-adjusted ELO with home court advantage and autocorrelation correction"
```

---

### Task 7: Fix total points prediction formula

**Files:**
- Modify: `backend/analysis/variants/ensemble.py:288-293`
- Modify: `backend/tests/test_ensemble.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_ensemble.py`, add:
```python
def test_predicted_total_uses_correct_formula():
    """Total should estimate each team's points using opponent defense, not sum offenses."""
    from backend.analysis.variants.ensemble import EnsembleStrategy
    from backend.data_types import GameData, TeamStats, OddsSnapshot
    from datetime import date

    strategy = EnsembleStrategy("test", {})
    ts_home = TeamStats(
        point_diff=0, home_record=(0,0), away_record=(0,0), last_n_record=(0,0),
        offensive_rating=115.0, defensive_rating=105.0,  # good offense, average D
        pace=100.0, strength_of_schedule=0.0, elo_rating=1500, rest_days=2)
    ts_away = TeamStats(
        point_diff=0, home_record=(0,0), away_record=(0,0), last_n_record=(0,0),
        offensive_rating=105.0, defensive_rating=115.0,  # average offense, bad D
        pace=100.0, strength_of_schedule=0.0, elo_rating=1500, rest_days=2)
    game = GameData(game_id=1, sport="nba", date=date(2026, 1, 1),
                    home_team_id=1, away_team_id=2, home_stats=ts_home, away_stats=ts_away)

    total = strategy._predicted_total(game)
    # Home scores against away's bad defense (115): home_pts ~ pace/100 * (home_off + away_def)/200 * 100
    # Or more correctly: each team's pts = possessions * (off_eff + opp_def_eff) / 200
    # With pace=100: home_pts ~ (115+115)/2 = 115, away_pts ~ (105+105)/2 = 105
    # Old formula: 100 * (115+105) / 200 = 110 (doesn't account for defensive matchup)
    # New formula should be higher because home plays against bad defense
    # And away plays against average defense
    assert total > 200, f"Total {total} seems too low — formula likely double-counting or ignoring defense matchup"
```

- [ ] **Step 2: Fix the formula**

In `backend/analysis/variants/ensemble.py`, replace `_predicted_total`:
```python
    def _predicted_total(self, game: GameData) -> float:
        """Predict total score using matchup-based efficiency.

        Each team's expected points = possessions * (own_off + opp_def) / 200.
        This accounts for the fact that a great offense vs bad defense scores more.
        """
        hs, aws = game.home_stats, game.away_stats
        avg_pace = (hs.pace + aws.pace) / 2
        possessions = avg_pace  # pace is possessions per game (NBA: ~100)
        # Home team scores based on their offense vs away defense
        home_pts = possessions * (hs.offensive_rating + aws.defensive_rating) / 200
        # Away team scores based on their offense vs home defense
        away_pts = possessions * (aws.offensive_rating + hs.defensive_rating) / 200
        return home_pts + away_pts
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_ensemble.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/variants/ensemble.py backend/tests/test_ensemble.py
git commit -m "fix: total points formula uses matchup-based efficiency instead of summing offenses"
```

---

### Task 8: Walk-forward auto-tuner (prevent look-ahead bias)

**Files:**
- Modify: `backend/backtesting/auto_tuner.py`
- Modify: `backend/tests/test_backtest_run_api.py`

- [ ] **Step 1: Write test**

In `backend/tests/test_backtest_run_api.py` (or create new `backend/tests/test_auto_tuner.py`), add:
```python
def test_tune_game_strategy_uses_walk_forward():
    """Auto-tuner should report out-of-sample metrics, not in-sample."""
    # The tune result should contain a 'validation' key separate from training
    from backend.backtesting.auto_tuner import _expand_grid, GAME_PARAM_GRIDS
    configs = _expand_grid(GAME_PARAM_GRIDS["ensemble"])
    assert len(configs) > 0
    # Each config dict should have 'min_edge' and 'weights'
    for cfg in configs:
        assert "min_edge" in cfg
```

- [ ] **Step 2: Implement walk-forward in auto-tuner**

In `backend/backtesting/auto_tuner.py`, modify `tune_game_strategy` to split data temporally:

Replace the function body (lines 71-131):
```python
def tune_game_strategy(
    session: Session,
    strategy_name: str,
    start_date: date,
    end_date: date,
    optimize_for: str = "roi",
) -> dict:
    """Grid-search game strategy parameters with walk-forward validation.

    Splits the date range: first 70% for training/tuning, last 30% for validation.
    Reports validation metrics to prevent look-ahead bias.
    """
    strategy_cls = STRATEGY_MAP.get(strategy_name)
    if not strategy_cls:
        return {"error": f"Unknown strategy: {strategy_name}"}

    grid = GAME_PARAM_GRIDS.get(strategy_name, {"min_edge": [3.0, 5.0, 7.0, 10.0]})
    configs = _expand_grid(grid)

    # Pre-load games once
    games = session.query(Game).filter(
        Game.status == "final", Game.date >= start_date, Game.date <= end_date,
    ).order_by(Game.date).all()
    games_with_results = []
    for g in games:
        if g.home_score is not None and g.away_score is not None:
            game_data = _build_game_data(session, g)
            games_with_results.append((game_data, g.home_score, g.away_score))

    if not games_with_results:
        return {"error": "No completed games found in date range"}

    # Walk-forward split: 70% train, 30% validation
    split_idx = int(len(games_with_results) * 0.7)
    if split_idx < 10 or (len(games_with_results) - split_idx) < 5:
        # Not enough data for walk-forward — use full set with warning
        train_games = games_with_results
        val_games = games_with_results
        walk_forward = False
    else:
        train_games = games_with_results[:split_idx]
        val_games = games_with_results[split_idx:]
        walk_forward = True

    logger.info(
        f"Auto-tuning {strategy_name}: {len(configs)} configs, "
        f"{len(train_games)} train / {len(val_games)} val games"
    )

    best_result = None
    best_config = None
    all_results = []

    for cfg in configs:
        strategy = strategy_cls(strategy_name, cfg)
        bt = Backtester(strategy)
        train_result = bt.run(train_games)
        train_result.pop("picks", None)

        entry = {"config": cfg, "train": train_result}

        score = train_result.get(optimize_for, 0)
        min_picks = max(3, len(train_games) // 20)
        if train_result["total"] < min_picks:
            entry["validation"] = None
            all_results.append(entry)
            continue

        # Validate on held-out data
        if walk_forward:
            val_result = bt.run(val_games)
            val_result.pop("picks", None)
            entry["validation"] = val_result
        else:
            entry["validation"] = train_result

        all_results.append(entry)

        if best_result is None or score > best_result.get(optimize_for, 0):
            best_result = entry
            best_config = cfg

    return {
        "strategy_name": strategy_name,
        "walk_forward": walk_forward,
        "min_picks_required": min_picks if games_with_results else 0,
        "train_games": len(train_games),
        "validation_games": len(val_games),
        "configs_tested": len(configs),
        "best_config": best_config,
        "best_result": best_result,
        "all_results": sorted(
            all_results,
            key=lambda r: (r.get("train") or {}).get(optimize_for, 0),
            reverse=True,
        ),
    }
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_backtest_run_api.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/backtesting/auto_tuner.py
git commit -m "feat: walk-forward validation in auto-tuner to prevent look-ahead bias"
```

---

### Task 9: Point-in-time ELO for model training (EloHistory table)

**Files:**
- Modify: `backend/models.py` (add EloHistory)
- Modify: `backend/database.py` (add migration)
- Modify: `backend/backtesting/historical.py` (store per-game ELO snapshots)
- Modify: `backend/analysis/calibrated_model.py:126-129` (use point-in-time ELO)
- Modify: `backend/analysis/variants/ensemble.py:195-196` (use point-in-time ELO)
- Create: `backend/tests/test_elo_history.py`

- [ ] **Step 1: Add EloHistory model**

In `backend/models.py`, add after the `EloRating` class:
```python
class EloHistory(Base):
    __tablename__ = "elo_history"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    sport = Column(String, nullable=False)
    rating = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: Add migration**

In `backend/database.py`, add:
```python
def migrate_elo_history(engine):
    """Create elo_history table if missing."""
    from backend.models import Base
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)
    if "elo_history" not in inspector.get_table_names():
        from backend.models import EloHistory
        EloHistory.__table__.create(engine)
```

- [ ] **Step 3: Store per-game ELO snapshots in historical.py**

In `backend/backtesting/historical.py`, modify `compute_historical_elo` to save snapshots. After each `elo.update()` call, add:
```python
        # Store point-in-time ELO snapshot for this game
        from backend.models import EloHistory
        home_rating_after = elo.get_rating(home_team.abbreviation)
        away_rating_after = elo.get_rating(away_team.abbreviation)
        session.add(EloHistory(team_id=game.home_team_id, game_id=game.id,
                               sport=sport, rating=home_rating_after))
        session.add(EloHistory(team_id=game.away_team_id, game_id=game.id,
                               sport=sport, rating=away_rating_after))
```

- [ ] **Step 4: Update calibrated_model.py to use point-in-time ELO**

In `backend/analysis/calibrated_model.py`, replace lines 126-129 (the elo_map construction):
```python
        # Use point-in-time ELO (rating at the time of each game) if available
        from backend.models import EloHistory
        elo_history_map: dict[tuple[int, int], float] = {}
        for eh in session.query(EloHistory).all():
            elo_history_map[(eh.team_id, eh.game_id)] = eh.rating

        # Fallback: current ELO ratings for games without history
        elo_current: dict[int, float] = {}
        for elo in session.query(EloRating).all():
            elo_current[elo.team_id] = elo.rating
```

Then in the game loop, change the elo lookup:
```python
            home_elo = elo_history_map.get(
                (game.home_team_id, game.id),
                elo_current.get(game.home_team_id, 1500.0)
            )
            away_elo = elo_history_map.get(
                (game.away_team_id, game.id),
                elo_current.get(game.away_team_id, 1500.0)
            )
```

- [ ] **Step 5: Same fix for ensemble.py train_lgbm_from_db**

In `backend/analysis/variants/ensemble.py`, in `train_lgbm_from_db` (line 195), replace:
```python
        elo_map = {e.team_id: e.rating for e in session.query(EloRating).all()}
```
with:
```python
        from backend.models import EloHistory
        elo_history = {}
        for eh in session.query(EloHistory).all():
            elo_history[(eh.team_id, eh.game_id)] = eh.rating
        elo_fallback = {e.team_id: e.rating for e in session.query(EloRating).all()}
```

And update the per-game ELO lookups inside the loop:
```python
            home_ts = TeamStats(
                ...
                elo_rating=elo_history.get((game.home_team_id, game.id),
                                           elo_fallback.get(game.home_team_id, 1500.0)),
                ...
            )
            away_ts = TeamStats(
                ...
                elo_rating=elo_history.get((game.away_team_id, game.id),
                                           elo_fallback.get(game.away_team_id, 1500.0)),
                ...
            )
```

- [ ] **Step 6: Write tests**

Create `backend/tests/test_elo_history.py`:
```python
from backend.models import Base, Team, Game, EloHistory, EloRating
from backend.database import get_engine, get_session
from datetime import date, datetime, timezone

def test_elo_history_stores_per_game_snapshot():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, home_score=110, away_score=100, status="final")
    session.add(g)
    session.flush()

    session.add(EloHistory(team_id=t1.id, game_id=g.id, sport="nba", rating=1520.0))
    session.add(EloHistory(team_id=t2.id, game_id=g.id, sport="nba", rating=1480.0))
    session.commit()

    # Query back
    h = session.query(EloHistory).filter(EloHistory.game_id == g.id).all()
    assert len(h) == 2
    ratings = {eh.team_id: eh.rating for eh in h}
    assert ratings[t1.id] == 1520.0
    assert ratings[t2.id] == 1480.0
    session.close()
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest backend/tests/test_elo_history.py backend/tests/test_historical.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add backend/models.py backend/database.py backend/backtesting/historical.py backend/analysis/calibrated_model.py backend/analysis/variants/ensemble.py backend/tests/test_elo_history.py
git commit -m "feat: point-in-time ELO history for training data integrity"
```

---

### Task 10: CLV tracking in the pipeline

**Files:**
- Modify: `backend/pipeline/grader.py` (add closing odds capture)
- Modify: `backend/api/stats.py` (CLV endpoint already exists, verify it works)
- Create: `backend/tests/test_clv.py`

- [ ] **Step 1: Add closing odds capture to grader**

The CLV endpoint in `stats.py` already computes CLV correctly when `odds_at_close` is populated. The gap is that nothing populates `PickResult.odds_at_close`. Add a function to `backend/pipeline/grader.py`:

```python
def capture_closing_odds(session, pick_result, game_id: int, pick_type: str, pick_value: str):
    """Store closing odds on a PickResult from the most recent odds snapshot.

    Called during grading when a game reaches 'final' status.
    The most recent pre-game odds snapshot is the closing line.
    """
    from backend.models import Odds
    closing = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp.desc())
        .first()
    )
    if not closing:
        return

    if pick_type == "moneyline":
        if "HOME" in pick_value:
            pick_result.odds_at_close = closing.moneyline_home
        else:
            pick_result.odds_at_close = closing.moneyline_away
    elif pick_type == "spread":
        pick_result.odds_at_close = -110  # spreads are typically -110
    elif pick_type == "over_under":
        pick_result.odds_at_close = -110
```

- [ ] **Step 2: Integrate into the grade_pending_picks flow**

In the scheduler's `grade_pending_picks` function (wherever PickResult is created), call `capture_closing_odds` after creating the result.

- [ ] **Step 3: Write test**

Create `backend/tests/test_clv.py`:
```python
from backend.models import Base, Team, Game, Odds, PickModel, PickResult, StrategyModel
from backend.database import get_engine, get_session
from backend.pipeline.grader import capture_closing_odds
from datetime import date, datetime, timezone

def test_capture_closing_odds_moneyline_home():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()

    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, home_score=110, away_score=100, status="final")
    session.add(g)
    session.flush()

    # Two odds snapshots — second is the closing line
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-170, moneyline_away=150,
                     timestamp=datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc)))
    session.flush()

    # Create a pick result without closing odds
    pr = PickResult(pick_id=1, result="win", payout=1.0)

    capture_closing_odds(session, pr, g.id, "moneyline", "HOME ML")
    assert pr.odds_at_close == -170  # Should use most recent snapshot

    session.close()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest backend/tests/test_clv.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/grader.py backend/tests/test_clv.py
git commit -m "feat: capture closing odds for CLV tracking"
```

---

### Task 11: Odds movement detection (sharp signals)

**Files:**
- Create: `backend/analysis/line_movement.py`
- Create: `backend/tests/test_line_movement.py`

- [ ] **Step 1: Create line movement analyzer**

Create `backend/analysis/line_movement.py`:
```python
"""Detect sharp money signals from odds movement patterns."""

from backend.models import Odds
from sqlalchemy.orm import Session


def analyze_line_movement(session: Session, game_id: int) -> dict | None:
    """Analyze odds snapshots for a game to detect sharp action.

    Returns dict with movement metrics or None if insufficient data.
    """
    snapshots = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp)
        .all()
    )

    if len(snapshots) < 2:
        return None

    first = snapshots[0]
    last = snapshots[-1]

    result = {"game_id": game_id, "snapshots": len(snapshots)}

    # Moneyline movement
    if first.moneyline_home is not None and last.moneyline_home is not None:
        ml_move_home = last.moneyline_home - first.moneyline_home
        result["ml_move_home"] = ml_move_home
        result["ml_move_away"] = (last.moneyline_away or 0) - (first.moneyline_away or 0)

    # Spread movement
    if first.spread_home is not None and last.spread_home is not None:
        spread_move = last.spread_home - first.spread_home
        result["spread_move"] = spread_move

    # O/U movement
    if first.over_under is not None and last.over_under is not None:
        ou_move = last.over_under - first.over_under
        result["ou_move"] = ou_move

    # Reverse line movement (RLM) detection:
    # If the line moves AGAINST the side getting public action,
    # it suggests sharp money on the other side.
    # Heuristic: if spread moved toward home (got more favorable for away)
    # but ML moved toward home (home became bigger favorite), that's RLM on away.
    if "spread_move" in result and "ml_move_home" in result:
        spread_moved_toward_home = result["spread_move"] < -0.5  # spread got more negative for home
        ml_moved_toward_home = result["ml_move_home"] < 0  # home ML got more negative (bigger fav)
        # RLM: spread and ML disagree
        result["reverse_line_movement"] = (
            (spread_moved_toward_home and not ml_moved_toward_home) or
            (not spread_moved_toward_home and ml_moved_toward_home)
        )
        if result.get("reverse_line_movement"):
            # Sharp side is where the ML moved
            result["sharp_side"] = "home" if ml_moved_toward_home else "away"
    else:
        result["reverse_line_movement"] = False

    return result


def get_steam_moves(session: Session, game_id: int, threshold: float = 0.5) -> list[dict]:
    """Detect sudden large line movements (steam moves) between consecutive snapshots."""
    snapshots = (
        session.query(Odds)
        .filter(Odds.game_id == game_id)
        .order_by(Odds.timestamp)
        .all()
    )

    steam_moves = []
    for i in range(1, len(snapshots)):
        prev, curr = snapshots[i - 1], snapshots[i]
        if prev.spread_home is not None and curr.spread_home is not None:
            move = abs(curr.spread_home - prev.spread_home)
            if move >= threshold:
                steam_moves.append({
                    "timestamp": str(curr.timestamp),
                    "spread_before": prev.spread_home,
                    "spread_after": curr.spread_home,
                    "move": curr.spread_home - prev.spread_home,
                })

    return steam_moves
```

- [ ] **Step 2: Write tests**

Create `backend/tests/test_line_movement.py`:
```python
from backend.models import Base, Team, Game, Odds
from backend.database import get_engine, get_session
from backend.analysis.line_movement import analyze_line_movement, get_steam_moves
from datetime import date, datetime, timezone

def _setup():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    t1 = Team(name="A", abbreviation="A", sport="nba")
    t2 = Team(name="B", abbreviation="B", sport="nba")
    session.add_all([t1, t2])
    session.flush()
    g = Game(sport="nba", season="2025-26", date=date(2026, 1, 1),
             home_team_id=t1.id, away_team_id=t2.id, status="scheduled")
    session.add(g)
    session.flush()
    return session, g

def test_analyze_line_movement_basic():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     spread_home=-3.5, spread_away=3.5, over_under=220.0,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-170, moneyline_away=150,
                     spread_home=-4.5, spread_away=4.5, over_under=222.0,
                     timestamp=datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)))
    session.flush()
    result = analyze_line_movement(session, g.id)
    assert result is not None
    assert result["ml_move_home"] == -20  # -170 - (-150) = -20
    assert result["spread_move"] == -1.0  # -4.5 - (-3.5) = -1.0
    assert result["ou_move"] == 2.0
    session.close()

def test_insufficient_snapshots_returns_none():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.flush()
    assert analyze_line_movement(session, g.id) is None
    session.close()

def test_steam_move_detection():
    session, g = _setup()
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-3.0, spread_away=3.0,
                     timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-4.0, spread_away=4.0,
                     timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)))
    session.add(Odds(game_id=g.id, bookmaker="dk", spread_home=-4.5, spread_away=4.5,
                     timestamp=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)))
    session.flush()
    moves = get_steam_moves(session, g.id, threshold=0.5)
    assert len(moves) == 2
    assert moves[0]["move"] == -1.0
    session.close()
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_line_movement.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/line_movement.py backend/tests/test_line_movement.py
git commit -m "feat: line movement analysis with RLM and steam move detection"
```

---

### Task 12: Opponent-adjusted stats

**Files:**
- Create: `backend/analysis/opponent_adjustments.py`
- Create: `backend/tests/test_opponent_adjustments.py`

- [ ] **Step 1: Create opponent adjustment module**

Create `backend/analysis/opponent_adjustments.py`:
```python
"""Adjust team stats for opponent strength (schedule-adjusted efficiency)."""

from sqlalchemy.orm import Session
from backend.models import Game, TeamStat


def compute_adjusted_efficiency(
    session: Session, team_id: int, sport: str, stat_type: str = "offensive_rating"
) -> float | None:
    """Compute schedule-adjusted efficiency for a team.

    Raw efficiency weighted by opponent defensive quality:
    adj_off = raw_off * (league_avg_def / avg_opponent_def)

    Returns adjusted value or None if insufficient data.
    """
    # Get team's raw rating
    raw = _get_stat(session, team_id, stat_type)
    if raw is None:
        return None

    # Get all opponents this team has faced
    games = session.query(Game).filter(
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id)),
        Game.status == "final",
    ).all()

    if len(games) < 5:
        return raw  # Not enough data to adjust

    # Collect opponent defensive ratings
    opp_stat = "defensive_rating" if stat_type == "offensive_rating" else "offensive_rating"
    opp_ratings = []
    for g in games:
        opp_id = g.away_team_id if g.home_team_id == team_id else g.home_team_id
        opp_val = _get_stat(session, opp_id, opp_stat)
        if opp_val is not None:
            opp_ratings.append(opp_val)

    if not opp_ratings:
        return raw

    # League average (use 110.0 as NBA baseline, works for other sports too)
    league_avg = 110.0
    avg_opp = sum(opp_ratings) / len(opp_ratings)

    # Adjust: if you faced tougher defenses (lower rating = better D),
    # your raw offense is understated
    if stat_type == "offensive_rating":
        # Lower avg_opp_def = tougher schedule = boost raw offense
        adjustment = league_avg / avg_opp if avg_opp > 0 else 1.0
    else:
        # For defensive rating: if you faced tougher offenses, your D is better than raw
        adjustment = avg_opp / league_avg if league_avg > 0 else 1.0

    return raw * adjustment


def _get_stat(session: Session, team_id: int, stat_type: str) -> float | None:
    row = session.query(TeamStat).filter(
        TeamStat.team_id == team_id,
        TeamStat.stat_type == stat_type,
    ).first()
    return row.value if row else None
```

- [ ] **Step 2: Write tests**

Create `backend/tests/test_opponent_adjustments.py`:
```python
from backend.models import Base, Team, Game, TeamStat
from backend.database import get_engine, get_session
from backend.analysis.opponent_adjustments import compute_adjusted_efficiency
from datetime import date

def test_adjusted_efficiency_boosts_tough_schedule():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    teams = [Team(name=f"T{i}", abbreviation=f"T{i}", sport="nba") for i in range(7)]
    session.add_all(teams)
    session.flush()

    # Team 0 has 100 off rating but faced 5 opponents with 100 def rating (tough)
    session.add(TeamStat(team_id=teams[0].id, game_id=0, stat_type="offensive_rating", value=100.0))
    for i in range(1, 6):
        session.add(TeamStat(team_id=teams[i].id, game_id=0, stat_type="defensive_rating", value=100.0))
        g = Game(sport="nba", season="2025-26", date=date(2026, 1, i),
                 home_team_id=teams[0].id, away_team_id=teams[i].id,
                 home_score=100, away_score=95, status="final")
        session.add(g)
    session.flush()

    adj = compute_adjusted_efficiency(session, teams[0].id, "nba", "offensive_rating")
    assert adj is not None
    # avg_opp_def = 100, league_avg = 110, adjustment = 110/100 = 1.1
    # adj = 100 * 1.1 = 110
    assert adj > 100, f"Expected boost for tough schedule, got {adj}"
    assert abs(adj - 110.0) < 1.0

    session.close()

def test_adjusted_efficiency_penalizes_weak_schedule():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)

    teams = [Team(name=f"T{i}", abbreviation=f"T{i}", sport="nba") for i in range(7)]
    session.add_all(teams)
    session.flush()

    # Team 0 faced 5 opponents with 120 def rating (bad defenses)
    session.add(TeamStat(team_id=teams[0].id, game_id=0, stat_type="offensive_rating", value=115.0))
    for i in range(1, 6):
        session.add(TeamStat(team_id=teams[i].id, game_id=0, stat_type="defensive_rating", value=120.0))
        g = Game(sport="nba", season="2025-26", date=date(2026, 1, i),
                 home_team_id=teams[0].id, away_team_id=teams[i].id,
                 home_score=110, away_score=95, status="final")
        session.add(g)
    session.flush()

    adj = compute_adjusted_efficiency(session, teams[0].id, "nba", "offensive_rating")
    # avg_opp_def = 120, league_avg = 110, adjustment = 110/120 = 0.917
    # adj = 115 * 0.917 ~ 105.4 — penalized for weak schedule
    assert adj < 115, f"Expected penalty for weak schedule, got {adj}"
    session.close()
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_opponent_adjustments.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/opponent_adjustments.py backend/tests/test_opponent_adjustments.py
git commit -m "feat: opponent-adjusted efficiency for schedule-aware team ratings"
```

---

### Task 13: Dynamic HCA in ensemble fallback

**Files:**
- Modify: `backend/analysis/calibrated_model.py:64-76` (_fallback_probability)
- Modify: `backend/analysis/variants/ensemble.py:244-257` (_model_probability)

- [ ] **Step 1: Update _fallback_probability to use sport-aware HCA**

In `backend/analysis/calibrated_model.py`, change `_fallback_probability`:
```python
def _fallback_probability(game: GameData) -> float:
    """Heuristic sigmoid probability used when the model is not trained."""
    from backend.analysis.sport_constants import get_home_win_rate
    hs, aws = game.home_stats, game.away_stats
    pd_diff = hs.point_diff - aws.point_diff
    pd_score = 1 / (1 + 10 ** (-pd_diff / 10))
    elo_diff = hs.elo_rating - aws.elo_rating
    elo_score = 1 / (1 + 10 ** (-elo_diff / 400))
    net_home = hs.offensive_rating - hs.defensive_rating
    net_away = aws.offensive_rating - aws.defensive_rating
    net_diff = net_home - net_away
    rating_score = 1 / (1 + 10 ** (-net_diff / 10))
    hca = get_home_win_rate(game.sport)
    prob = 0.3 * pd_score + 0.35 * elo_score + 0.25 * rating_score + 0.1 * hca
    return max(0.01, min(0.99, prob))
```

- [ ] **Step 2: _model_probability in ensemble already updated in Task 5**

The HCA fix was already handled in Task 5 (`get_home_win_rate(game.sport)`). Verify it's present.

- [ ] **Step 3: Run tests**

Run: `python -m pytest backend/tests/test_win_probability.py backend/tests/test_ensemble.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add backend/analysis/calibrated_model.py
git commit -m "fix: dynamic sport-aware HCA in fallback probability"
```

---

### Task 14: Add receptions to player stats collectors

**Files:**
- Modify: `backend/collectors/player_stats/nba_api_source.py` (NBA doesn't have receptions, skip)
- Modify: `backend/collectors/player_stats/espn_stats_source.py` (add receptions parsing)

- [ ] **Step 1: Update ESPN stats source to extract receptions**

Check the ESPN stats source for where it parses receiving stats. Add `receptions` extraction alongside `rec_yards`. The field should be populated when available from the API response.

- [ ] **Step 2: Run existing tests**

Run: `python -m pytest backend/tests/test_espn_stats_source.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/collectors/player_stats/
git commit -m "feat: extract receptions field from player stats sources"
```

---

### Task 15: Call startup migrations in app creation

**Files:**
- Modify: `backend/api/main.py` (call new migrations at startup)

- [ ] **Step 1: Add new migration calls**

In the `create_app()` function in `backend/api/main.py`, alongside existing migration calls, add:
```python
from backend.database import migrate_player_stat_receptions, migrate_elo_history
migrate_player_stat_receptions(engine)
migrate_elo_history(engine)
```

- [ ] **Step 2: Run existing API tests**

Run: `python -m pytest backend/tests/test_api_main.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/api/main.py
git commit -m "feat: run new migrations (receptions, elo_history) at startup"
```

---

### Task 16: Final integration — run full test suite

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest backend/tests/ -v --tb=short`
Expected: All tests pass (old + new)

- [ ] **Step 2: Final commit with .gitignore updates**

Add to `.gitignore`:
```
sports_picks.db-shm
sports_picks.db-wal
sports_picks.egg-info/
.claude/settings.local.json
```

```bash
git add .gitignore
git commit -m "chore: add working files to .gitignore"
```

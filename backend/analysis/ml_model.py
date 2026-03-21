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

MIN_ML_GAMES = 200
DEFAULT_RESIDUAL_STD = 12.0  # NBA empirical margin std


class LightGBMModel:
    def __init__(self):
        self.model = None
        self.trained = False
        self.residual_std = None
        self.n_training_games = 0

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

    def predict(self, X: np.ndarray) -> float:
        """Predict home margin for a single game."""
        if not self.trained:
            raise RuntimeError("Model not trained")
        return float(self.model.predict(X)[0])

    def home_win_prob(self, X: np.ndarray) -> float:
        """Derive P(home win) = P(margin > 0) from predicted margin."""
        predicted_margin = self.predict(X)
        std = self.residual_std or DEFAULT_RESIDUAL_STD
        prob = float(norm.sf(0, loc=predicted_margin, scale=std))
        return max(0.01, min(0.99, prob))

    def walk_forward_validate(
        self, X: np.ndarray, y: np.ndarray, dates: list[date],
        min_train: int = 200,
    ) -> dict:
        """Walk-forward validation: train on past, predict future.

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

        correct_direction = ((all_preds > 0) == (all_true > 0)).sum()
        accuracy = float(correct_direction / len(all_true))
        residual_std = float(np.std(residuals))

        # Update model's residual_std with walk-forward calibrated value
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

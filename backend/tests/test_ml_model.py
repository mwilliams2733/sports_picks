"""Tests for LightGBM regression model with walk-forward validation."""
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
    model = LightGBMModel()
    X = np.random.randn(50, 13)
    y = np.random.randn(50) * 10
    dates = [date(2026, 1, i % 28 + 1) for i in range(50)]
    model.train(X, y, dates)
    assert model.trained is False


def test_model_trains_with_enough_games():
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + X[:, 1] * 3 + np.random.randn(n) * 5
    dates = [date(2026, 1, 1) for _ in range(n)]
    model.train(X, y, dates)
    assert model.trained is True
    assert model.residual_std is not None
    assert model.residual_std > 0


def test_model_predict_returns_point_diff():
    model = LightGBMModel()
    np.random.seed(42)
    n = 250
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5
    dates = [date(2026, 1, 1)] * n
    model.train(X, y, dates)
    predicted_margin = model.predict(X[0:1])
    assert -50 < predicted_margin < 50


def test_model_home_win_prob():
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
    model = LightGBMModel()
    np.random.seed(42)
    n = 300
    X = np.random.randn(n, 13)
    y = X[:, 0] * 5 + np.random.randn(n) * 5
    dates = [date(2026, 1, (i % 30) + 1) for i in range(n)]
    metrics = model.walk_forward_validate(X, y, dates, min_train=200)
    assert "accuracy" in metrics
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

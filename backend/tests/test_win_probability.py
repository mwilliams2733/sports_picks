import numpy as np
import pytest
from backend.analysis.win_probability import WinProbabilityModel

def test_train_and_predict():
    model = WinProbabilityModel()
    X = np.array([
        [5.0, 112.0, 105.0, 1550, 1], [-3.0, 105.0, 110.0, 1450, 0],
        [8.0, 115.0, 102.0, 1600, 1], [-6.0, 100.0, 112.0, 1400, 0],
        [2.0, 108.0, 108.0, 1510, 1], [-1.0, 107.0, 109.0, 1490, 0],
    ])
    y = np.array([1, 0, 1, 0, 1, 0])
    model.train(X, y)
    prob = model.predict_proba(np.array([[6.0, 113.0, 104.0, 1560, 1]]))
    assert prob > 0.5

def test_untrained_model_raises():
    model = WinProbabilityModel()
    with pytest.raises(ValueError, match="not trained"):
        model.predict_proba(np.array([[0, 0, 0, 0, 0]]))

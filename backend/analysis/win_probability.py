import numpy as np
from sklearn.linear_model import LogisticRegression

class WinProbabilityModel:
    def __init__(self):
        self.model: LogisticRegression | None = None

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.model = LogisticRegression(max_iter=1000)
        self.model.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> float:
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        proba = self.model.predict_proba(X)
        return float(proba[0][1])

"""Simple baselines for step 5: per-age train mean, and ridge regression."""

from __future__ import annotations

import numpy as np
import pandas as pd

from darwin_core.ingestion import FEATURE_COLUMNS


class TrainMeanPerAge:
    label = "baseline: train mean strength per curing age"

    def fit(self, rows: pd.DataFrame):
        self._by_t = rows.groupby("Time")["strength_mpa"].mean().to_dict()
        self._all = float(rows["strength_mpa"].mean())
        return self

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return np.array([self._by_t.get(t, self._all) for t in rows["Time"]], dtype=float)


class RidgeBaseline:
    label = "baseline: ridge regression on composition + log1p(age)"

    def _X(self, rows):
        X = rows[FEATURE_COLUMNS].to_numpy(float)
        lt = np.log1p(rows["Time"].to_numpy(float))[:, None]
        return np.hstack([X, lt, X[:, :7] * lt])

    def fit(self, rows: pd.DataFrame):
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        self._m = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(self._X(rows), rows["strength_mpa"])
        return self

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        return self._m.predict(self._X(rows))

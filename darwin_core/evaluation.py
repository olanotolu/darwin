"""Step 5: held-out evaluation on unseen mixtures (MPa)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from darwin_core.baselines import RidgeBaseline, TrainMeanPerAge

Z95 = 1.959963984540054


def point_metrics(y: np.ndarray, mu: np.ndarray) -> dict:
    err = np.asarray(mu, float) - np.asarray(y, float)
    return {"mae_mpa": round(float(np.abs(err).mean()), 3),
            "rmse_mpa": round(float(np.sqrt((err**2).mean())), 3), "n": int(len(err))}


def evaluate_model(model, test_rows: pd.DataFrame) -> dict:
    mu, sd = model.predict(test_rows, test_rows["Time"].to_numpy(), latent=False)
    y = test_rows["strength_mpa"].to_numpy()
    out = point_metrics(y, mu)
    out["pi95_coverage"] = round(float((np.abs(y - mu) <= Z95 * sd).mean()), 3)
    out["mean_pi95_halfwidth_mpa"] = round(float((Z95 * sd).mean()), 3)
    out["by_age"] = {int(t): point_metrics(y[m], mu[m]) | {"pi95_coverage": round(float((np.abs(y[m] - mu[m]) <= Z95 * sd[m]).mean()), 3)}
                     for t in sorted(test_rows["Time"].unique()) for m in [test_rows["Time"].to_numpy() == t]}
    out["provenance"] = {"kind": "computed", "source": "held-out evaluation vs measured rows",
                         "model_version": model.version, "seed": model.seed, "split_id": model.split_id,
                         "model_label": model.label}
    return out


def evaluate_with_baselines(model, train_rows: pd.DataFrame, test_rows: pd.DataFrame) -> dict:
    y = test_rows["strength_mpa"].to_numpy()
    res = {"model": evaluate_model(model, test_rows)}
    for key, b in (("train_mean_per_age", TrainMeanPerAge()), ("ridge", RidgeBaseline())):
        b.fit(train_rows)
        res[key] = point_metrics(y, b.predict(test_rows)) | {"label": b.label}
    return res

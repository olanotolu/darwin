"""Step 7: deterministic acquisition (expected improvement), numpy/scipy only.

The acquisition code path receives ONLY: a fitted model, a feature-only
candidate frame (validated to contain no label columns) and the incumbent
best measured TRAINING/revealed 28-day strength. It never sees the oracle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from darwin_core.candidates import check_feasibility
from darwin_core.ingestion import FEATURE_COLUMNS, LABEL_COLUMNS

TARGET_AGE_DAYS = 28


class LabelLeakError(RuntimeError):
    pass


def expected_improvement(mean: np.ndarray, std: np.ndarray, best_f: float, xi: float = 0.0) -> np.ndarray:
    mean, std = np.asarray(mean, float), np.asarray(std, float)
    imp = mean - best_f - xi
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(std > 0, imp / std, 0.0)
        ei = np.where(std > 0, imp * norm.cdf(z) + std * norm.pdf(z), np.maximum(imp, 0.0))
    return np.maximum(ei, 0.0)


def assert_label_free(candidates: pd.DataFrame) -> None:
    leaked = [c for c in candidates.columns if c in LABEL_COLUMNS or c in ("GWP", "Time")]
    if leaked:
        raise LabelLeakError(f"acquisition input contains label/outcome columns: {leaked}")


def rank_candidates(model, candidates: pd.DataFrame, best_f_mpa: float, xi: float = 0.0) -> pd.DataFrame:
    """Score + rank feasible candidates by EI on predicted 28-day strength (MPa).

    Infeasible candidates are kept with eligible=False and their reasons.
    Ties are broken by Mix Name so the ranking is fully deterministic.
    """
    assert_label_free(candidates)
    feats = candidates[["Mix Name"] + FEATURE_COLUMNS].copy()
    reasons = [check_feasibility(r) for r in feats.to_dict("records")]
    mean, std = model.predict(feats, age_days=TARGET_AGE_DAYS, latent=True)
    out = feats[["Mix Name"]].copy()
    out["pred_28d_mpa"] = mean
    out["std_28d_mpa"] = std
    out["eligible"] = [len(r) == 0 for r in reasons]
    out["rejection_reasons"] = reasons
    out["ei"] = np.where(out["eligible"], expected_improvement(mean, std, best_f_mpa, xi), -np.inf)
    out = out.sort_values(["eligible", "ei", "Mix Name"], ascending=[False, False, True], kind="mergesort")
    out["rank"] = np.arange(1, len(out) + 1)
    out["provenance"] = [{"kind": "computed", "source": "EI(xi=%g) on predicted 28d strength" % xi,
                          "model_version": model.version, "seed": model.seed, "split_id": model.split_id,
                          "best_f_mpa": best_f_mpa}] * len(out)
    return out.reset_index(drop=True)

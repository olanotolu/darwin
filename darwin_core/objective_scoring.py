"""P1: objective-aware deterministic acquisition with three modes.

Builds on P0 ``acquisition`` (label guard + expected improvement). Every
component is in [0, 1] and returned alongside the score:

* ``p_meet_target``  P(strength_at_age >= target) under the model posterior
* ``ei_norm``        EI over the incumbent best measured strength / max EI in set
* ``uncertainty_norm`` posterior std / max std in set (exploration signal)
* ``carbon_score``   clip(1 - GWP / carbon_budget, 0, 1) (lower GWP is better)
* ``over_budget_penalty`` -1 when GWP exceeds the carbon budget, else 0

score = sum(weight[mode][c] * component[c]) + over_budget_penalty.
Exploit puts no weight on uncertainty; explore puts most weight on it.
Ties are broken by candidate id, so rankings are fully deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from darwin_core.acquisition import assert_label_free, expected_improvement
from darwin_core.ingestion import FEATURE_COLUMNS

MODE_WEIGHTS = {
    "exploit":  {"p_meet_target": 0.55, "carbon_score": 0.35, "ei_norm": 0.10, "uncertainty_norm": 0.00},
    "balanced": {"p_meet_target": 0.35, "carbon_score": 0.25, "ei_norm": 0.15, "uncertainty_norm": 0.25},
    "explore":  {"p_meet_target": 0.10, "carbon_score": 0.15, "ei_norm": 0.05, "uncertainty_norm": 0.70},
}
OVER_BUDGET_PENALTY = -1.0
SCORER_VERSION = "darwin-objective-acq-v1"


def score_candidates(model, candidates: pd.DataFrame, *, gwp: np.ndarray, gwp_basis: str,
                     target_mpa: float, target_age_days: int, carbon_budget: float,
                     incumbent_mpa: float, mode: str) -> list[dict]:
    if mode not in MODE_WEIGHTS:
        raise ValueError(f"mode must be one of {sorted(MODE_WEIGHTS)}")
    assert_label_free(candidates)
    feats = candidates[["Mix Name"] + FEATURE_COLUMNS]
    if len(feats) == 0:
        return []
    mu, sd = model.predict(feats, age_days=target_age_days, latent=True)
    sd = np.maximum(sd, 1e-9)
    gwp = np.asarray(gwp, float)
    ei = expected_improvement(mu, sd, incumbent_mpa)
    comps = {
        "p_meet_target": norm.cdf((mu - target_mpa) / sd),
        "ei_norm": ei / ei.max() if ei.max() > 0 else np.zeros_like(ei),
        "uncertainty_norm": sd / sd.max(),
        "carbon_score": np.clip(1.0 - gwp / carbon_budget, 0.0, 1.0),
    }
    penalty = np.where(gwp > carbon_budget, OVER_BUDGET_PENALTY, 0.0)
    w = MODE_WEIGHTS[mode]
    score = sum(w[k] * comps[k] for k in w) + penalty
    out = []
    for i, name in enumerate(feats["Mix Name"]):
        out.append({
            "candidate_id": name,
            "score": float(score[i]),
            "components": {k: round(float(v[i]), 6) for k, v in comps.items()}
                          | {"over_budget_penalty": float(penalty[i]), "ei_raw_mpa": round(float(ei[i]), 6)},
            "weights": dict(w),
            "prediction": {"age_days": target_age_days, "mean_mpa": round(float(mu[i]), 4),
                           "std_mpa": round(float(sd[i]), 4), "kind": "latent posterior",
                           "provenance": model.provenance()},
            "gwp_used": {"value": round(float(gwp[i]), 3), "basis": gwp_basis, "unit": "kgCO2e/m3"},
        })
    out.sort(key=lambda r: (-r["score"], r["candidate_id"]))
    for rank, r in enumerate(out, 1):
        r["rank"] = rank
        r["provenance"] = {"kind": "computed", "source": f"{SCORER_VERSION} mode={mode}",
                           "model_version": model.version, "target_mpa": target_mpa,
                           "target_age_days": target_age_days, "carbon_budget": carbon_budget,
                           "incumbent_mpa": incumbent_mpa}
    return out

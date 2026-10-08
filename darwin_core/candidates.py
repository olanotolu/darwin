"""Step 6: deterministic feasibility checks + seeded candidate generation.

Generated candidates are PREDICTION-ONLY: they have no recorded measurement
and can never be "revealed" by the replay oracle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from darwin_core.ingestion import BINDER_COLUMNS, FEATURE_COLUMNS, QUANTITY_COLUMNS
from darwin_core.provenance import prov

WB_MIN, WB_MAX = 0.25, 0.65


def check_feasibility(comp: dict, bounds: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """Return a list of machine-readable rejection reasons ([] == feasible)."""
    reasons: list[dict] = []
    for c in QUANTITY_COLUMNS:
        v = float(comp[c])
        if not np.isfinite(v) or v < 0:
            reasons.append({"code": "NEGATIVE_QUANTITY", "field": c, "limit": 0.0, "value": v})
    binder = float(sum(float(comp[c]) for c in BINDER_COLUMNS))
    if binder <= 0:
        reasons.append({"code": "BINDER_NONPOSITIVE", "field": "binder_kg_m3", "limit": 0.0, "value": binder})
    else:
        wb = float(comp["Water (kg/m3)"]) / binder
        if wb < WB_MIN:
            reasons.append({"code": "WB_RATIO_BELOW_MIN", "field": "w_b", "limit": WB_MIN, "value": round(wb, 4)})
        if wb > WB_MAX:
            reasons.append({"code": "WB_RATIO_ABOVE_MAX", "field": "w_b", "limit": WB_MAX, "value": round(wb, 4)})
    if bounds:
        for c, (lo, hi) in bounds.items():
            v = float(comp[c])
            if v < lo - 1e-9:
                reasons.append({"code": "BELOW_OBSERVED_BOUND", "field": c, "limit": lo, "value": v})
            if v > hi + 1e-9:
                reasons.append({"code": "ABOVE_OBSERVED_BOUND", "field": c, "limit": hi, "value": v})
    return reasons


def observed_bounds(train_mixes: pd.DataFrame) -> dict[str, tuple[float, float]]:
    return {c: (float(train_mixes[c].min()), float(train_mixes[c].max())) for c in QUANTITY_COLUMNS}


def generate_candidates(train_mixes: pd.DataFrame, n: int, seed: int,
                        material_class: str, split_id: str | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Uniformly sample quantities within observed TRAIN bounds (seeded).

    Returns (feasible_df, rejected) where rejected carries reasons.
    Temp is fixed at 22 C (standard curing; typed 'assumed'); Material Source
    is drawn from sources observed in the training mixtures.
    """
    rng = np.random.default_rng(seed)
    bounds = observed_bounds(train_mixes)
    sources = np.sort(train_mixes["Material Source"].unique())
    rows, rejected = [], []
    for i in range(n):
        comp = {c: float(rng.uniform(lo, hi)) if hi > lo else lo for c, (lo, hi) in bounds.items()}
        if material_class == "mortar":
            comp["Coarse Aggregates (kg/m3)"] = 0.0
        comp["Material Source"] = int(rng.choice(sources))
        comp["Temp (C)"] = 22.0
        cid = f"GEN-{material_class[:3]}-s{seed}-{i:04d}"
        reasons = check_feasibility(comp, bounds)
        p = prov("computed", "seeded uniform sampler within train-split bounds", seed=seed,
                 split_id=split_id, temp_c="assumed (standard 22C curing)")
        if reasons:
            rejected.append({"candidate_id": cid, "composition": comp, "reasons": reasons, "provenance": p})
        else:
            rows.append({"Mix Name": cid, **comp, "provenance": p})
    df = pd.DataFrame(rows, columns=["Mix Name"] + FEATURE_COLUMNS + ["provenance"])
    return df, rejected

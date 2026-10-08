"""P1: deterministic facility-aware feasibility solver (no LLM, no randomness
except the explicitly seeded candidate generator).

``evaluate(composition, facility, ...)`` returns exactly one status:

* ``OUTSIDE_SUPPORTED_DOMAIN`` — at least one hard rejection reason: the mix is
  non-physical, violates a facility constraint (availability, inventory,
  dosage bounds, w/b, replacement cap) or a user restriction.
* ``REQUIRES_LAB_VALIDATION`` — no hard rejection, but the model would be
  extrapolating (an ingredient/temperature outside the range observed in the
  real dataset for this material class). Predictions are weakly supported.
* ``COMPUTATIONALLY_FEASIBLE`` — passes every deterministic check AND lies
  inside the observed data domain.

None of these mean construction-ready, approved or code-compliant; every
result carries ``DISCLAIMER``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from darwin_core.candidates import observed_bounds
from darwin_core.facilities import COLUMN_MATERIALS, MATERIAL_COLUMNS, SCM_MATERIALS
from darwin_core.ingestion import BINDER_COLUMNS, FEATURE_COLUMNS, QUANTITY_COLUMNS
from darwin_core.provenance import prov

COMPUTATIONALLY_FEASIBLE = "COMPUTATIONALLY_FEASIBLE"
REQUIRES_LAB_VALIDATION = "REQUIRES_LAB_VALIDATION"
OUTSIDE_SUPPORTED_DOMAIN = "OUTSIDE_SUPPORTED_DOMAIN"
STATUSES = (COMPUTATIONALLY_FEASIBLE, REQUIRES_LAB_VALIDATION, OUTSIDE_SUPPORTED_DOMAIN)

# hard rejection codes
REJECTION_CODES = (
    "NEGATIVE_QUANTITY", "NON_FINITE_QUANTITY", "MISSING_QUANTITY", "BINDER_NONPOSITIVE",
    "WB_RATIO_OUT_OF_BOUNDS", "BINDER_OUT_OF_BOUNDS", "MATERIAL_UNAVAILABLE", "MATERIAL_RESTRICTED",
    "INVENTORY_EXCEEDED", "CEMENT_REPLACEMENT_LIMIT", "FACILITY_BOUND_EXCEEDED",
    "MATERIAL_CLASS_UNSUPPORTED",
)
# soft validation flags
VALIDATION_CODES = ("OUTSIDE_TRAINING_RANGE", "TEMP_OUTSIDE_TRAINING_RANGE")

DISCLAIMER = ("Computational screening only. This status is NOT a construction-readiness, approval, "
              "or code-compliance determination; any mixture requires physical testing and qualified "
              "engineering review before use.")
SOLVER_VERSION = "darwin-feasibility-v1"


def normalize_composition(comp: dict) -> dict:
    """Accept CSV column names or material keys (cement, fly_ash, ...)."""
    out = {}
    for k, v in comp.items():
        out[MATERIAL_COLUMNS.get(k, k)] = v
    return out


def data_domain(obs: pd.DataFrame, material_class: str) -> dict:
    """Observed (feature-only) ranges of the real dataset for one class."""
    mixes = obs.drop_duplicates("Mix Name")
    mixes = mixes[mixes["material_class"] == material_class]
    d = {c: list(v) for c, v in observed_bounds(mixes).items()}
    d["Temp (C)"] = [float(mixes["Temp (C)"].min()), float(mixes["Temp (C)"].max())]
    d["material_class"] = material_class
    return d


def _r(code, field, limit, value, **extra):
    v = value if not isinstance(value, float) else round(value, 4)
    return {"code": code, "field": field, "limit": limit, "value": v, **extra}


def evaluate(composition: dict, facility: dict, *, domain: dict | None = None,
             restricted_materials: list[str] | tuple = (), replacement_cap: float | None = None,
             planned_volume_m3: float | None = None, material_class: str = "concrete") -> dict:
    comp = normalize_composition(composition)
    reasons: list[dict] = []
    flags: list[dict] = []

    q: dict[str, float] = {}
    for c in QUANTITY_COLUMNS:
        if c not in comp or comp[c] is None:
            reasons.append(_r("MISSING_QUANTITY", c, None, None))
            q[c] = 0.0
            continue
        v = float(comp[c])
        if not math.isfinite(v):
            reasons.append(_r("NON_FINITE_QUANTITY", c, None, str(v)))
            v = 0.0
        elif v < 0:
            reasons.append(_r("NEGATIVE_QUANTITY", c, 0.0, v))
        q[c] = v

    if material_class != facility.get("material_class", "concrete"):
        reasons.append(_r("MATERIAL_CLASS_UNSUPPORTED", "material_class", facility.get("material_class"),
                          material_class))
    if material_class == "concrete" and q["Coarse Aggregates (kg/m3)"] <= 0:
        reasons.append(_r("MATERIAL_CLASS_UNSUPPORTED", "Coarse Aggregates (kg/m3)", "> 0 for concrete",
                          q["Coarse Aggregates (kg/m3)"]))

    binder = sum(max(q[c], 0.0) for c in BINDER_COLUMNS)
    mb = facility["mixture_bounds"]
    wb = None
    if binder <= 0:
        reasons.append(_r("BINDER_NONPOSITIVE", "binder_kg_m3", 0.0, binder))
    else:
        wb = q["Water (kg/m3)"] / binder
        lo, hi = mb["w_b"]
        if not (lo - 1e-9 <= wb <= hi + 1e-9):
            reasons.append(_r("WB_RATIO_OUT_OF_BOUNDS", "w_b", [lo, hi], wb))
        blo, bhi = mb["binder_kg_m3"]
        if not (blo - 1e-9 <= binder <= bhi + 1e-9):
            reasons.append(_r("BINDER_OUT_OF_BOUNDS", "binder_kg_m3", [blo, bhi], binder))

    volume = float(planned_volume_m3 if planned_volume_m3 is not None else facility["planned_batch_volume_m3"])
    restricted = set(restricted_materials or ())
    for mat, rec in facility["materials"].items():
        v = q[rec["column"]]
        if v <= 0:
            continue
        if not rec["available"]:
            reasons.append(_r("MATERIAL_UNAVAILABLE", mat, 0.0, v, facility=facility["id"]))
            continue
        if mat in restricted:
            reasons.append(_r("MATERIAL_RESTRICTED", mat, 0.0, v, restriction="user"))
        lo, hi = rec["bounds_kg_m3"]
        if not (lo - 1e-9 <= v <= hi + 1e-9):
            reasons.append(_r("FACILITY_BOUND_EXCEEDED", mat, [lo, hi], v))
        if rec["inventory_kg"] is not None and v * volume > rec["inventory_kg"] + 1e-9:
            reasons.append(_r("INVENTORY_EXCEEDED", mat, rec["inventory_kg"], v * volume,
                              planned_volume_m3=volume, unit="kg"))

    cap = float(replacement_cap if replacement_cap is not None else mb["max_cement_replacement_fraction"])
    repl = None
    if binder > 0:
        repl = sum(q[MATERIAL_COLUMNS[m]] for m in SCM_MATERIALS) / binder
        if repl > cap + 1e-9:
            reasons.append(_r("CEMENT_REPLACEMENT_LIMIT", "scm_fraction_of_binder", cap, repl))

    if domain:
        for c in QUANTITY_COLUMNS:
            lo, hi = domain[c]
            if not (lo - 1e-9 <= q[c] <= hi + 1e-9):
                flags.append(_r("OUTSIDE_TRAINING_RANGE", c, [lo, hi], q[c]))
        t = comp.get("Temp (C)")
        if t is not None:
            lo, hi = domain["Temp (C)"]
            if not (lo <= float(t) <= hi):
                flags.append(_r("TEMP_OUTSIDE_TRAINING_RANGE", "Temp (C)", [lo, hi], float(t)))

    status = (OUTSIDE_SUPPORTED_DOMAIN if reasons else REQUIRES_LAB_VALIDATION if flags
              else COMPUTATIONALLY_FEASIBLE)
    return {
        "status": status,
        "rejection_reasons": reasons,
        "validation_flags": flags,
        "derived": {"binder_kg_m3": round(binder, 3), "w_b": None if wb is None else round(wb, 4),
                    "scm_fraction_of_binder": None if repl is None else round(repl, 4),
                    "replacement_cap": cap, "planned_volume_m3": volume},
        "facility_id": facility["id"],
        "facility_fictional": bool(facility.get("fictional")),
        "disclaimer": DISCLAIMER,
        "provenance": prov("computed", "deterministic feasibility solver", solver_version=SOLVER_VERSION,
                           restricted_materials=sorted(restricted)),
    }


def facility_composition_row(comp: dict, facility: dict) -> dict:
    """Full model feature row for a composition made at a facility. The
    Material Source mapping and curing temperature are fixture assumptions."""
    comp = normalize_composition(comp)
    row = {c: float(comp.get(c, 0.0) or 0.0) for c in QUANTITY_COLUMNS}
    row["Material Source"] = int(comp.get("Material Source", facility["model_material_source"]["value"]))
    row["Temp (C)"] = float(comp.get("Temp (C)", facility["curing_temp_c"]["value"]))
    return row


def generate_facility_candidates(facility: dict, domain: dict, n: int, seed: int, *,
                                 restricted_materials=(), replacement_cap=None,
                                 planned_volume_m3=None) -> tuple[list[dict], list[dict]]:
    """Seeded uniform sampling inside (data domain ∩ facility dosage bounds),
    unavailable/restricted materials fixed at 0. Every sample is run through
    ``evaluate``; rejected ones are kept with their reasons (negative results
    are never dropped). Candidates are PREDICTION-ONLY (no measurement)."""
    rng = np.random.default_rng(seed)
    restricted = set(restricted_materials or ())
    accepted, rejected = [], []
    for i in range(n):
        comp = {}
        for c in QUANTITY_COLUMNS:
            mat = COLUMN_MATERIALS[c]
            rec = facility["materials"][mat]
            if not rec["available"] or mat in restricted:
                comp[c] = 0.0
                continue
            lo = max(domain[c][0], rec["bounds_kg_m3"][0])
            hi = min(domain[c][1], rec["bounds_kg_m3"][1])
            comp[c] = float(rng.uniform(lo, hi)) if hi > lo else float(lo)
        row = facility_composition_row(comp, facility)
        ev = evaluate(row, facility, domain=domain, restricted_materials=sorted(restricted),
                      replacement_cap=replacement_cap, planned_volume_m3=planned_volume_m3)
        cid = f"GEN-{facility['id']}-s{seed}-{i:04d}"
        rec = {"candidate_id": cid, "composition": row, "feasibility": ev, "measurable": False,
               "provenance": prov("computed", "seeded uniform sampler within data domain ∩ facility bounds",
                                  seed=seed, facility=facility["id"], fictional_facility=True,
                                  material_source="assumed (facility fixture mapping)",
                                  temp_c="assumed (standard 22C curing)")}
        (rejected if ev["status"] == OUTSIDE_SUPPORTED_DOMAIN else accepted).append(rec)
    return accepted, rejected


def features_frame(records: list[dict], id_key: str = "candidate_id") -> pd.DataFrame:
    """Label-free feature frame (Mix Name + FEATURE_COLUMNS) for the model."""
    return pd.DataFrame([{"Mix Name": r[id_key], **{c: r["composition"][c] for c in FEATURE_COLUMNS}}
                         for r in records], columns=["Mix Name"] + FEATURE_COLUMNS)

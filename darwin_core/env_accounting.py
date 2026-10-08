"""P1: environmental accounting with an explicitly PARTIAL boundary.

Two estimates are produced and kept in SEPARATE fields — they are alternative
estimates of overlapping scope and are NEVER added together:

* ``darwin_materials_gwp`` — sum(qty_kg x factor) over constituent materials
  using the facility's per-material factor table (fictional, ``assumed``).
* ``boxcrete_gwp_model_output`` — BOxCrete's own GWP LinearModel
  (``SustainableConcreteModel.fit_gwp_model`` with DEFAULT_GWP_COEFFICIENTS,
  dataset-derived regression over the full CSV; see PLAN leakage hazard 2).

Dependency chain (immutable snapshots stored in the ledger):

    supplier -> material factor -> mixture -> impact -> experiment

Changing a factor creates a new factor snapshot and recomputes every current
impact that depended on the old one (new snapshot, ``supersedes`` the old);
linked experiments get a new impact link. Nothing is overwritten.
"""

from __future__ import annotations

import copy
import sys
import types
import warnings

import numpy as np

from darwin_core.facilities import COLUMN_MATERIALS, MATERIAL_COLUMNS, factor_table
from darwin_core.ingestion import FEATURE_COLUMNS, QUANTITY_COLUMNS
from darwin_core.provenance import prov

BOUNDARY = {
    "name": "DARWIN partial boundary: constituent materials only",
    "covered": [
        "Embodied GWP of each constituent material per kg as represented by the supplied factor "
        "(cement, fly ash, slag, water, HRWR admixture, fine aggregate, coarse aggregate), "
        "multiplied by the batch quantity in kg per m3 of mixture",
    ],
    "not_covered": [
        "A2 transport of materials to the plant",
        "A3 concrete plant manufacturing (batching/mixing energy, plant operations, waste)",
        "A4-A5 transport to site and construction",
        "B use stage, including carbonation CO2 uptake",
        "C end of life and D beyond-boundary credits",
        "Packaging, curing energy, and lab testing",
        "Any allocation or biogenic carbon accounting",
    ],
    "statement": ("This is a materials-only partial estimate. It is NOT a full A1-A3 (cradle-to-gate) "
                  "result and NOT an EPD; factors from fictional facilities are assumed values."),
    "unit": "kgCO2e/m3",
}
REQUIRED_FACTOR_FIELDS = ("material", "unit", "source", "geography", "version", "published_or_assumed")
NOT_SUMMED_NOTE = ("darwin_materials_gwp and boxcrete_gwp_model_output are alternative estimates of "
                   "overlapping scope; they are reported side by side and never added.")


def compute_materials_gwp(composition: dict, factors: dict[str, dict], volume_m3: float = 1.0) -> dict:
    """composition: CSV columns (kg/m3). factors: material -> factor record.
    A non-zero ingredient without a usable factor is flagged MISSING_FACTOR and
    the total becomes an explicit lower bound (``complete: False``)."""
    lines, missing, bad_prov = [], [], []
    total = 0.0
    for col in QUANTITY_COLUMNS:
        mat = COLUMN_MATERIALS[col]
        qty = float(composition.get(col, 0.0) or 0.0) * volume_m3
        f = factors.get(mat)
        line = {"material": mat, "qty_kg": round(qty, 4), "factor": copy.deepcopy(f)}
        if qty == 0:
            line.update(contribution_kgco2e=0.0, status="ZERO_QUANTITY")
        elif f is None or f.get("value") is None:
            line.update(contribution_kgco2e=None, status="MISSING_FACTOR")
            missing.append(mat)
        else:
            gaps = [k for k in REQUIRED_FACTOR_FIELDS if not f.get(k)]
            if f.get("published_or_assumed") not in ("published", "assumed"):
                gaps.append("published_or_assumed")
            if f.get("unit") != "kgCO2e/kg":
                raise ValueError(f"factor for {mat} has unit {f.get('unit')!r}, expected kgCO2e/kg")
            c = qty * float(f["value"])
            total += c
            line.update(contribution_kgco2e=round(c, 4), status="INCOMPLETE_PROVENANCE" if gaps else "OK")
            if gaps:
                bad_prov.append({"material": mat, "missing_fields": sorted(set(gaps))})
        lines.append(line)
    complete = not missing
    return {
        "field": "darwin_materials_gwp",
        "value": round(total, 3),
        "unit": "kgCO2e" if volume_m3 != 1.0 else "kgCO2e/m3",
        "volume_m3": volume_m3,
        "complete": complete,
        "status": "COMPLETE" if complete else "INCOMPLETE_MISSING_FACTORS (value is a lower bound)",
        "missing_factors": missing,
        "incomplete_provenance": bad_prov,
        "lines": lines,
        "boundary": BOUNDARY,
        "provenance": prov("computed", "sum(qty_kg x factor) over constituent materials",
                           boundary=BOUNDARY["name"],
                           factor_basis=sorted({(l["factor"] or {}).get("published_or_assumed") or "missing"
                                                for l in lines if l["status"] != "ZERO_QUANTITY"})),
    }


class BoxcreteGwpModel:
    """Thin wrapper over BOxCrete's own GWP LinearModel (real code)."""

    LABEL = ("BOxCrete GWP LinearModel (fit_gwp_model, DEFAULT_GWP_COEFFICIENTS; "
             "coefficients regressed on the full CSV incl. held-out mixes)")

    def __init__(self):
        from darwin_core.model import BOXCRETE_SRC, X_COLUMNS
        if BOXCRETE_SRC not in sys.path:
            sys.path.insert(0, BOXCRETE_SRC)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from boxcrete.concrete_model import SustainableConcreteModel
            scm = SustainableConcreteModel(strength_days=[28])
            # fit_gwp_model only reads data.X_columns (coefficients are not fitted).
            self._m = scm.fit_gwp_model(types.SimpleNamespace(X_columns=X_COLUMNS))

    def predict(self, rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
        import torch
        X = torch.tensor([[float(r[c]) for c in FEATURE_COLUMNS] for r in rows], dtype=torch.float64)
        with torch.no_grad():
            post = self._m.posterior(X)
            mean = -post.mean.squeeze(-1).numpy()      # BOxCrete models -GWP
            std = post.variance.squeeze(-1).clamp_min(0).sqrt().numpy()
        return mean, std

    def output(self, row: dict) -> dict:
        mu, sd = self.predict([row])
        return {"field": "boxcrete_gwp_model_output", "value": round(float(mu[0]), 3),
                "std": round(float(sd[0]), 3), "unit": "kgCO2e/m3",
                "material_source_class": int(row["Material Source"]),
                "provenance": prov("predicted", self.LABEL, model_version="boxcrete@116ad28")}


class AccountingChain:
    """supplier -> factor -> mixture -> impact -> experiment, as immutable
    ledger snapshots. The in-memory index maps current factor snapshots to the
    impacts that depend on them so a factor change recomputes dependents."""

    def __init__(self, ledger, facilities: list[dict]):
        self.ledger = ledger
        self.factors: dict[str, dict[str, dict]] = {}       # facility -> material -> factor
        self.factor_ids: dict[tuple[str, str], str] = {}     # (facility, material) -> calc id
        self.supplier_ids: dict[tuple[str, str], str] = {}
        self.impacts: dict[str, dict] = {}                   # current impact id -> record
        self.experiment_impact: dict[str, str] = {}          # experiment -> current impact id
        for f in facilities:
            self.factors[f["id"]] = factor_table(f)
            for mat, rec in f["materials"].items():
                sup = ledger.add_calc_snapshot(
                    "supplier", {"facility_id": f["id"], "material": mat, "supplier": rec["supplier"],
                                 "fictional": True}, {"supplier": rec["supplier"]}, [])
                self.supplier_ids[(f["id"], mat)] = sup
                self.factor_ids[(f["id"], mat)] = ledger.add_calc_snapshot(
                    "factor", {"facility_id": f["id"], "material": mat}, rec["gwp_factor"], [sup])

    def assess(self, composition: dict, facility_id: str, *, supersedes: str | None = None) -> dict:
        comp = {c: float(composition.get(c, 0.0) or 0.0) for c in QUANTITY_COLUMNS}
        mix_id = self.ledger.add_calc_snapshot("mixture", {"composition_kg_m3": comp}, {}, [])
        used = [m for m in MATERIAL_COLUMNS if comp[MATERIAL_COLUMNS[m]] > 0]
        fids = [self.factor_ids[(facility_id, m)] for m in used]
        result = compute_materials_gwp(comp, self.factors[facility_id])
        imp = self.ledger.add_calc_snapshot(
            "impact", {"mixture_id": mix_id, "facility_id": facility_id, "factor_ids": fids},
            result, [mix_id] + fids, supersedes=supersedes)
        rec = {"impact_id": imp, "mixture_id": mix_id, "facility_id": facility_id, "factor_ids": fids,
               "composition": comp, "result": result}
        self.impacts[imp] = rec
        return rec

    def link_experiment(self, experiment_id: str, impact_id: str, reason: str = "proposal") -> None:
        self.ledger.link_impact(experiment_id, impact_id, reason)
        self.experiment_impact[experiment_id] = impact_id

    def update_factor(self, facility_id: str, material: str, *, value: float, source: str,
                      geography: str, version: str, published_or_assumed: str) -> dict:
        if published_or_assumed not in ("published", "assumed"):
            raise ValueError("published_or_assumed must be 'published' or 'assumed'")
        old_fid = self.factor_ids[(facility_id, material)]
        new_factor = {"material": material, "value": float(value), "unit": "kgCO2e/kg", "source": source,
                      "geography": geography, "version": version,
                      "published_or_assumed": published_or_assumed,
                      "fictional": self.factors[facility_id][material].get("fictional", False)}
        new_fid = self.ledger.add_calc_snapshot(
            "factor", {"facility_id": facility_id, "material": material},
            new_factor, [self.supplier_ids[(facility_id, material)]], supersedes=old_fid)
        self.factors[facility_id][material] = new_factor
        self.factor_ids[(facility_id, material)] = new_fid
        recomputed = []
        for imp_id, rec in list(self.impacts.items()):
            if old_fid not in rec["factor_ids"]:
                continue
            new = self.assess(rec["composition"], facility_id, supersedes=imp_id)
            del self.impacts[imp_id]
            exps = [e for e, i in self.experiment_impact.items() if i == imp_id]
            for e in exps:
                self.link_experiment(e, new["impact_id"], reason=f"recomputed: factor {old_fid} -> {new_fid}")
            recomputed.append({"old_impact_id": imp_id, "new_impact_id": new["impact_id"],
                               "old_value": rec["result"]["value"], "new_value": new["result"]["value"],
                               "experiments_relinked": exps})
        return {"old_factor_id": old_fid, "new_factor_id": new_fid, "factor": new_factor,
                "recomputed_impacts": recomputed}

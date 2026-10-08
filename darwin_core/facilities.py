"""P1: two FICTIONAL facilities used to constrain discovery.

EVERYTHING in this module is a demo fixture. Hudson Materials and Atlantic
Concrete are not real companies; inventories, prices, lab budgets and GWP
factors are illustrative values typed ``assumed``. No factor here comes from
an EPD or a published database. Every record carries ``fictional: True`` and
the ``FICTIONAL_LABEL`` string so no consumer can mistake it for real data.

Material keys map 1:1 to the BOxCrete CSV composition columns (kg/m3).
"""

from __future__ import annotations

import copy

from darwin_core.ingestion import QUANTITY_COLUMNS

FICTIONAL_LABEL = "FICTIONAL — demo fixture, not a real facility; all values assumed"
FIXTURE_VERSION = "darwin-fixtures-2026-10-08"

# material key -> BOxCrete CSV column
MATERIAL_COLUMNS = {
    "cement": "Cement (kg/m3)",
    "fly_ash": "Fly Ash (kg/m3)",
    "slag": "Slag (kg/m3)",
    "water": "Water (kg/m3)",
    "admixture_hrwr": "HRWR (kg/m3)",
    "fine_aggregate": "Fine Aggregate (kg/m3)",
    "coarse_aggregate": "Coarse Aggregates (kg/m3)",
}
COLUMN_MATERIALS = {v: k for k, v in MATERIAL_COLUMNS.items()}
SCM_MATERIALS = ("fly_ash", "slag")  # supplementary cementitious materials (cement replacement)
assert sorted(MATERIAL_COLUMNS.values()) == sorted(QUANTITY_COLUMNS)


def _factor(material: str, value: float | None, geography: str, supplier: str) -> dict:
    return {
        "material": material,
        "value": value,
        "unit": "kgCO2e/kg",
        "source": f"DARWIN fictional fixture — illustrative value for supplier '{supplier}' (NOT an EPD)",
        "geography": geography,
        "version": FIXTURE_VERSION,
        "published_or_assumed": "assumed",
        "fictional": True,
    }


def _mat(material, *, available, inventory_kg, cost, gwp, bounds, supplier, geography):
    return {
        "material": material,
        "column": MATERIAL_COLUMNS[material],
        "available": available,
        "inventory_kg": inventory_kg,           # None == not inventory-limited
        "cost_per_kg_usd": cost,
        "supplier": supplier,
        "gwp_factor": _factor(material, gwp, geography, supplier),
        "bounds_kg_m3": list(bounds),           # facility-supported dosage range
        "provenance": {"kind": "assumed", "source": FICTIONAL_LABEL},
    }


def _hudson() -> dict:
    geo = "US-NY (fictional Hudson Valley plant)"
    m = lambda *a, **k: _mat(*a, geography=geo, **k)  # noqa: E731
    return {
        "id": "hudson",
        "name": "Hudson Materials (FICTIONAL)",
        "fictional": True,
        "label": FICTIONAL_LABEL,
        "material_class": "concrete",
        # Which BOxCrete 'Material Source' class the strength model should treat
        # this facility's materials as. An ASSUMPTION — no real mapping exists.
        "model_material_source": {"value": 2, "provenance": {"kind": "assumed",
                                  "source": "fixture assumption: Class F fly ash set (BOxCrete Material Source 2)"}},
        "curing_temp_c": {"value": 22.0, "provenance": {"kind": "assumed", "source": "standard lab curing"}},
        "materials": {
            "cement": m("cement", available=True, inventory_kg=None, cost=0.16, gwp=0.91,
                        bounds=(60, 900), supplier="Hudson Cement Co. (fictional)"),
            "fly_ash": m("fly_ash", available=True, inventory_kg=20000, cost=0.06, gwp=0.02,
                         bounds=(0, 550), supplier="Ravena Ash (fictional)"),
            "slag": m("slag", available=True, inventory_kg=5000, cost=0.09, gwp=0.08,
                      bounds=(0, 400), supplier="Port Slag Terminal (fictional)"),
            "water": m("water", available=True, inventory_kg=None, cost=0.001, gwp=0.0003,
                       bounds=(110, 340), supplier="municipal (fictional)"),
            "admixture_hrwr": m("admixture_hrwr", available=True, inventory_kg=400, cost=3.20, gwp=1.9,
                                bounds=(0, 5), supplier="PolyFlow Admixtures (fictional)"),
            "fine_aggregate": m("fine_aggregate", available=True, inventory_kg=None, cost=0.018, gwp=0.005,
                                bounds=(400, 1000), supplier="Hudson Sand (fictional)"),
            "coarse_aggregate": m("coarse_aggregate", available=True, inventory_kg=None, cost=0.016, gwp=0.007,
                                  bounds=(900, 1400), supplier="Catskill Stone (fictional)"),
        },
        "mixture_bounds": {"w_b": [0.30, 0.50], "binder_kg_m3": [250, 960],
                           "max_cement_replacement_fraction": 0.70},
        "planned_batch_volume_m3": 15.0,   # inventory checks use qty_kg_m3 x this volume
        "lab_budget": {"max_experiments": 12, "cost_per_experiment_usd": 450.0,
                       "provenance": {"kind": "assumed", "source": FICTIONAL_LABEL}},
    }


def _atlantic() -> dict:
    geo = "US-NJ (fictional Atlantic coast plant)"
    m = lambda *a, **k: _mat(*a, geography=geo, **k)  # noqa: E731
    return {
        "id": "atlantic",
        "name": "Atlantic Concrete (FICTIONAL)",
        "fictional": True,
        "label": FICTIONAL_LABEL,
        "material_class": "concrete",
        "model_material_source": {"value": 1, "provenance": {"kind": "assumed",
                                  "source": "fixture assumption: BOxCrete Material Source 1 cement set"}},
        "curing_temp_c": {"value": 22.0, "provenance": {"kind": "assumed", "source": "standard lab curing"}},
        "materials": {
            "cement": m("cement", available=True, inventory_kg=None, cost=0.18, gwp=0.86,
                        bounds=(60, 900), supplier="Liberty Cement (fictional)"),
            "fly_ash": m("fly_ash", available=False, inventory_kg=0, cost=None, gwp=None,
                         bounds=(0, 0), supplier="none — not stocked"),
            "slag": m("slag", available=True, inventory_kg=2500, cost=0.11, gwp=0.07,
                      bounds=(0, 300), supplier="Newark Slag (fictional)"),
            "water": m("water", available=True, inventory_kg=None, cost=0.0012, gwp=0.0003,
                       bounds=(110, 340), supplier="municipal (fictional)"),
            "admixture_hrwr": m("admixture_hrwr", available=True, inventory_kg=250, cost=3.60, gwp=2.1,
                                bounds=(0, 5), supplier="ShoreChem (fictional)"),
            "fine_aggregate": m("fine_aggregate", available=True, inventory_kg=None, cost=0.021, gwp=0.004,
                                bounds=(400, 1000), supplier="Pine Barrens Sand (fictional)"),
            "coarse_aggregate": m("coarse_aggregate", available=True, inventory_kg=None, cost=0.019, gwp=0.009,
                                  bounds=(900, 1400), supplier="Palisades Trap Rock (fictional)"),
        },
        "mixture_bounds": {"w_b": [0.32, 0.50], "binder_kg_m3": [250, 960],
                           "max_cement_replacement_fraction": 0.50},
        "planned_batch_volume_m3": 15.0,
        "lab_budget": {"max_experiments": 6, "cost_per_experiment_usd": 600.0,
                       "provenance": {"kind": "assumed", "source": FICTIONAL_LABEL}},
    }


_FACILITIES = {"hudson": _hudson(), "atlantic": _atlantic()}


def list_facilities() -> list[dict]:
    """Deep copies, so callers can never mutate the fixtures."""
    return [copy.deepcopy(f) for f in _FACILITIES.values()]


def get_facility(facility_id: str) -> dict:
    if facility_id not in _FACILITIES:
        raise KeyError(f"unknown facility {facility_id!r}; known: {sorted(_FACILITIES)}")
    return copy.deepcopy(_FACILITIES[facility_id])


def factor_table(facility: dict) -> dict[str, dict]:
    """material -> gwp factor record (value may be None == missing)."""
    return {k: copy.deepcopy(v["gwp_factor"]) for k, v in facility["materials"].items()}


def cost_per_m3(composition: dict, facility: dict) -> dict:
    """Material cost per m3 (USD), assumed fixture prices. Missing price -> flagged."""
    lines, missing, total = [], [], 0.0
    for mat, rec in facility["materials"].items():
        q = float(composition.get(rec["column"], 0.0))
        if q == 0:
            continue
        if rec["cost_per_kg_usd"] is None:
            missing.append(mat)
            continue
        c = q * rec["cost_per_kg_usd"]
        total += c
        lines.append({"material": mat, "qty_kg": q, "usd_per_kg": rec["cost_per_kg_usd"], "usd": round(c, 4)})
    return {"value": round(total, 3), "unit": "USD/m3", "complete": not missing, "missing_prices": missing,
            "lines": lines, "provenance": {"kind": "computed", "source": f"qty x fixture price ({FICTIONAL_LABEL})"}}

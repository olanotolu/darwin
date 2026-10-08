"""Steps 1-2: import the real BOxCrete CSV and verify schema + units.

Unit facts (verified against /tmp/boxcrete-src, commit 116ad28):
  * composition columns are kg/m3
  * strength columns are psi; DARWIN converts with 1 psi = 0.00689476 MPa
  * ``Time`` is the curing age in DAYS at which a set of 3 cylinders was
    broken; observed values {1, 3, 5, 14, 28}. Each row = one (mix, age)
    pair; ``Strength (Mean)`` is the mean of Strength1..3 at that age.
    Unit inferred from BOxCrete's bounds (0, 28) and README "28-day" wording.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from darwin_core.provenance import prov

CSV_PATH = Path("/tmp/boxcrete-src/data/boxcrete_data.csv")
BOXCRETE_COMMIT = "116ad287351bee2fa4742ca5d8ce1b7d44db44c9"
PSI_TO_MPA = 0.00689476

EXPECTED_COLUMNS = [
    "Mix Name", "Material Source", "Cement (kg/m3)", "Fly Ash (kg/m3)",
    "Slag (kg/m3)", "Water (kg/m3)", "HRWR (kg/m3)", "Fine Aggregate (kg/m3)",
    "Coarse Aggregates (kg/m3)", "Temp (C)", "Time", "GWP", "Strength1 (psi)",
    "Strength2 (psi)", "Strength3 (psi)", "Strength (Mean)", "Strength (Std)",
    "# of measurements", "Slump (in)",
]
QUANTITY_COLUMNS = [
    "Cement (kg/m3)", "Fly Ash (kg/m3)", "Slag (kg/m3)", "Water (kg/m3)",
    "HRWR (kg/m3)", "Fine Aggregate (kg/m3)", "Coarse Aggregates (kg/m3)",
]
BINDER_COLUMNS = ["Cement (kg/m3)", "Fly Ash (kg/m3)", "Slag (kg/m3)"]
# Mixture-level features (constant within a mix). Order matches BOxCrete
# DEFAULT_X_COLUMNS minus the trailing "Time".
FEATURE_COLUMNS = QUANTITY_COLUMNS + ["Material Source", "Temp (C)"]
# Columns that carry measured outcomes — never handed to acquisition code.
LABEL_COLUMNS = [
    "Strength1 (psi)", "Strength2 (psi)", "Strength3 (psi)", "Strength (Mean)",
    "Strength (Std)", "# of measurements", "Slump (in)", "strength_mpa",
    "strength_std_mpa",
]
EXPECTED_TIMES = {1, 3, 5, 14, 28}
EXPECTED_ROWS = 670
EXPECTED_MIXES = 149


class SchemaError(ValueError):
    pass


def psi_to_mpa(x):
    return np.asarray(x, dtype=float) * PSI_TO_MPA


def file_sha256(path: Path = CSV_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_raw(path: Path = CSV_PATH) -> pd.DataFrame:
    """Step 1: read the real CSV, untouched."""
    return pd.read_csv(path)


def recipe_hash(row: pd.Series | dict) -> str:
    """Hash of the 7 mass quantities (temperature/source excluded so the same
    recipe cured at a different temperature still groups together)."""
    key = "|".join(f"{float(row[c]):.4f}" for c in QUANTITY_COLUMNS)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def verify_and_prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Step 2: hard-fail on schema/unit deviations; add derived columns."""
    if list(df.columns) != EXPECTED_COLUMNS:
        raise SchemaError(f"header mismatch: {list(df.columns)}")
    if len(df) != EXPECTED_ROWS:
        raise SchemaError(f"expected {EXPECTED_ROWS} rows, got {len(df)}")
    if df["Mix Name"].nunique() != EXPECTED_MIXES:
        raise SchemaError("unexpected number of mixes")
    times = set(df["Time"].unique().tolist())
    if not times <= EXPECTED_TIMES:
        raise SchemaError(f"unexpected Time values {times}")
    if df[["Mix Name", "Time"]].duplicated().any():
        raise SchemaError("duplicate (Mix Name, Time) rows")
    if (df[QUANTITY_COLUMNS] < 0).any().any():
        raise SchemaError("negative quantity in source data")
    if df.groupby("Mix Name")[FEATURE_COLUMNS].nunique().max().max() != 1:
        raise SchemaError("composition varies within a Mix Name")
    # Strength sanity: plausible psi range (would be ~1-110 if already MPa).
    s = df["Strength (Mean)"]
    if not (s.min() > 50 and s.max() < 20000):
        raise SchemaError("Strength (Mean) not in a plausible psi range")

    out = df.copy()
    out["material_class"] = np.where(out["Coarse Aggregates (kg/m3)"] > 0, "concrete", "mortar")
    prefix_class = out["Mix Name"].str[0].map({"M": "mortar", "C": "concrete"})
    if (prefix_class != out["material_class"]).any():
        raise SchemaError("material_class (coarse==0) disagrees with M/C prefix")
    out["recipe_hash"] = out.apply(recipe_hash, axis=1)
    out["binder_kg_m3"] = out[BINDER_COLUMNS].sum(axis=1)
    out["w_b"] = out["Water (kg/m3)"] / out["binder_kg_m3"]
    out["strength_mpa"] = psi_to_mpa(out["Strength (Mean)"])
    out["strength_std_mpa"] = psi_to_mpa(out["Strength (Std)"])
    return out


def load_dataset(path: Path = CSV_PATH) -> pd.DataFrame:
    return verify_and_prepare(load_raw(path))


def mixture_table(obs: pd.DataFrame) -> pd.DataFrame:
    """One row per mixture, features only (no outcome columns)."""
    cols = ["Mix Name", "material_class", "recipe_hash", "binder_kg_m3", "w_b"] + FEATURE_COLUMNS
    return obs.drop_duplicates("Mix Name")[cols].reset_index(drop=True)


def data_card(obs: pd.DataFrame, path: Path = CSV_PATH) -> dict:
    src = prov("measured", f"BOxCrete boxcrete_data.csv @ {BOXCRETE_COMMIT[:7]}")
    mixes = mixture_table(obs)
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "boxcrete_commit": BOXCRETE_COMMIT,
        "rows": int(len(obs)),
        "mixtures": int(len(mixes)),
        "mixtures_by_class": mixes["material_class"].value_counts().to_dict(),
        "time_values_days": sorted(int(t) for t in obs["Time"].unique()),
        "time_semantics": "curing age in days at which 3 cylinders were broken; one row per (mix, age)",
        "rows_by_time": {int(k): int(v) for k, v in obs["Time"].value_counts().sort_index().items()},
        "units": {"quantities": "kg/m3", "strength_raw": "psi", "strength_model": "MPa",
                  "psi_to_mpa": PSI_TO_MPA, "temp": "C", "gwp": "kg CO2e/m3", "slump": "in"},
        "strength_mpa_range": [round(float(obs["strength_mpa"].min()), 2),
                               round(float(obs["strength_mpa"].max()), 2)],
        "same_recipe_different_name": _same_recipe_groups(mixes),
        "provenance": src,
    }


def _same_recipe_groups(mixes: pd.DataFrame) -> list[list[str]]:
    g = mixes.groupby("recipe_hash")["Mix Name"].apply(list)
    return [sorted(v) for v in g if len(v) > 1]

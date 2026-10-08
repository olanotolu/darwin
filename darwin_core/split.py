"""Step 3: leakage-safe mixture-level split.

* Groups = connected components over (Mix Name, recipe_hash): every row of a
  mix, and every mix sharing a recipe, lands on the same side.
* Mortar and concrete are split independently (stratified also by Material
  Source); a Split is always for ONE material class — models are never
  trained on one class and tested on the other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from darwin_core.ingestion import mixture_table


@dataclass(frozen=True)
class Split:
    material_class: str
    seed: int
    train_mixes: tuple[str, ...]
    heldout_mixes: tuple[str, ...]
    split_id: str = field(default="")


def _groups(mixes: pd.DataFrame) -> dict[str, str]:
    """Mix Name -> group id via union-find on shared recipe hashes."""
    parent = {m: m for m in mixes["Mix Name"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, names in mixes.groupby("recipe_hash")["Mix Name"]:
        names = sorted(names)
        for n in names[1:]:
            parent[find(n)] = find(names[0])
    return {m: find(m) for m in parent}


def make_split(obs: pd.DataFrame, material_class: str, seed: int = 0,
               heldout_frac: float = 0.3) -> Split:
    if material_class not in ("mortar", "concrete"):
        raise ValueError("material_class must be 'mortar' or 'concrete'")
    mixes = mixture_table(obs)
    mixes = mixes[mixes["material_class"] == material_class].copy()
    mixes["group"] = mixes["Mix Name"].map(_groups(mixes))
    groups = mixes.groupby("group").agg(src=("Material Source", "first")).reset_index()
    rng = np.random.default_rng(seed)
    held_groups: list[str] = []
    for _, g in groups.sort_values("group").groupby("src"):
        ids = g["group"].to_numpy()
        n_hold = int(round(heldout_frac * len(ids)))
        held_groups += list(rng.permutation(ids)[:n_hold])
    held = mixes["group"].isin(held_groups)
    heldout = tuple(sorted(mixes.loc[held, "Mix Name"]))
    train = tuple(sorted(mixes.loc[~held, "Mix Name"]))
    digest = hashlib.sha1(f"{material_class}|{seed}|{','.join(heldout)}".encode()).hexdigest()[:10]
    return Split(material_class, seed, train, heldout, split_id=f"{material_class}-s{seed}-{digest}")

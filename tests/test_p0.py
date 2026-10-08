import inspect
import warnings

import numpy as np
import pandas as pd
import pytest

from darwin_core import acquisition
from darwin_core.acquisition import LabelLeakError, expected_improvement, rank_candidates
from darwin_core.candidates import check_feasibility, generate_candidates
from darwin_core.ingestion import (LABEL_COLUMNS, PSI_TO_MPA, SchemaError, load_dataset, load_raw,
                                   mixture_table, psi_to_mpa, verify_and_prepare)
from darwin_core.model import BoxcreteStrengthModel, boxcrete_available, make_model
from darwin_core.provenance import prov
from darwin_core.replay import HeldOutOracle, best_28d, run_acquisition_replay
from darwin_core.split import make_split

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def obs():
    return load_dataset()


@pytest.fixture(scope="module")
def concrete_fit(obs):
    sp = make_split(obs, "concrete", 0)
    tr = obs[obs["Mix Name"].isin(sp.train_mixes)]
    return sp, tr, make_model("sklearn", 0, sp.split_id).fit(tr)


# --- schema / units -------------------------------------------------------

def test_psi_to_mpa_conversion(obs):
    assert PSI_TO_MPA == 0.00689476
    assert psi_to_mpa(1000) == pytest.approx(6.89476)
    assert psi_to_mpa(5801.5) == pytest.approx(40.0, abs=1e-3)
    np.testing.assert_allclose(obs["strength_mpa"], obs["Strength (Mean)"] * 0.00689476)
    assert 0.5 < obs["strength_mpa"].min() and obs["strength_mpa"].max() < 111


def test_schema_hard_fails_on_bad_header():
    df = load_raw().rename(columns={"Strength (Mean)": "Strength (MPa)"})
    with pytest.raises(SchemaError):
        verify_and_prepare(df)


def test_material_class_matches_prefix(obs):
    m = mixture_table(obs)
    assert (m["material_class"] == m["Mix Name"].str[0].map({"M": "mortar", "C": "concrete"})).all()
    assert set(obs["Time"]) == {1, 3, 5, 14, 28}


# --- split isolation ------------------------------------------------------

@pytest.mark.parametrize("cls", ["concrete", "mortar"])
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_mixture_level_isolation(obs, cls, seed):
    sp = make_split(obs, cls, seed)
    assert not set(sp.train_mixes) & set(sp.heldout_mixes)
    m = mixture_table(obs).set_index("Mix Name")
    assert not set(m.loc[list(sp.train_mixes), "recipe_hash"]) & set(m.loc[list(sp.heldout_mixes), "recipe_hash"])
    # class separation: nothing from the other class on either side
    assert set(m.loc[list(sp.train_mixes + sp.heldout_mixes), "material_class"]) == {cls}
    assert len(sp.train_mixes) + len(sp.heldout_mixes) == (m["material_class"] == cls).sum()


def test_same_recipe_stays_together(obs):
    for seed in range(10):
        sp = make_split(obs, "mortar", seed)
        assert ({"M22", "M36"} <= set(sp.train_mixes)) or ({"M22", "M36"} <= set(sp.heldout_mixes))


# --- no leakage -----------------------------------------------------------

def test_pool_features_contain_no_labels(obs):
    oracle = HeldOutOracle(obs, make_split(obs, "concrete", 0))
    pool = oracle.pool_features()
    assert not set(pool.columns) & set(LABEL_COLUMNS + ["GWP", "Time"])
    assert not hasattr(oracle, "labels") and not hasattr(oracle, "_labels")


def test_acquisition_signature_never_receives_oracle():
    params = list(inspect.signature(rank_candidates).parameters)
    assert params == ["model", "candidates", "best_f_mpa", "xi"]
    src = inspect.getsource(acquisition)
    assert "oracle" not in src.replace("never sees the oracle", "")


def test_acquisition_rejects_label_columns(obs, concrete_fit):
    _, _, model = concrete_fit
    held = obs[obs["Mix Name"].isin(make_split(obs, "concrete", 0).heldout_mixes)]
    with pytest.raises(LabelLeakError):
        rank_candidates(model, held, 50.0)


def test_ranking_invariant_to_hidden_labels(obs, concrete_fit):
    sp, tr, model = concrete_fit
    oracle = HeldOutOracle(obs, sp)
    seen = []
    orig = model.predict

    def spy(mixes, age_days, latent=False):
        seen.append(list(mixes.columns))
        return orig(mixes, age_days, latent)

    model.predict = spy
    try:
        r1 = rank_candidates(model, oracle.pool_features(), best_28d(tr))
        # scramble the hidden labels: ranking must not move
        lab = oracle._HeldOutOracle__labels
        lab["strength_mpa"] = lab["strength_mpa"].sample(frac=1, random_state=1).to_numpy()
        lab["Strength (Mean)"] = 0.0
        r2 = rank_candidates(model, oracle.pool_features(), best_28d(tr))
    finally:
        del model.predict
    assert r1["Mix Name"].tolist() == r2["Mix Name"].tolist()
    np.testing.assert_array_equal(r1["ei"].to_numpy(), r2["ei"].to_numpy())
    for cols in seen:
        assert not set(cols) & set(LABEL_COLUMNS)


def test_incumbent_from_train_only(obs):
    sp = make_split(obs, "concrete", 0)
    tr = obs[obs["Mix Name"].isin(sp.train_mixes)]
    assert best_28d(tr) == tr[tr["Time"] == 28]["strength_mpa"].max()


def test_reveal_only_selected_mixture(obs):
    sp = make_split(obs, "concrete", 0)
    oracle = HeldOutOracle(obs, sp)
    name = sp.heldout_mixes[0]
    rows = oracle.reveal(name, 1)
    assert set(rows["Mix Name"]) == {name}
    assert name not in oracle.pool_features()["Mix Name"].tolist()
    assert oracle.reveal_log[0]["provenance"]["kind"] == "measured"
    with pytest.raises(KeyError):
        oracle.reveal(sp.train_mixes[0])
    with pytest.raises(ValueError):
        oracle.reveal(name)


# --- feasibility ----------------------------------------------------------

BASE = {"Cement (kg/m3)": 300.0, "Fly Ash (kg/m3)": 50.0, "Slag (kg/m3)": 50.0, "Water (kg/m3)": 160.0,
        "HRWR (kg/m3)": 2.0, "Fine Aggregate (kg/m3)": 800.0, "Coarse Aggregates (kg/m3)": 1000.0,
        "Material Source": 1, "Temp (C)": 22.0}


def _codes(c, **kw):
    return {r["code"] for r in check_feasibility(c, **kw)}


def test_feasible_has_no_reasons():
    assert check_feasibility(BASE) == []


def test_rejection_reasons():
    assert _codes(BASE | {"Slag (kg/m3)": -5.0}) >= {"NEGATIVE_QUANTITY"}
    assert _codes(BASE | {"Water (kg/m3)": 80.0}) == {"WB_RATIO_BELOW_MIN"}
    assert _codes(BASE | {"Water (kg/m3)": 300.0}) == {"WB_RATIO_ABOVE_MAX"}
    zero = BASE | {"Cement (kg/m3)": 0.0, "Fly Ash (kg/m3)": 0.0, "Slag (kg/m3)": 0.0}
    assert _codes(zero) == {"BINDER_NONPOSITIVE"}
    r = check_feasibility(BASE | {"Water (kg/m3)": 300.0})[0]
    assert r == {"code": "WB_RATIO_ABOVE_MAX", "field": "w_b", "limit": 0.65, "value": 0.75}
    assert _codes(BASE, bounds={"Cement (kg/m3)": (0.0, 250.0)}) == {"ABOVE_OBSERVED_BOUND"}


def test_generated_candidates_deterministic_and_typed(obs):
    sp = make_split(obs, "concrete", 0)
    tm = mixture_table(obs[obs["Mix Name"].isin(sp.train_mixes)])
    a, ra = generate_candidates(tm, 100, 7, "concrete", sp.split_id)
    b, rb = generate_candidates(tm, 100, 7, "concrete", sp.split_id)
    pd.testing.assert_frame_equal(a.drop(columns="provenance"), b.drop(columns="provenance"))
    assert [r["reasons"] for r in ra] == [r["reasons"] for r in rb]
    assert all(check_feasibility(r) == [] for r in a.drop(columns="provenance").to_dict("records"))
    assert all(r["reasons"] for r in ra)
    assert {p["kind"] for p in a["provenance"]} == {"computed"}


# --- acquisition ----------------------------------------------------------

def test_ei_math():
    ei = expected_improvement(np.array([10.0, 10.0, 5.0]), np.array([0.0, 1.0, 1.0]), 9.0)
    assert ei[0] == pytest.approx(1.0) and ei[1] > ei[0] and ei[2] < 1e-3


def test_deterministic_acquisition(obs):
    sp = make_split(obs, "concrete", 3)
    tr = obs[obs["Mix Name"].isin(sp.train_mixes)]
    pool = HeldOutOracle(obs, sp).pool_features()
    r = [rank_candidates(make_model("sklearn", 3, sp.split_id).fit(tr), pool, best_28d(tr)) for _ in range(2)]
    assert r[0]["Mix Name"].tolist() == r[1]["Mix Name"].tolist()
    np.testing.assert_array_equal(r[0]["ei"].to_numpy(), r[1]["ei"].to_numpy())


# --- model update + provenance -------------------------------------------

def test_observation_changes_model_sklearn(obs):
    res = run_acquisition_replay(obs, "concrete", 0, 1, "sklearn", log=lambda *a: None)
    s = res["steps"][0]
    changed = (s["pred_28d_before_mpa"]["value"] != s["pred_28d_after_mpa"]["value"]
               or s["test_mae_before_mpa"] != s["test_mae_after_mpa"])
    assert changed
    assert s["pred_28d_before_mpa"]["provenance"]["kind"] == "predicted"
    assert s["measured_28d_mpa"]["provenance"]["kind"] == "measured"
    assert s["metrics_provenance"]["kind"] == "computed"
    assert s["pred_28d_before_mpa"]["provenance"]["model_version"] != s["pred_28d_after_mpa"]["provenance"]["model_version"]


@pytest.mark.skipif(not boxcrete_available()[0], reason="torch/BOxCrete runtime unavailable")
def test_observation_changes_model_boxcrete(obs):
    sp = make_split(obs, "mortar", 0)
    tr = obs[obs["Mix Name"].isin(sp.train_mixes)]
    m0 = make_model("boxcrete", 0, sp.split_id).fit(tr)
    assert "BOxCrete" in m0.label and m0.fit_mode.startswith("cold")
    oracle = HeldOutOracle(obs, sp)
    name = sp.heldout_mixes[0]
    feats = oracle.pool_features().query("`Mix Name` == @name")
    before = m0.predict(feats, 28)[0][0]
    m1 = BoxcreteStrengthModel(0, sp.split_id, warm_start_from=m0).fit(
        pd.concat([tr, oracle.reveal(name)], ignore_index=True))
    after = m1.predict(feats, 28)[0][0]
    assert after != before and m1.version != m0.version


def test_provenance_kinds():
    assert prov("assumed", "day-zero pseudo-row")["kind"] == "assumed"
    with pytest.raises(ValueError):
        prov("guessed", "x")

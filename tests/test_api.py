"""API smoke tests via fastapi.testclient against the REAL darwin_core model
(DARWIN_BACKEND=auto -> BOxCrete when the torch stack is present). The model
is fitted once for the module (~60-90 s with BOxCrete)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from darwin_core import api as api_mod

BASE = {"cement": 300, "fly_ash": 50, "slag": 100, "water": 180, "admixture_hrwr": 1.0,
        "fine_aggregate": 800, "coarse_aggregate": 1100}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DARWIN_DB", str(tmp_path_factory.mktemp("db") / "darwin_test.db"))
    mp.delenv("JEV_API_KEY", raising=False)
    api_mod.STATE = None
    with TestClient(api_mod.app) as c:
        yield c
    api_mod.STATE = None
    mp.undo()


def ok(r):
    assert r.status_code == 200, r.text[:500]
    return r.json()


def test_facilities_and_materials_labeled(client):
    f = ok(client.get("/facilities"))
    assert "FICTIONAL" in f["label"] and all(x["fictional"] for x in f["facilities"])
    m = ok(client.get("/materials"))
    assert {x["material"] for x in m["materials"]} >= {"cement", "slag", "fly_ash"}
    assert m["materials"][1]["facilities"]["atlantic"]["available"] is False   # fly ash


def test_dataset_metadata_reports_real_model(client):
    md = ok(client.get("/dataset/metadata"))
    assert md["data_card"]["rows"] == 670 and md["split"]["material_class"] == "concrete"
    if api_mod.boxcrete_available()[0]:
        assert "BOxCrete" in md["model"]["label"] and md["model"]["backend"] == "boxcrete"
    assert md["router"]["reviewer"] == "deterministic-fallback"


def test_predict_uses_cached_model_not_hardcoded(client):
    st = api_mod.get_state()
    model_before = st.model
    p = ok(client.post("/predict", json={"composition": BASE, "facility_id": "hudson"}))
    row = p["composition"]
    mu, sd = st.model.predict(pd.DataFrame([row]), 28, latent=True)
    p28 = next(x for x in p["strength"] if x["age_days"] == 28)
    assert p28["mean_mpa"] == pytest.approx(float(mu[0]), abs=1e-3)
    assert p28["std_mpa"] == pytest.approx(float(sd[0]), abs=1e-3)
    assert p["strength_provenance"]["kind"] == "predicted"
    g = p["gwp"]
    assert g["darwin_materials_gwp"]["field"] == "darwin_materials_gwp" and g["darwin_materials_gwp"]["impact_snapshot_id"]
    if g["boxcrete_gwp_model_output"]:
        assert g["boxcrete_gwp_model_output"]["value"] != g["darwin_materials_gwp"]["value"]
    assert "never added" in g["note"]
    p2 = ok(client.post("/predict", json={"composition": {**BASE, "cement": 400}, "facility_id": "hudson"}))
    assert next(x for x in p2["strength"] if x["age_days"] == 28)["mean_mpa"] != p28["mean_mpa"]
    assert st.model is model_before                       # no refit per request
    bad = ok(client.post("/predict", json={"composition": BASE, "facility_id": "atlantic"}))
    assert bad["feasibility"]["status"] == "OUTSIDE_SUPPORTED_DOMAIN"
    assert "MATERIAL_UNAVAILABLE" in {r["code"] for r in bad["feasibility"]["rejection_reasons"]}


def test_objectives_validation(client):
    assert client.post("/objectives", json={"acquisition_mode": "greedy"}).status_code == 422
    o = ok(client.post("/objectives", json={"target_strength_mpa": 40, "carbon_budget_kgco2e_m3": 300,
                                            "facility_id": "hudson", "acquisition_mode": "balanced"}))
    assert o["acquisition_mode"] == "balanced"


def test_generate_and_rank_modes_differ(client):
    g = ok(client.post("/candidates/generate", json={"n": 150, "seed": 0, "facility_id": "hudson"}))
    assert g["n_accepted"] + g["n_rejected"] == 150 and g["n_accepted"] > 5
    assert all(r["feasibility"]["rejection_reasons"] for r in g["rejected"])
    tops = {}
    for mode in ("exploit", "balanced", "explore"):
        r = ok(client.post("/experiments/rank", json={"mode": mode, "top_k": 8}))
        tops[mode] = [x["candidate_id"] for x in r["ranked"]]
        x = r["ranked"][0]
        assert set(x["components"]) >= {"p_meet_target", "ei_norm", "uncertainty_norm", "carbon_score"}
        assert x["review_route"]["label"] == "SIMULATED — no live Jev call"
        scores = [y["score"] for y in r["ranked"]]
        assert scores == sorted(scores, reverse=True)
    assert tops["exploit"] != tops["explore"]


def test_replay_loop_prediction_before_reveal_then_update(client):
    s = ok(client.post("/replay/start", json={"facility_id": "hudson"}))
    assert s["n_eligible"] > 0 and s["budget_experiments"] == 12
    n = ok(client.post("/replay/next", json={}))
    e = n["experiment"]
    assert e["status"] == "AWAITING_RESULT" and e["measured"] is None
    assert e["predicted"]["made_before_reveal"] is True
    assert client.post("/replay/next", json={}).status_code == 409       # one open experiment at a time
    rv = ok(client.post("/replay/reveal", json={"experiment_id": e["id"]}))
    assert rv["experiment"]["status"] == "MEASURED"
    assert rv["experiment"]["measured"]["provenance"]["kind"] == "measured"
    assert rv["experiment"]["predicted"] == e["predicted"]              # prediction frozen
    assert client.post("/replay/reveal", json={"experiment_id": e["id"]}).status_code == 409
    st = api_mod.get_state()
    mix = pd.DataFrame([{**e["composition"]}])
    before = st.replay.model.predict(mix, 28, latent=True)[0][0]
    u = ok(client.post("/replay/update"))
    assert u["old_version"] != u["new_version"] and u["added_rows"] >= 3
    assert st.replay.model.n_train_rows == st.model.n_train_rows + u["added_rows"]
    after = st.replay.model.predict(mix, 28, latent=True)[0][0]
    assert not np.isclose(before, after)                                 # model actually learned
    assert st.model.version != u["new_version"]                          # startup model untouched
    assert client.post("/replay/update").status_code == 409              # nothing new to train on


def test_history_baselines_report(client):
    h = ok(client.get("/history"))
    assert any(t["to_status"] == "MEASURED" for t in h["transitions"]) and h["router_decisions"]
    b = ok(client.post("/baselines/compare", json={}))
    assert {"model", "train_mean_per_age", "ridge"} <= set(b["heldout_evaluation"])
    rep = ok(client.get("/report/export"))
    assert any("SIMULATED" in d for d in rep["disclaimers"])
    assert any("NOT a full A1-A3" in d for d in rep["disclaimers"])
    assert rep["ledger"]["calc_snapshots"] and rep["ledger"]["experiments"]

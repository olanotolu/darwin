import copy
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from darwin_core import facilities as F
from darwin_core.env_accounting import (BOUNDARY, AccountingChain, BoxcreteGwpModel, compute_materials_gwp)
from darwin_core.feasibility import (COMPUTATIONALLY_FEASIBLE, OUTSIDE_SUPPORTED_DOMAIN, REJECTION_CODES,
                                     REQUIRES_LAB_VALIDATION, STATUSES, data_domain, evaluate,
                                     generate_facility_candidates)
from darwin_core.ingestion import FEATURE_COLUMNS, load_dataset
from darwin_core.jev_router import (CONFLICTING_MEASUREMENTS, DOMAIN_SHIFT, EXPERT_ESCALATION, FALLBACK_LABEL,
                                    MISSING_EVIDENCE, NORMAL_REVIEW, TAXONOMY, DeterministicFallbackRouter,
                                    JevHttpAdapter, get_router, route_ranked)
from darwin_core.ledger import InvalidTransition, Ledger
from darwin_core.model import boxcrete_available
from darwin_core.objective_scoring import MODE_WEIGHTS, score_candidates
from darwin_core.split import make_split

REPO = Path(__file__).resolve().parent.parent
BASE = {"cement": 300, "fly_ash": 50, "slag": 100, "water": 180, "admixture_hrwr": 1.0,
        "fine_aggregate": 800, "coarse_aggregate": 1100}


@pytest.fixture(scope="module")
def obs():
    return load_dataset()


@pytest.fixture(scope="module")
def domain(obs):
    sp = make_split(obs, "concrete", 0)
    return data_domain(obs[obs["Mix Name"].isin(sp.train_mixes)], "concrete")


def codes(res):
    return {r["code"] for r in res["rejection_reasons"]}


# --- facilities -----------------------------------------------------------

def test_every_facility_record_is_labeled_fictional():
    fs = F.list_facilities()
    assert {f["id"] for f in fs} == {"hudson", "atlantic"}
    for f in fs:
        assert f["fictional"] is True and "FICTIONAL" in f["label"] and "FICTIONAL" in f["name"]
        assert f["lab_budget"]["max_experiments"] > 0 and f["lab_budget"]["cost_per_experiment_usd"] > 0
        for mat, m in f["materials"].items():
            assert m["provenance"]["kind"] == "assumed" and "FICTIONAL" in m["provenance"]["source"]
            g = m["gwp_factor"]
            assert g["fictional"] is True and g["published_or_assumed"] == "assumed"
            assert all(g[k] for k in ("material", "unit", "source", "geography", "version"))
            assert "NOT an EPD" in g["source"]


def test_facilities_differ_as_specified():
    h, a = F.get_facility("hudson"), F.get_facility("atlantic")
    assert h["materials"]["slag"]["inventory_kg"] == 5000
    assert h["materials"]["fly_ash"]["available"] and not a["materials"]["fly_ash"]["available"]
    assert a["materials"]["slag"]["inventory_kg"] != 5000
    assert h["lab_budget"] != a["lab_budget"]
    assert h["materials"]["cement"]["gwp_factor"]["value"] != a["materials"]["cement"]["gwp_factor"]["value"]
    assert h["materials"]["cement"]["cost_per_kg_usd"] != a["materials"]["cement"]["cost_per_kg_usd"]


def test_get_facility_returns_copies():
    f = F.get_facility("hudson")
    f["materials"]["slag"]["inventory_kg"] = 1
    assert F.get_facility("hudson")["materials"]["slag"]["inventory_kg"] == 5000


# --- feasibility ----------------------------------------------------------

def test_clean_mix_is_computationally_feasible(domain):
    r = evaluate(BASE, F.get_facility("hudson"), domain=domain)
    assert r["status"] == COMPUTATIONALLY_FEASIBLE
    assert r["rejection_reasons"] == [] and r["validation_flags"] == []
    assert r["derived"]["w_b"] == pytest.approx(0.4)


@pytest.mark.parametrize("change,facility,kwargs,code", [
    ({"water": -5}, "hudson", {}, "NEGATIVE_QUANTITY"),
    ({"water": 300}, "hudson", {}, "WB_RATIO_OUT_OF_BOUNDS"),
    ({}, "atlantic", {}, "MATERIAL_UNAVAILABLE"),                      # BASE has fly ash
    ({"slag": 350}, "hudson", {}, "INVENTORY_EXCEEDED"),               # 350 kg/m3 x 15 m3 > 5000 kg
    ({}, "hudson", {"replacement_cap": 0.2}, "CEMENT_REPLACEMENT_LIMIT"),
    ({}, "hudson", {"restricted_materials": ["slag"]}, "MATERIAL_RESTRICTED"),
    ({"cement": 0, "fly_ash": 0, "slag": 0}, "hudson", {}, "BINDER_NONPOSITIVE"),
    ({"coarse_aggregate": 1500}, "hudson", {}, "FACILITY_BOUND_EXCEEDED"),
])
def test_rejection_codes(domain, change, facility, kwargs, code):
    r = evaluate({**BASE, **change}, F.get_facility(facility), domain=domain, **kwargs)
    assert r["status"] == OUTSIDE_SUPPORTED_DOMAIN
    assert code in codes(r)
    assert codes(r) <= set(REJECTION_CODES)
    for reason in r["rejection_reasons"]:
        assert set(reason) >= {"code", "field", "limit", "value"}


def test_inventory_depends_on_planned_volume(domain):
    h = F.get_facility("hudson")
    assert "INVENTORY_EXCEEDED" in codes(evaluate({**BASE, "slag": 350}, h, domain=domain))
    assert "INVENTORY_EXCEEDED" not in codes(evaluate({**BASE, "slag": 350}, h, domain=domain, planned_volume_m3=10))


def test_extrapolation_requires_lab_validation(domain):
    # HRWR 4.9 kg/m3 is within the facility dosage bound (0-5) but above the training range.
    r = evaluate({**BASE, "admixture_hrwr": 4.9}, F.get_facility("hudson"), domain=domain)
    assert r["status"] == REQUIRES_LAB_VALIDATION and r["rejection_reasons"] == []
    assert {f["code"] for f in r["validation_flags"]} == {"OUTSIDE_TRAINING_RANGE"}


def test_feasibility_never_claims_readiness(domain):
    acc, rej = generate_facility_candidates(F.get_facility("hudson"), domain, 60, 3)
    for rec in acc + rej:
        res = rec["feasibility"]
        assert res["status"] in STATUSES
        assert "NOT a construction-readiness, approval, or code-compliance" in res["disclaimer"]
        blob = json.dumps({k: v for k, v in res.items() if k != "disclaimer"}).lower()
        for word in ("construction-ready", "approved", "compliant", "certified"):
            assert word not in blob


def test_feasibility_and_generation_deterministic(domain):
    h = F.get_facility("hudson")
    assert evaluate(BASE, h, domain=domain) == evaluate(BASE, h, domain=domain)
    a1, r1 = generate_facility_candidates(h, domain, 40, 7)
    a2, r2 = generate_facility_candidates(h, domain, 40, 7)
    assert a1 == a2 and r1 == r2
    assert all(rec["composition"]["Fly Ash (kg/m3)"] == 0
               for rec in generate_facility_candidates(F.get_facility("atlantic"), domain, 20, 0)[0])


# --- environmental accounting --------------------------------------------

def _row(d):
    return {F.MATERIAL_COLUMNS[k]: v for k, v in d.items()}


def test_gwp_arithmetic_matches_hand_sum():
    h = F.get_facility("hudson")
    res = compute_materials_gwp(_row(BASE), F.factor_table(h))
    hand = sum(q * h["materials"][m]["gwp_factor"]["value"] for m, q in BASE.items())
    assert res["value"] == pytest.approx(hand, abs=1e-3)
    assert res["complete"] and res["missing_factors"] == [] and res["field"] == "darwin_materials_gwp"
    assert sum(l["contribution_kgco2e"] for l in res["lines"]) == pytest.approx(res["value"], abs=1e-2)
    for l in res["lines"]:
        if l["status"] == "OK":
            assert set(l["factor"]) >= {"material", "unit", "source", "geography", "version", "published_or_assumed"}


def test_missing_factor_is_flagged_and_total_is_lower_bound():
    fac = F.factor_table(F.get_facility("hudson"))
    full = compute_materials_gwp(_row(BASE), fac)["value"]
    fac["slag"]["value"] = None
    res = compute_materials_gwp(_row(BASE), fac)
    assert res["complete"] is False and res["missing_factors"] == ["slag"]
    assert "lower bound" in res["status"]
    assert res["value"] == pytest.approx(full - 100 * 0.08, abs=1e-3)
    assert next(l for l in res["lines"] if l["material"] == "slag")["status"] == "MISSING_FACTOR"


def test_missing_factor_for_zero_quantity_is_not_flagged():
    a = F.get_facility("atlantic")           # fly ash factor is None (not stocked)
    res = compute_materials_gwp(_row({**BASE, "fly_ash": 0}), F.factor_table(a))
    assert res["complete"] and res["missing_factors"] == []


def test_incomplete_factor_provenance_flagged():
    fac = F.factor_table(F.get_facility("hudson"))
    fac["cement"]["geography"] = ""
    res = compute_materials_gwp(_row(BASE), fac)
    assert res["incomplete_provenance"] == [{"material": "cement", "missing_fields": ["geography"]}]


def test_boundary_is_explicitly_partial():
    assert "NOT a full A1-A3" in BOUNDARY["statement"]
    assert any("A3" in x for x in BOUNDARY["not_covered"]) and any("A2" in x for x in BOUNDARY["not_covered"])


@pytest.mark.skipif(not boxcrete_available()[0], reason="BOxCrete runtime unavailable")
def test_boxcrete_gwp_model_is_real_and_separate(obs):
    m = BoxcreteGwpModel()
    mixes = obs.drop_duplicates("Mix Name")
    mu, sd = m.predict(mixes[FEATURE_COLUMNS].to_dict("records"))
    # BOxCrete's coefficients reproduce the CSV GWP column (regression on it).
    assert np.abs(mu - mixes["GWP"].to_numpy()).max() < 15 and (sd > 0).all()
    row = {**_row(BASE), "Material Source": 2, "Temp (C)": 22.0}
    out = m.output(row)
    assert out["field"] == "boxcrete_gwp_model_output" and out["provenance"]["kind"] == "predicted"


def test_factor_change_recomputes_dependents_with_immutable_snapshots(tmp_path):
    led = Ledger(str(tmp_path / "t.db"))
    chain = AccountingChain(led, F.list_facilities())
    imp = chain.assess(_row(BASE), "hudson")
    eid = led.propose(candidate_id="X", composition=_row(BASE), facility_id="hudson", predicted={"m": 1},
                      acquisition_score=None, acquisition_components=None, constraint_eval=None,
                      provenance={"kind": "computed", "source": "test"})
    chain.link_experiment(eid, imp["impact_id"])
    old_snapshot = led.calc_snapshot(imp["impact_id"])
    upd = chain.update_factor("hudson", "cement", value=0.80, source="test EPD", geography="US",
                              version="v2", published_or_assumed="assumed")
    (rc,) = upd["recomputed_impacts"]
    assert rc["old_impact_id"] == imp["impact_id"] and rc["experiments_relinked"] == [eid]
    assert rc["new_value"] == pytest.approx(rc["old_value"] + 300 * (0.80 - 0.91), abs=1e-3)
    new = led.calc_snapshot(rc["new_impact_id"])
    assert new["supersedes"] == imp["impact_id"] and upd["new_factor_id"] in new["parents"]
    assert led.calc_snapshot(imp["impact_id"]) == old_snapshot          # old one untouched
    assert [i["calc_snapshot_id"] for i in led.impacts_for(eid)] == [imp["impact_id"], rc["new_impact_id"]]
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("UPDATE calc_snapshots SET kind='x'")
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("DELETE FROM calc_snapshots")
    # Unrelated factor change recomputes nothing.
    assert chain.update_factor("atlantic", "cement", value=0.5, source="s", geography="g", version="v",
                               published_or_assumed="assumed")["recomputed_impacts"] == []


# --- ledger ---------------------------------------------------------------

def _propose(led, cid="C1", session=None):
    return led.propose(candidate_id=cid, composition={"a": 1}, facility_id="hudson",
                       predicted={"mean_mpa": 40.0}, acquisition_score=0.5, acquisition_components={"x": 1},
                       constraint_eval={"status": "COMPUTATIONALLY_FEASIBLE"},
                       provenance={"kind": "computed", "source": "test"}, session_id=session)


MEAS = {"rows": [{"age_days": 28, "strength_mpa": 41.0}], "provenance": {"kind": "measured", "source": "test"}}


def test_ledger_happy_path_and_audit(tmp_path):
    led = Ledger(str(tmp_path / "l.db"))
    e = _propose(led)
    led.transition(e, "APPROVED", actor="engineer")
    led.transition(e, "AWAITING_RESULT", actor="lab")
    got = led.record_measurement(e, MEAS, actor="lab")
    assert got["status"] == "MEASURED" and got["measured"] == MEAS
    assert [t["to_status"] for t in led.transitions_for(e)] == ["PROPOSED", "APPROVED", "AWAITING_RESULT", "MEASURED"]


@pytest.mark.parametrize("path,bad", [
    ([], "MEASURED"), ([], "AWAITING_RESULT"), (["APPROVED"], "PROPOSED"),
    (["APPROVED", "AWAITING_RESULT"], "REJECTED"), (["REJECTED"], "APPROVED"), ([], "DONE"),
])
def test_ledger_rejects_invalid_transitions(tmp_path, path, bad):
    led = Ledger(str(tmp_path / "l.db"))
    e = _propose(led)
    for s in path:
        led.transition(e, s, actor="t")
    with pytest.raises(InvalidTransition):
        led.transition(e, bad, actor="t", measured=MEAS if bad == "MEASURED" else None)


def test_measured_is_terminal_and_requires_measured_provenance(tmp_path):
    led = Ledger(str(tmp_path / "l.db"))
    e = _propose(led)
    for s in ("APPROVED", "AWAITING_RESULT"):
        led.transition(e, s, actor="t")
    with pytest.raises(ValueError):
        led.record_measurement(e, {"rows": [], "provenance": {"kind": "predicted"}}, actor="t")
    led.record_measurement(e, MEAS, actor="t")
    for s in ("REJECTED", "APPROVED", "AWAITING_RESULT"):
        with pytest.raises(InvalidTransition):
            led.transition(e, s, actor="t")


def test_db_trigger_blocks_sql_bypass(tmp_path):
    led = Ledger(str(tmp_path / "l.db"))
    e = _propose(led)
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("UPDATE experiments SET status='MEASURED' WHERE id=?", (e,))
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("UPDATE experiments SET status='BOGUS' WHERE id=?", (e,))


def test_only_approved_measurements_enter_training(tmp_path):
    led = Ledger(str(tmp_path / "l.db"))
    good = _propose(led, "GOOD", "S")
    for s in ("APPROVED", "AWAITING_RESULT"):
        led.transition(good, s, actor="t")
    led.record_measurement(good, MEAS, actor="t")
    rej = _propose(led, "REJ", "S"); led.transition(rej, "REJECTED", actor="t")
    _propose(led, "PROP", "S")
    # Forged row inserted MEASURED directly, with no audited APPROVED transition.
    led.conn.execute("INSERT INTO experiments (id,candidate_id,session_id,composition_json,proposed_at,"
                     "predicted_json,status,measured_json,provenance_json,updated_at) VALUES "
                     "('FORGED','F','S','{}','t','{}','MEASURED',?,'{}','t')", (json.dumps(MEAS),))
    got = led.approved_measurements_for_training("S")
    assert [e["candidate_id"] for e in got] == ["GOOD"]


# --- Jev router -----------------------------------------------------------

def _cand(**kw):
    c = {"candidate_id": "C", "facility_id": "hudson", "facility_fictional": True,
         "feasibility_status": "COMPUTATIONALLY_FEASIBLE", "validation_flags": [], "rejection_reasons": [],
         "prediction": {"mean_mpa": 45.0, "std_mpa": 4.0},
         "gwp": {"missing_factors": [], "incomplete_provenance": []}, "measured": None,
         "material_source_in_training": True, "acquisition_score": 0.9, "rank": 1}
    c.update(kw)
    return c


@pytest.mark.parametrize("kw,route", [
    ({}, NORMAL_REVIEW),
    ({"gwp": {"missing_factors": ["slag"]}}, MISSING_EVIDENCE),
    ({"validation_flags": [{"code": "OUTSIDE_TRAINING_RANGE", "field": "HRWR (kg/m3)"}]}, DOMAIN_SHIFT),
    ({"measured": {"strength_28d_mpa": 70.0}}, CONFLICTING_MEASUREMENTS),
    ({"prediction": {"mean_mpa": 20.0, "std_mpa": 9.0}}, EXPERT_ESCALATION),
    ({"gwp": {"missing_factors": ["slag"]}, "material_source_in_training": False}, EXPERT_ESCALATION),
])
def test_fallback_taxonomy_and_shape(kw, route):
    d = DeterministicFallbackRouter().classify(_cand(**kw))
    assert d["route"] == route and d["route"] in TAXONOMY
    assert d["label"] == FALLBACK_LABEL == "SIMULATED — no live Jev call"
    assert d["reviewer"] == "deterministic-fallback" and d["live_call"] is False
    assert d["adapter_model_version"] and d["numeric_fields_modified"] is False
    assert set(d) >= {"candidate_id", "route", "route_description", "triggered", "reviewer", "label"}
    assert "acquisition_score" not in json.dumps(d) and "rank" not in d


def test_router_cannot_reorder_or_mutate_ranking():
    ranked = [_cand(candidate_id=f"C{i}", acquisition_score=1 - i / 10) for i in range(4)]
    before = copy.deepcopy(ranked)
    out = route_ranked(ranked, DeterministicFallbackRouter())
    assert ranked == before and [d["candidate_id"] for d in out] == ["C0", "C1", "C2", "C3"]

    class Evil:
        def classify(self, c):
            c["acquisition_score"] = 0
            return {"route": NORMAL_REVIEW}
    with pytest.raises(RuntimeError):
        route_ranked(ranked, Evil())


def test_get_router_selects_fallback_without_key():
    assert isinstance(get_router({}), DeterministicFallbackRouter)
    assert isinstance(get_router({"JEV_API_KEY": "k"}), JevHttpAdapter)


def test_jev_adapter_matches_observed_contract_via_mock_transport():
    """MOCKED transport (no network): checks request/response shapes against
    the OpenAPI snapshot fetched from https://api.typesafe.ai/openapi.json."""
    import httpx
    spec = json.loads((REPO / "docs" / "jev_openapi_snapshot.json").read_text())
    req_schema = spec["components"]["schemas"]["SystemOneRequest"]
    assert set(req_schema["required"]) == {"model", "questions", "state"}
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "jev-2026-09-15", "usage": {"input_tokens": 10, "output_tokens": 1},
                                         "answers": {"review_route": {"type": "choice", "choice": DOMAIN_SHIFT,
                                                                      "confidence": 0.8,
                                                                      "probabilities": {DOMAIN_SHIFT: 0.8}}}})
    ad = JevHttpAdapter("test-key", transport=httpx.MockTransport(handler))
    d = ad.classify(_cand())
    assert seen["auth"] == "Bearer test-key" and seen["path"] == "/v1/systemone"
    assert set(seen["body"]) == set(req_schema["required"])
    q = seen["body"]["questions"]["review_route"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(TAXONOMY)
    assert "acquisition_score" not in seen["body"]["state"] and '"rank"' not in seen["body"]["state"]
    assert d["route"] == DOMAIN_SHIFT and d["adapter_model_version"] == "jev-2026-09-15" and d["live_call"]


# --- acquisition modes ----------------------------------------------------

class _StubModel:
    version, seed, split_id, label = "stub", 0, None, "stub"

    def predict(self, df, age_days, latent=True):
        mu = np.array([50.0, 42.0, 38.0])[: len(df)]
        sd = np.array([1.0, 3.0, 12.0])[: len(df)]
        return mu, sd

    def provenance(self):
        return {"kind": "predicted", "source": "stub"}


def test_modes_change_scoring():
    df = pd.DataFrame([{"Mix Name": n, **{c: 1.0 for c in FEATURE_COLUMNS}} for n in ("A", "B", "C")])
    kw = dict(gwp=np.array([250.0, 200.0, 200.0]), gwp_basis="t", target_mpa=40, target_age_days=28,
              carbon_budget=300, incumbent_mpa=45)
    top = {m: [r["candidate_id"] for r in score_candidates(_StubModel(), df, mode=m, **kw)] for m in MODE_WEIGHTS}
    assert top["exploit"][0] == "A" and top["explore"][0] == "C"
    assert MODE_WEIGHTS["exploit"]["uncertainty_norm"] == 0 < MODE_WEIGHTS["explore"]["uncertainty_norm"]
    r = score_candidates(_StubModel(), df, mode="balanced", **kw)[0]
    assert set(r["components"]) >= {"p_meet_target", "ei_norm", "uncertainty_norm", "carbon_score",
                                    "over_budget_penalty"}
    assert r["score"] == pytest.approx(sum(r["weights"][k] * r["components"][k] for k in r["weights"])
                                       + r["components"]["over_budget_penalty"], abs=1e-5)
    over = score_candidates(_StubModel(), df, mode="exploit", **{**kw, "gwp": np.array([400.0, 200, 200])})
    assert next(x for x in over if x["candidate_id"] == "A")["components"]["over_budget_penalty"] == -1.0

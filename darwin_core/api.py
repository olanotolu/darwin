"""P1: FastAPI service over darwin_core.

Start:  .venv/bin/uvicorn darwin_core.api:app
Env:    DARWIN_BACKEND (auto|boxcrete|sklearn, default auto), DARWIN_SEED (0),
        DARWIN_DB (default <repo>/darwin.db), JEV_API_KEY (optional).

The strength model is fitted ONCE at startup on the concrete TRAIN split and
cached. No request refits it, except ``POST /replay/update``, whose purpose is
to refit the REPLAY SESSION model on approved measurements (the startup model
is never mutated). Every number comes from darwin_core; nothing is hardcoded.
"""

from __future__ import annotations

import copy
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from darwin_core import __version__
from darwin_core.candidates import check_feasibility
from darwin_core.env_accounting import (BOUNDARY, NOT_SUMMED_NOTE, AccountingChain, BoxcreteGwpModel,
                                        compute_materials_gwp)
from darwin_core.evaluation import evaluate_model, evaluate_with_baselines
from darwin_core.facilities import FICTIONAL_LABEL, MATERIAL_COLUMNS, cost_per_m3, list_facilities
from darwin_core.feasibility import (DISCLAIMER, OUTSIDE_SUPPORTED_DOMAIN, data_domain, evaluate,
                                     facility_composition_row, features_frame, generate_facility_candidates)
from darwin_core.ingestion import FEATURE_COLUMNS, PSI_TO_MPA, data_card, load_dataset
from darwin_core.jev_router import TAXONOMY, get_router, route_ranked
from darwin_core.ledger import APPROVED, AWAITING_RESULT, InvalidTransition, Ledger
from darwin_core.model import BoxcreteStrengthModel, boxcrete_available, make_model
from darwin_core.objective_scoring import MODE_WEIGHTS, score_candidates
from darwin_core.replay import HeldOutOracle, best_28d, run_random_replay
from darwin_core.split import make_split

REPO = Path(__file__).resolve().parent.parent
MATERIAL_CLASS = "concrete"
AGES = [1, 3, 5, 14, 28]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- state
class ReplaySession:
    def __init__(self, sid, oracle, model, train_rows, facility_id, apply_facility, budget, eligible, excluded):
        self.id = sid
        self.oracle = oracle
        self.model = model
        self.train_rows = train_rows
        self.facility_id = facility_id
        self.apply_facility = apply_facility
        self.budget = budget
        self.eligible = eligible
        self.excluded = excluded
        self.trained_experiments: list[str] = []
        self.steps: list[dict] = []
        self.updates: list[dict] = []
        self.started_at = _now()


class DarwinState:
    def __init__(self, backend: str, seed: int, db_path: str):
        self.obs = load_dataset()
        self.seed = seed
        self.split = make_split(self.obs, MATERIAL_CLASS, seed)
        self.train_rows = self.obs[self.obs["Mix Name"].isin(self.split.train_mixes)].copy()
        self.model = make_model(backend, seed, self.split.split_id).fit(self.train_rows)  # ONCE
        self.domain = data_domain(self.train_rows, MATERIAL_CLASS)
        self.train_sources = sorted(int(s) for s in self.train_rows["Material Source"].unique())
        self.facilities = {f["id"]: f for f in list_facilities()}
        self.ledger = Ledger(db_path)
        self.chain = AccountingChain(self.ledger, list(self.facilities.values()))
        self.gwp_model = BoxcreteGwpModel() if boxcrete_available()[0] else None
        self.router = get_router()
        self.model_snapshot_id = self.ledger.add_model_snapshot(
            self.model.label, self.model.version,
            {"split_id": self.split.split_id, "n_train_rows": self.model.n_train_rows,
             "fit_seconds": self.model.fit_seconds, "fit_mode": getattr(self.model, "fit_mode", None),
             "role": "startup model (cached)"})
        self.objective = {"id": "default", "target_strength_mpa": 40.0, "target_age_days": 28,
                          "secondary_target_1d_mpa": None, "carbon_budget_kgco2e_m3": 300.0,
                          "facility_id": "hudson", "acquisition_mode": "balanced",
                          "restricted_materials": [], "replacement_cap": None, "planned_volume_m3": None,
                          "provenance": {"kind": "assumed", "source": "DARWIN default objective (PLAN Decisions a)"}}
        self.candidates: dict[str, dict] = {}
        self.rejected_candidates: list[dict] = []
        self.replay: ReplaySession | None = None
        self.baselines_cache: dict | None = None
        self.lock = threading.RLock()

    # helpers -----------------------------------------------------------------
    def facility(self, fid: str | None) -> dict:
        fid = fid or self.objective["facility_id"]
        if fid not in self.facilities:
            raise HTTPException(404, f"unknown facility {fid!r}")
        f = copy.deepcopy(self.facilities[fid])
        for mat, fac in self.chain.factors[fid].items():   # current (possibly updated) factors
            f["materials"][mat]["gwp_factor"] = copy.deepcopy(fac)
        return f

    def feasibility(self, row: dict, facility: dict) -> dict:
        o = self.objective
        return evaluate(row, facility, domain=self.domain, restricted_materials=o["restricted_materials"],
                        replacement_cap=o["replacement_cap"], planned_volume_m3=o["planned_volume_m3"],
                        material_class=MATERIAL_CLASS)

    def gwp_both(self, row: dict, facility_id: str | None) -> dict:
        out = {"darwin_materials_gwp": None, "boxcrete_gwp_model_output": None, "note": NOT_SUMMED_NOTE}
        if facility_id:
            out["darwin_materials_gwp"] = compute_materials_gwp(row, self.chain.factors[facility_id])
        if self.gwp_model is not None:
            out["boxcrete_gwp_model_output"] = self.gwp_model.output(row)
        return out

    def review_record(self, cid, row, feas, pred, gwp, measured=None) -> dict:
        return {"candidate_id": cid, "facility_id": feas["facility_id"],
                "facility_fictional": feas["facility_fictional"], "feasibility_status": feas["status"],
                "validation_flags": feas["validation_flags"], "rejection_reasons": feas["rejection_reasons"],
                "prediction": pred, "gwp": gwp, "measured": measured,
                "material_source_in_training": int(row["Material Source"]) in self.train_sources}


STATE: DarwinState | None = None


def get_state() -> DarwinState:
    global STATE
    if STATE is None:
        STATE = DarwinState(os.environ.get("DARWIN_BACKEND", "auto"), int(os.environ.get("DARWIN_SEED", "0")),
                            os.environ.get("DARWIN_DB", str(REPO / "darwin.db")))
    return STATE


@asynccontextmanager
async def lifespan(_app):
    get_state()   # load data + fit the model once, before serving
    yield


app = FastAPI(title="DARWIN API (P1)", version=__version__, lifespan=lifespan,
              description="Concrete discovery layer over BOxCrete. Facilities are FICTIONAL. "
                          "Nothing here is construction-ready, approved or code-compliant.")


# --------------------------------------------------------------------------- schemas
class ObjectiveIn(BaseModel):
    target_strength_mpa: float = Field(40.0, gt=0)
    target_age_days: Literal[1, 3, 5, 14, 28] = 28
    secondary_target_1d_mpa: float | None = None
    carbon_budget_kgco2e_m3: float = Field(300.0, gt=0)
    facility_id: str = "hudson"
    acquisition_mode: Literal["exploit", "explore", "balanced"] = "balanced"
    restricted_materials: list[str] = []
    replacement_cap: float | None = Field(None, ge=0, le=1)
    planned_volume_m3: float | None = Field(None, gt=0)


class PredictIn(BaseModel):
    composition: dict[str, float]
    facility_id: str | None = None
    ages_days: list[int] = AGES
    use_replay_model: bool = False


class GenerateIn(BaseModel):
    n: int = Field(100, ge=1, le=2000)
    seed: int = 0
    facility_id: str | None = None


class RankIn(BaseModel):
    candidate_ids: list[str] | None = None
    mode: Literal["exploit", "explore", "balanced"] | None = None
    top_k: int = Field(10, ge=1, le=500)
    propose_top: int = Field(0, ge=0, le=50)


class ReplayStartIn(BaseModel):
    facility_id: str | None = None
    apply_facility_constraints: bool = True
    budget: int | None = Field(None, ge=1)


class ReplayNextIn(BaseModel):
    mix_name: str | None = None
    mode: Literal["exploit", "explore", "balanced"] | None = None
    auto_approve: bool = True


class RevealIn(BaseModel):
    experiment_id: str


class TransitionIn(BaseModel):
    to_status: str
    actor: str
    note: str = ""


class FactorUpdateIn(BaseModel):
    facility_id: str
    material: str
    value: float = Field(..., ge=0)
    source: str
    geography: str
    version: str
    published_or_assumed: Literal["published", "assumed"]


class CompareIn(BaseModel):
    include_replay_vs_random: bool = True


# --------------------------------------------------------------------------- helpers
def _predict_all_ages(model, row: dict, ages: list[int]) -> list[dict]:
    df = pd.DataFrame([row] * len(ages))
    mu_l, sd_l = model.predict(df, np.asarray(ages, float), latent=True)
    mu_p, sd_p = model.predict(df, np.asarray(ages, float), latent=False)
    return [{"age_days": int(a), "mean_mpa": round(float(mu_l[i]), 3), "std_mpa": round(float(sd_l[i]), 3),
             "predictive_std_mpa": round(float(sd_p[i]), 3),
             "pi95_mpa": [round(float(mu_p[i] - 1.96 * sd_p[i]), 3), round(float(mu_p[i] + 1.96 * sd_p[i]), 3)],
             "mean_psi": round(float(mu_l[i] / PSI_TO_MPA), 1)} for i, a in enumerate(ages)]


def _score(st: DarwinState, model, records: list[dict], facility: dict, mode: str, incumbent: float) -> list[dict]:
    o = st.objective
    feats = features_frame(records)
    gwp = [compute_materials_gwp(r["composition"], st.chain.factors[facility["id"]])["value"] for r in records]
    return score_candidates(model, feats, gwp=np.array(gwp), gwp_basis=f"darwin_materials_gwp ({facility['id']}, "
                            "partial boundary; BOxCrete GWP model NOT added)",
                            target_mpa=o["target_strength_mpa"], target_age_days=o["target_age_days"],
                            carbon_budget=o["carbon_budget_kgco2e_m3"], incumbent_mpa=incumbent, mode=mode)


def _incumbent(rows: pd.DataFrame, age: int) -> float:
    r = rows[rows["Time"] == age]["strength_mpa"]
    return float(r.max()) if len(r) else 0.0


# --------------------------------------------------------------------------- endpoints
@app.get("/facilities")
def facilities():
    st = get_state()
    return {"label": FICTIONAL_LABEL, "facilities": [st.facility(fid) for fid in st.facilities]}


@app.get("/materials")
def materials():
    st = get_state()
    out = []
    for mat, col in MATERIAL_COLUMNS.items():
        per = {}
        for fid in st.facilities:
            m = st.facility(fid)["materials"][mat]
            per[fid] = {k: m[k] for k in ("available", "inventory_kg", "cost_per_kg_usd", "supplier",
                                          "gwp_factor", "bounds_kg_m3")}
        out.append({"material": mat, "dataset_column": col, "unit": "kg/m3",
                    "training_range_kg_m3": st.domain[col], "facilities": per})
    return {"label": FICTIONAL_LABEL, "materials": out}


@app.get("/dataset/metadata")
def dataset_metadata():
    st = get_state()
    m = st.model
    return {"data_card": data_card(st.obs),
            "split": {"split_id": st.split.split_id, "material_class": MATERIAL_CLASS, "seed": st.seed,
                      "n_train_mixes": len(st.split.train_mixes), "n_heldout_mixes": len(st.split.heldout_mixes)},
            "model": {"label": m.label, "backend": m.backend, "version": m.version, "n_train_rows": m.n_train_rows,
                      "fit_seconds": m.fit_seconds, "snapshot_id": st.model_snapshot_id,
                      "fit_mode": getattr(m, "fit_mode", None)},
            "training_domain": st.domain, "router": {"reviewer": st.router.reviewer,
                                                     "model_version": st.router.model_version}}


@app.post("/objectives")
def set_objectives(body: ObjectiveIn):
    st = get_state()
    st.facility(body.facility_id)
    with st.lock:
        st.objective = {"id": f"OBJ-{uuid.uuid4().hex[:8]}", **body.model_dump(), "set_at": _now(),
                        "provenance": {"kind": "assumed", "source": "user-configured objective"}}
        st.baselines_cache = None
    return st.objective


@app.get("/objectives")
def get_objectives():
    return get_state().objective


@app.post("/predict")
def predict(body: PredictIn):
    st = get_state()
    fac = st.facility(body.facility_id)
    bad = [k for k in body.composition if k not in MATERIAL_COLUMNS and k not in MATERIAL_COLUMNS.values()
           and k not in ("Material Source", "Temp (C)")]
    if bad:
        raise HTTPException(422, f"unknown composition keys {bad}; use {list(MATERIAL_COLUMNS)}")
    row = facility_composition_row(body.composition, fac)
    feas = st.feasibility(row, fac)
    model = st.replay.model if (body.use_replay_model and st.replay) else st.model
    preds = _predict_all_ages(model, row, sorted(set(body.ages_days)))
    impact = st.chain.assess(row, fac["id"])
    gwp = st.gwp_both(row, None)
    gwp["darwin_materials_gwp"] = impact["result"] | {"impact_snapshot_id": impact["impact_id"]}
    return {"composition": row, "facility_id": fac["id"], "facility_fictional": True,
            "strength": preds, "strength_provenance": model.provenance(),
            "model_role": "replay session model" if model is not st.model else "startup model (cached)",
            "gwp": gwp, "cost": cost_per_m3(row, fac), "feasibility": feas, "disclaimer": DISCLAIMER}


@app.post("/candidates/generate")
def candidates_generate(body: GenerateIn):
    st = get_state()
    fac = st.facility(body.facility_id)
    o = st.objective
    acc, rej = generate_facility_candidates(fac, st.domain, body.n, body.seed,
                                            restricted_materials=o["restricted_materials"],
                                            replacement_cap=o["replacement_cap"],
                                            planned_volume_m3=o["planned_volume_m3"])
    with st.lock:
        st.candidates = {r["candidate_id"]: r for r in acc}
        st.rejected_candidates = rej
    counts: dict[str, int] = {}
    for r in rej:
        for code in {x["code"] for x in r["feasibility"]["rejection_reasons"]}:
            counts[code] = counts.get(code, 0) + 1
    status_counts: dict[str, int] = {}
    for r in acc + rej:
        s = r["feasibility"]["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    return {"facility_id": fac["id"], "facility_fictional": True, "n_requested": body.n, "seed": body.seed,
            "n_accepted": len(acc), "n_rejected": len(rej), "status_counts": status_counts,
            "rejection_code_counts": dict(sorted(counts.items())), "accepted": acc, "rejected": rej,
            "note": "Generated candidates are prediction-only; they can never be 'measured' in replay."}


@app.post("/experiments/rank")
def experiments_rank(body: RankIn):
    st = get_state()
    if not st.candidates:
        raise HTTPException(409, "no generated candidates; call POST /candidates/generate first")
    ids = body.candidate_ids or list(st.candidates)
    missing = [i for i in ids if i not in st.candidates]
    if missing:
        raise HTTPException(404, f"unknown candidate ids {missing[:5]}")
    recs = [st.candidates[i] for i in ids]
    fac = st.facility(recs[0]["feasibility"]["facility_id"])
    mode = body.mode or st.objective["acquisition_mode"]
    incumbent = _incumbent(st.train_rows, st.objective["target_age_days"])
    ranked = _score(st, st.model, recs, fac, mode, incumbent)[: body.top_k]   # numbers first ...
    reviews = []
    for r in ranked:
        rec = st.candidates[r["candidate_id"]]
        gwp = compute_materials_gwp(rec["composition"], st.chain.factors[fac["id"]])
        reviews.append(st.review_record(r["candidate_id"], rec["composition"], rec["feasibility"],
                                        r["prediction"], gwp))
    decisions = route_ranked(reviews, st.router)                               # ... then review routing
    proposed = []
    for r, d in zip(ranked, decisions):
        r["feasibility_status"] = st.candidates[r["candidate_id"]]["feasibility"]["status"]
        r["review_route"] = d
    for r, d in list(zip(ranked, decisions))[: body.propose_top]:
        rec = st.candidates[r["candidate_id"]]
        eid = st.ledger.propose(
            candidate_id=r["candidate_id"], composition=rec["composition"], facility_id=fac["id"],
            predicted=r["prediction"], acquisition_score=r["score"],
            acquisition_components=r["components"] | {"mode": mode, "weights": r["weights"]},
            constraint_eval=rec["feasibility"], model_snapshot_id=st.model_snapshot_id,
            provenance={"kind": "computed", "source": "POST /experiments/rank", "candidate": rec["provenance"],
                        "measurable": False})
        imp = st.chain.assess(rec["composition"], fac["id"])
        st.chain.link_experiment(eid, imp["impact_id"])
        st.ledger.add_router_decision(d, eid)
        proposed.append(eid)
    for d in decisions[body.propose_top:]:
        st.ledger.add_router_decision(d)
    return {"mode": mode, "weights": MODE_WEIGHTS[mode], "objective": st.objective, "incumbent_mpa": incumbent,
            "n_scored": len(recs), "ranked": ranked, "proposed_experiment_ids": proposed,
            "router": {"reviewer": st.router.reviewer, "model_version": st.router.model_version,
                       "note": "router runs after ranking and cannot change scores or order"}}


@app.post("/experiments/{experiment_id}/transition")
def experiment_transition(experiment_id: str, body: TransitionIn):
    st = get_state()
    try:
        return st.ledger.transition(experiment_id, body.to_status, actor=body.actor, note=body.note)
    except KeyError:
        raise HTTPException(404, experiment_id)
    except InvalidTransition as e:
        raise HTTPException(409, str(e))


@app.post("/factors/update")
def factors_update(body: FactorUpdateIn):
    st = get_state()
    if body.facility_id not in st.facilities or body.material not in MATERIAL_COLUMNS:
        raise HTTPException(404, "unknown facility or material")
    with st.lock:
        return st.chain.update_factor(body.facility_id, body.material, value=body.value, source=body.source,
                                      geography=body.geography, version=body.version,
                                      published_or_assumed=body.published_or_assumed)


# ---- replay ---------------------------------------------------------------
def _session() -> ReplaySession:
    st = get_state()
    if st.replay is None:
        raise HTTPException(409, "no replay session; call POST /replay/start")
    return st.replay


@app.post("/replay/start")
def replay_start(body: ReplayStartIn):
    st = get_state()
    fac = st.facility(body.facility_id)
    oracle = HeldOutOracle(st.obs, st.split)
    eligible, excluded = [], []
    for r in oracle.pool_features().to_dict("records"):
        base = check_feasibility(r)
        feas = st.feasibility(r, fac)
        if base or (body.apply_facility_constraints and feas["status"] == OUTSIDE_SUPPORTED_DOMAIN):
            excluded.append({"mix": r["Mix Name"], "p0_reasons": base,
                             "facility_reasons": feas["rejection_reasons"]})
        else:
            eligible.append(r["Mix Name"])
    if not eligible:
        raise HTTPException(409, {"error": "no eligible held-out mixtures under these constraints",
                                  "excluded": excluded})
    budget = body.budget or fac["lab_budget"]["max_experiments"]
    with st.lock:
        st.replay = ReplaySession(f"REPLAY-{uuid.uuid4().hex[:8]}", oracle, st.model, st.train_rows.copy(),
                                  fac["id"], body.apply_facility_constraints, budget, eligible, excluded)
    s = st.replay
    return {"session_id": s.id, "split_id": st.split.split_id, "facility_id": fac["id"],
            "facility_fictional": True, "pool_size": len(st.split.heldout_mixes), "n_eligible": len(eligible),
            "n_excluded": len(excluded), "excluded": excluded, "budget_experiments": budget,
            "lab_cost_per_experiment_usd": fac["lab_budget"]["cost_per_experiment_usd"],
            "model_version": s.model.version,
            "note": "Pool = real held-out mixtures; labels stay inside the oracle until /replay/reveal."}


@app.post("/replay/next")
def replay_next(body: ReplayNextIn):
    st = get_state()
    s = _session()
    with st.lock:
        open_ = [e for e in st.ledger.experiments(s.id) if e["status"] in ("PROPOSED", APPROVED, AWAITING_RESULT)]
        if open_:
            raise HTTPException(409, f"experiment {open_[0]['id']} is still {open_[0]['status']}; reveal or reject it first")
        if len(s.steps) >= s.budget:
            raise HTTPException(409, f"lab budget exhausted ({s.budget} experiments)")
        pool = s.oracle.pool_features()
        pool = pool[pool["Mix Name"].isin(s.eligible)]
        if pool.empty:
            raise HTTPException(409, "eligible pool exhausted")
        fac = st.facility(s.facility_id)
        recs = [{"candidate_id": r["Mix Name"], "composition": {c: r[c] for c in FEATURE_COLUMNS}}
                for r in pool.to_dict("records")]
        mode = body.mode or st.objective["acquisition_mode"]
        incumbent = _incumbent(s.train_rows, st.objective["target_age_days"])
        ranked = _score(st, s.model, recs, fac, mode, incumbent)
        if body.mix_name:
            pick = next((r for r in ranked if r["candidate_id"] == body.mix_name), None)
            if pick is None:
                raise HTTPException(404, f"{body.mix_name} is not in the eligible unrevealed pool")
        else:
            pick = ranked[0]
        comp = next(r["composition"] for r in recs if r["candidate_id"] == pick["candidate_id"])
        feas = st.feasibility(comp, fac)
        preds = _predict_all_ages(s.model, comp, AGES)                         # BEFORE reveal
        p_target = next(p for p in preds if p["age_days"] == st.objective["target_age_days"])
        predicted = {"by_age": preds, "target_age": p_target, "model_version": s.model.version,
                     "provenance": s.model.provenance(), "made_before_reveal": True}
        snap = st.ledger.add_model_snapshot(s.model.label, s.model.version,
                                            {"session": s.id, "fit_mode": getattr(s.model, "fit_mode", None),
                                             "n_train_rows": s.model.n_train_rows})
        eid = st.ledger.propose(
            candidate_id=pick["candidate_id"], composition=comp, facility_id=fac["id"], predicted=predicted,
            acquisition_score=pick["score"],
            acquisition_components=pick["components"] | {"mode": mode, "weights": pick["weights"],
                                                         "rank": pick["rank"], "n_ranked": len(ranked)},
            constraint_eval=feas, model_snapshot_id=snap, session_id=s.id,
            provenance={"kind": "computed", "source": "replay/next over real held-out pool",
                        "split_id": st.split.split_id, "chosen_by": "user" if body.mix_name else "acquisition"})
        imp = st.chain.assess(comp, fac["id"])
        st.chain.link_experiment(eid, imp["impact_id"])
        decision = st.router.classify(st.review_record(pick["candidate_id"], comp, feas, pick["prediction"],
                                                       imp["result"]))
        st.ledger.add_router_decision(decision, eid)
        if body.auto_approve:
            st.ledger.transition(eid, APPROVED, actor="replay-operator (auto-approve)",
                                 note=f"review route {decision['route']} ({decision['reviewer']})")
            st.ledger.transition(eid, AWAITING_RESULT, actor="replay-operator", note="sent to (replayed) lab")
        s.steps.append({"experiment_id": eid, "mix": pick["candidate_id"]})
    return {"experiment": st.ledger.get(eid), "acquisition": pick, "top5": ranked[:5], "review_route": decision,
            "gwp": {"darwin_materials_gwp": imp["result"], "impact_snapshot_id": imp["impact_id"]},
            "budget_remaining": s.budget - len(s.steps)}


@app.post("/replay/reveal")
def replay_reveal(body: RevealIn):
    st = get_state()
    s = _session()
    with st.lock:
        try:
            exp = st.ledger.get(body.experiment_id)
        except KeyError:
            raise HTTPException(404, body.experiment_id)
        if exp["session_id"] != s.id:
            raise HTTPException(409, "experiment does not belong to the active replay session")
        if exp["status"] != AWAITING_RESULT:
            raise HTTPException(409, f"experiment is {exp['status']}; only AWAITING_RESULT can be revealed")
        rows = s.oracle.reveal(exp["candidate_id"], step=len(s.oracle.revealed) + 1)
        mrows = [{"age_days": int(r["Time"]), "strength_mpa": round(float(r["strength_mpa"]), 3),
                  "strength_std_mpa": round(float(r["strength_std_mpa"]), 3),
                  "strength_psi": float(r["Strength (Mean)"]), "strength_std_psi": float(r["Strength (Std)"]),
                  "n_cylinders": int(r["# of measurements"])} for r in rows.sort_values("Time").to_dict("records")]
        age = st.objective["target_age_days"]
        at = next((m for m in mrows if m["age_days"] == age), None)
        measured = {"rows": mrows, "strength_28d_mpa": next((m["strength_mpa"] for m in mrows if m["age_days"] == 28), None),
                    "provenance": {"kind": "measured", "source": "BOxCrete CSV held-out row via oracle.reveal",
                                   "split_id": st.split.split_id, "mix": exp["candidate_id"]}}
        exp = st.ledger.record_measurement(exp["id"], measured, actor="replay-oracle")
    pred = exp["predicted"]["target_age"]
    comparison = None
    if at is not None:
        err = at["strength_mpa"] - pred["mean_mpa"]
        comparison = {"age_days": age, "predicted_mpa": pred["mean_mpa"], "measured_mpa": at["strength_mpa"],
                      "error_mpa": round(err, 3), "z": round(err / pred["predictive_std_mpa"], 3),
                      "within_pi95": pred["pi95_mpa"][0] <= at["strength_mpa"] <= pred["pi95_mpa"][1],
                      "met_target": at["strength_mpa"] >= st.objective["target_strength_mpa"],
                      "provenance": {"kind": "computed", "source": "measured minus pre-reveal prediction"}}
    fac = st.facility(s.facility_id)
    feas = exp["constraint_eval"]
    decision = st.router.classify(st.review_record(exp["candidate_id"], exp["composition"], feas,
                                                   {"mean_mpa": pred["mean_mpa"], "std_mpa": pred["predictive_std_mpa"]},
                                                   compute_materials_gwp(exp["composition"], st.chain.factors[fac["id"]]),
                                                   measured))
    st.ledger.add_router_decision(decision, exp["id"])
    return {"experiment": exp, "comparison": comparison, "post_measurement_review": decision,
            "note": "Negative results (target missed / outside PI) are kept in the ledger."}


@app.post("/replay/update")
def replay_update():
    st = get_state()
    s = _session()
    with st.lock:
        approved = st.ledger.approved_measurements_for_training(s.id)
        new = [e for e in approved if e["id"] not in s.trained_experiments]
        if not new:
            raise HTTPException(409, "no new APPROVED+MEASURED experiments to train on")
        add = []
        for e in new:
            for m in e["measured"]["rows"]:
                add.append({"Mix Name": e["candidate_id"], **{c: e["composition"][c] for c in FEATURE_COLUMNS},
                            "Time": m["age_days"], "Strength (Mean)": m["strength_psi"],
                            "Strength (Std)": m["strength_std_psi"], "strength_mpa": m["strength_mpa"],
                            "strength_std_mpa": m["strength_std_mpa"], "material_class": MATERIAL_CLASS})
        add_df = pd.DataFrame(add)
        # BOxCrete's loader requires a finite GWP column (it drops NaN rows); the
        # strength GP never reads it. Fill it from BOxCrete's own GWP LinearModel
        # (typed computed) rather than copying anything from the oracle.
        if st.gwp_model is not None:
            add_df["GWP"] = st.gwp_model.predict(add)[0]
            add_df["GWP_provenance"] = "computed: BOxCrete GWP LinearModel (loader placeholder, unused by strength GP)"
        train = pd.concat([s.train_rows, add_df], ignore_index=True)
        test_rows = s.oracle._evaluation_rows()               # metrics only (unrevealed held-out rows)
        before = evaluate_model(s.model, test_rows) if len(test_rows) else None
        if isinstance(s.model, BoxcreteStrengthModel):
            refit = BoxcreteStrengthModel(st.seed, st.split.split_id, warm_start_from=s.model).fit(train)
        else:
            refit = make_model(s.model.backend, st.seed, st.split.split_id).fit(train)
        after = evaluate_model(refit, test_rows) if len(test_rows) else None
        old_version = s.model.version
        s.model, s.train_rows = refit, train
        s.trained_experiments += [e["id"] for e in new]
        snap = st.ledger.add_model_snapshot(refit.label, refit.version,
                                            {"session": s.id, "fit_mode": getattr(refit, "fit_mode", None),
                                             "parent_version": old_version, "added_experiments": [e["id"] for e in new],
                                             "n_train_rows": refit.n_train_rows, "fit_seconds": refit.fit_seconds})
        upd = {"model_snapshot_id": snap, "old_version": old_version, "new_version": refit.version,
               "fit_mode": getattr(refit, "fit_mode", None), "fit_seconds": refit.fit_seconds,
               "added_experiments": [e["id"] for e in new], "added_rows": len(add),
               "test_mae_before_mpa": before and before["mae_mpa"], "test_mae_after_mpa": after and after["mae_mpa"],
               "test_pi95_coverage_before": before and before["pi95_coverage"],
               "test_pi95_coverage_after": after and after["pi95_coverage"], "n_test_rows": int(len(test_rows)),
               "metrics_provenance": {"kind": "computed", "source": "unrevealed held-out rows (evaluator only)"}}
        s.updates.append(upd)
    return upd


@app.get("/history")
def history(session_id: str | None = None):
    st = get_state()
    exps = st.ledger.experiments(session_id)
    return {"experiments": exps, "transitions": st.ledger.transitions_for(),
            "model_snapshots": st.ledger.model_snapshots(), "router_decisions": st.ledger.router_decisions(),
            "replay_session": None if st.replay is None else
            {"id": st.replay.id, "steps": st.replay.steps, "updates": st.replay.updates,
             "budget": st.replay.budget, "facility_id": st.replay.facility_id}}


@app.post("/baselines/compare")
def baselines_compare(body: CompareIn = CompareIn()):
    st = get_state()
    if st.baselines_cache is None:
        held = st.obs[st.obs["Mix Name"].isin(st.split.heldout_mixes)]
        st.baselines_cache = evaluate_with_baselines(st.model, st.train_rows, held)
    out = {"heldout_evaluation": st.baselines_cache, "split_id": st.split.split_id,
           "note": "Startup model vs baselines on the full held-out split (whole unseen mixtures)."}
    s = st.replay
    if body.include_replay_vs_random and s and s.oracle.revealed:
        k = len(s.oracle.revealed)
        acq_curve, best = [], float("-inf")
        for e in st.ledger.experiments(s.id):
            if e["status"] == "MEASURED":
                best = max(best, e["measured"]["strength_28d_mpa"] or float("-inf"))
                acq_curve.append(round(best, 3))
        rnd = run_random_replay(st.obs, MATERIAL_CLASS, st.seed, k)
        out["replay_vs_random"] = {
            "k": k, "acquisition_best_28d_curve": acq_curve, "random_best_28d_curve": rnd["best_curve"],
            "random_order": rnd["order"], "train_best_28d_mpa": best_28d(st.train_rows),
            "caveat": "single seed; random arm uses the P0 eligibility pool (no facility filter)",
            "provenance": {"kind": "computed", "source": "replay ledger vs seeded random replay"}}
    return out


@app.get("/report/export")
def report_export():
    st = get_state()
    exps = st.ledger.experiments()
    negatives = []
    for e in exps:
        if e["status"] == "REJECTED":
            negatives.append({"experiment_id": e["id"], "kind": "rejected experiment"})
        elif e["status"] == "MEASURED":
            m = e["measured"]
            pt = e["predicted"].get("target_age", {})
            age = pt.get("age_days", 28)
            meas = next((r["strength_mpa"] for r in m["rows"] if r["age_days"] == age), None)
            if meas is not None and meas < st.objective["target_strength_mpa"]:
                negatives.append({"experiment_id": e["id"], "kind": "measured below target",
                                  "measured_mpa": meas, "predicted_mpa": pt.get("mean_mpa")})
    return {
        "generated_at": _now(), "darwin_version": __version__,
        "disclaimers": [DISCLAIMER, FICTIONAL_LABEL, BOUNDARY["statement"],
                        "Review routing labels are " + ("live Jev outputs" if st.router.reviewer == "jev"
                                                        else "SIMULATED — no live Jev call")],
        "dataset": dataset_metadata(), "objective": st.objective, "facilities": facilities()["facilities"],
        "environmental_boundary": BOUNDARY, "gwp_fields_note": NOT_SUMMED_NOTE,
        "review_taxonomy": TAXONOMY,
        "ledger": {"experiments": exps, "transitions": st.ledger.transitions_for(),
                   "model_snapshots": st.ledger.model_snapshots(), "router_decisions": st.ledger.router_decisions(),
                   "calc_snapshots": st.ledger.calc_snapshots()},
        "baselines": st.baselines_cache,
        "replay": None if st.replay is None else {"id": st.replay.id, "steps": st.replay.steps,
                                                  "updates": st.replay.updates, "excluded": st.replay.excluded},
        "rejected_generated_candidates": st.rejected_candidates,
        "negative_results": negatives,
        "citation": "BOxCrete (facebookresearch/SustainableConcrete @116ad28, MIT) — baten2026boxcrete",
    }


# --------------------------------------------------------------------------- Phase 3 UI support
# ADDITIVE ONLY (Phase 3 frontend). These routes do not change any existing
# endpoint's behaviour. They exist because the UI strictly needs:
#   GET  /ui/space[?use_replay_model=true] one batch of model predictions + GWP for every generated candidate
#                               (accepted AND rejected) plus measured TRAIN mixtures, with Pareto
#                               flags; replaces hundreds of /predict calls for the scatter.
#   POST /ui/facility_overrides what-if edits to a FICTIONAL facility (availability, inventory,
#                               price, w/b bounds, lab budget); no prior endpoint could change these.
#   POST /ui/facility_reset     restore the fixture values.
# Held-out labels are never exposed here: measured points come from the train split only.
class FacilityMaterialOverride(BaseModel):
    available: bool | None = None
    inventory_kg: float | None = Field(None, ge=0)
    clear_inventory_limit: bool = False
    cost_per_kg_usd: float | None = Field(None, ge=0)


class FacilityOverrideIn(BaseModel):
    facility_id: str
    materials: dict[str, FacilityMaterialOverride] = {}
    w_b: list[float] | None = None
    max_cement_replacement_fraction: float | None = Field(None, ge=0, le=1)
    max_experiments: int | None = Field(None, ge=1, le=500)
    cost_per_experiment_usd: float | None = Field(None, ge=0)
    note: str = ""


class FacilityResetIn(BaseModel):
    facility_id: str


UI_OVERRIDES: dict[str, list[dict]] = {}


def _pareto(points: list[tuple[float, float]]) -> list[bool]:
    """Non-dominated set for (minimise gwp, maximise strength)."""
    out = []
    for i, (g, s) in enumerate(points):
        out.append(not any((g2 <= g and s2 >= s) and (g2 < g or s2 > s)
                           for j, (g2, s2) in enumerate(points) if j != i))
    return out


@app.get("/ui/space")
def ui_space(facility_id: str | None = None, use_replay_model: bool = False):
    st = get_state()
    fac = st.facility(facility_id)
    model = st.replay.model if (use_replay_model and st.replay) else st.model
    age = st.objective["target_age_days"]
    factors = st.chain.factors[fac["id"]]
    cands = []
    gen = [(r, True) for r in st.candidates.values()] + [(r, False) for r in st.rejected_candidates]
    if gen:
        feats = features_frame([r for r, _ in gen])
        mu, sd = model.predict(feats, age_days=age, latent=True)
        for i, (r, acc) in enumerate(gen):
            g = compute_materials_gwp(r["composition"], factors)
            cands.append({"candidate_id": r["candidate_id"], "accepted": acc,
                          "status": r["feasibility"]["status"],
                          "rejection_codes": sorted({x["code"] for x in r["feasibility"]["rejection_reasons"]}),
                          "pred_mean_mpa": round(float(mu[i]), 3), "pred_std_mpa": round(float(sd[i]), 3),
                          "gwp_kgco2e_m3": g["value"], "gwp_complete": g["complete"],
                          "facility_id": r["feasibility"]["facility_id"]})
        acc_idx = [i for i, c in enumerate(cands) if c["accepted"]]
        flags = _pareto([(cands[i]["gwp_kgco2e_m3"], cands[i]["pred_mean_mpa"]) for i in acc_idx])
        for i, f in zip(acc_idx, flags):
            cands[i]["pareto"] = f
    measured = []
    for name, grp in st.train_rows.groupby("Mix Name", sort=True):
        row = {c: float(grp.iloc[0][c]) for c in FEATURE_COLUMNS}
        by_age = [{"age_days": int(r["Time"]), "strength_mpa": round(float(r["strength_mpa"]), 3),
                   "strength_std_mpa": round(float(r["strength_std_mpa"]), 3)}
                  for r in grp.sort_values("Time").to_dict("records")]
        at = next((b["strength_mpa"] for b in by_age if b["age_days"] == age), None)
        g = compute_materials_gwp(row, factors)
        measured.append({"mix": name, "composition": row, "by_age": by_age, "strength_at_target_age_mpa": at,
                         "gwp_kgco2e_m3": g["value"], "gwp_complete": g["complete"]})
    have = [m for m in measured if m["strength_at_target_age_mpa"] is not None]
    for m, f in zip(have, _pareto([(m["gwp_kgco2e_m3"], m["strength_at_target_age_mpa"]) for m in have])):
        m["pareto"] = f
    return {"facility_id": fac["id"], "facility_fictional": True, "target_age_days": age,
            "objective": st.objective, "model": {"label": model.label, "version": model.version,
                                                 "role": "replay session model" if model is not st.model
                                                 else "startup model (cached)"},
            "candidates": cands, "measured_train_mixtures": measured,
            "provenance": {"candidates": {"kind": "predicted", "source": "model latent posterior at target age (see model.role)"},
                           "measured": {"kind": "measured", "source": "BoxCrete CSV TRAIN split rows only"},
                           "gwp": {"kind": "computed", "source": f"darwin_materials_gwp with {fac['id']} factors "
                                   "(partial boundary; assumed fictional factors)"},
                           "pareto": {"kind": "computed", "source": "non-dominated (min GWP, max strength), "
                                      "computed separately for predicted candidates and measured mixtures"}}}


@app.post("/ui/facility_overrides")
def ui_facility_overrides(body: FacilityOverrideIn):
    st = get_state()
    st.facility(body.facility_id)
    bad = [m for m in body.materials if m not in MATERIAL_COLUMNS]
    if bad:
        raise HTTPException(422, f"unknown materials {bad}")
    if body.w_b is not None and (len(body.w_b) != 2 or not 0 < body.w_b[0] < body.w_b[1] < 2):
        raise HTTPException(422, "w_b must be [lo, hi] with 0 < lo < hi < 2")
    with st.lock:
        f = st.facilities[body.facility_id]
        changes = []
        for mat, o in body.materials.items():
            rec = f["materials"][mat]
            for k in ("available", "inventory_kg", "cost_per_kg_usd"):
                v = getattr(o, k)
                if v is not None and v != rec[k]:
                    changes.append({"field": f"materials.{mat}.{k}", "old": rec[k], "new": v})
                    rec[k] = v
            if o.clear_inventory_limit and rec["inventory_kg"] is not None:
                changes.append({"field": f"materials.{mat}.inventory_kg", "old": rec["inventory_kg"], "new": None})
                rec["inventory_kg"] = None
        mb = f["mixture_bounds"]
        if body.w_b is not None and list(body.w_b) != mb["w_b"]:
            changes.append({"field": "mixture_bounds.w_b", "old": mb["w_b"], "new": list(body.w_b)})
            mb["w_b"] = list(body.w_b)
        if body.max_cement_replacement_fraction is not None and \
                body.max_cement_replacement_fraction != mb["max_cement_replacement_fraction"]:
            changes.append({"field": "mixture_bounds.max_cement_replacement_fraction",
                            "old": mb["max_cement_replacement_fraction"], "new": body.max_cement_replacement_fraction})
            mb["max_cement_replacement_fraction"] = body.max_cement_replacement_fraction
        lb = f["lab_budget"]
        for k, v in (("max_experiments", body.max_experiments), ("cost_per_experiment_usd", body.cost_per_experiment_usd)):
            if v is not None and v != lb[k]:
                changes.append({"field": f"lab_budget.{k}", "old": lb[k], "new": v})
                lb[k] = v
        entry = {"at": _now(), "facility_id": body.facility_id, "changes": changes, "note": body.note,
                 "provenance": {"kind": "assumed", "source": "user what-if override of a FICTIONAL fixture"}}
        if changes:
            UI_OVERRIDES.setdefault(body.facility_id, []).append(entry)
            st.baselines_cache = None
    return {"applied": entry, "overrides": UI_OVERRIDES.get(body.facility_id, []),
            "facility": st.facility(body.facility_id),
            "note": "Feasibility/acquisition are NOT recomputed by this call; regenerate + rank candidates."}


@app.post("/ui/facility_reset")
def ui_facility_reset(body: FacilityResetIn):
    st = get_state()
    st.facility(body.facility_id)
    fresh = {f["id"]: f for f in list_facilities()}[body.facility_id]
    with st.lock:
        st.facilities[body.facility_id] = fresh
        UI_OVERRIDES.pop(body.facility_id, None)
    return {"facility": st.facility(body.facility_id),
            "note": "Fixture values restored. GWP factor versions are immutable ledger snapshots and are NOT reset."}

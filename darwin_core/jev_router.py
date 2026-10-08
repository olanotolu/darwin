"""P1: Jev decision router — classifies the REVIEW NEED of already-ranked
candidates. It never sees or changes numerical optimisation, acquisition
scores or rankings (``route_ranked`` verifies the input is unchanged).

Live adapter: built against the TypeSafe/Jev OpenAPI contract observed on
2026-10-08 at https://api.typesafe.ai/redoc -> /openapi.json (title
"TypeSafe", version 0.2.0; snapshot in docs/jev_openapi_snapshot.json):

* auth: HTTP Bearer — ``Authorization: Bearer <API_KEY>``
* ``GET  /v1/models``    -> ModelMetadataList {models: [{name, description, release_date}]}
* ``POST /v1/systemone`` <- SystemOneRequest {state, model, questions: {name: Question}}
                         -> SystemOneResponse {model, answers: {name: Answer}, usage}
  Question/Answer are discriminated on ``type``: noul | choice | score.
  ChoiceQuestion {type:"choice", instructions?, criteria: {choice: description}}
  ChoiceAnswer   {type:"choice", choice, confidence, probabilities}

No JEV_API_KEY exists on this machine, so the live adapter has never been
called; ``DeterministicFallbackRouter`` is used instead and every decision it
emits is labeled ``SIMULATED — no live Jev call``.
"""

from __future__ import annotations

import copy
import json
import os

NORMAL_REVIEW = "NORMAL_REVIEW"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
DOMAIN_SHIFT = "DOMAIN_SHIFT"
CONFLICTING_MEASUREMENTS = "CONFLICTING_MEASUREMENTS"
EXPERT_ESCALATION = "EXPERT_ESCALATION"
TAXONOMY = {
    NORMAL_REVIEW: "Evidence is complete and in-domain; standard reviewer sign-off is sufficient.",
    MISSING_EVIDENCE: "Required evidence is absent (e.g. a missing emission factor, missing uncertainty, "
                      "incomplete provenance).",
    DOMAIN_SHIFT: "The candidate lies outside the data the model was trained on or the facility differs "
                  "from the data's material sources; predictions are extrapolations.",
    CONFLICTING_MEASUREMENTS: "Measured results disagree with the prediction or with each other beyond "
                              "stated uncertainty.",
    EXPERT_ESCALATION: "Multiple risk signals or very high uncertainty; a domain expert must review.",
}
FALLBACK_LABEL = "SIMULATED — no live Jev call"
FALLBACK_VERSION = "darwin-fallback-rules-v1"
JEV_BASE_URL = "https://api.typesafe.ai"
JEV_DEFAULT_MODEL = "jev-latest"   # example alias from the OpenAPI schema; resolve via GET /v1/models

# fallback thresholds (deterministic, documented)
REL_UNCERTAINTY_ESCALATE = 0.25    # predicted std / mean at target age
Z_CONFLICT = 3.0                   # |measured - predicted| / predicted std
REPLICATE_CV_CONFLICT = 0.15       # replicate std / mean at any age


def review_summary(candidate: dict) -> dict:
    """The only view of a candidate a router receives: review-relevant
    evidence, no acquisition score, no rank."""
    keys = ("candidate_id", "facility_id", "facility_fictional", "feasibility_status", "validation_flags",
            "rejection_reasons", "prediction", "gwp", "measured", "material_source_in_training")
    return {k: copy.deepcopy(candidate.get(k)) for k in keys}


class DeterministicFallbackRouter:
    reviewer = "deterministic-fallback"
    model_version = FALLBACK_VERSION

    def classify(self, candidate: dict) -> dict:
        s = review_summary(candidate)
        triggered: list[dict] = []
        pred = s.get("prediction") or {}
        mean, std = pred.get("mean_mpa"), pred.get("std_mpa")

        gwp = s.get("gwp") or {}
        if mean is None or std is None:
            triggered.append({"route": MISSING_EVIDENCE, "code": "PREDICTION_OR_UNCERTAINTY_MISSING"})
        if gwp.get("missing_factors"):
            triggered.append({"route": MISSING_EVIDENCE, "code": "GWP_FACTOR_MISSING",
                              "detail": gwp["missing_factors"]})
        if gwp.get("incomplete_provenance"):
            triggered.append({"route": MISSING_EVIDENCE, "code": "FACTOR_PROVENANCE_INCOMPLETE"})

        flags = s.get("validation_flags") or []
        if flags:
            triggered.append({"route": DOMAIN_SHIFT, "code": "OUTSIDE_TRAINING_RANGE",
                              "detail": sorted({f["field"] for f in flags})})
        if s.get("material_source_in_training") is False:
            triggered.append({"route": DOMAIN_SHIFT, "code": "MATERIAL_SOURCE_NOT_IN_TRAINING"})
        if s.get("feasibility_status") == "OUTSIDE_SUPPORTED_DOMAIN":
            triggered.append({"route": EXPERT_ESCALATION, "code": "RANKED_DESPITE_OUTSIDE_DOMAIN"})

        meas = s.get("measured") or {}
        m28 = meas.get("strength_28d_mpa")
        if m28 is not None and mean is not None and std:
            z = abs(m28 - mean) / std
            if z > Z_CONFLICT:
                triggered.append({"route": CONFLICTING_MEASUREMENTS, "code": "MEASUREMENT_VS_PREDICTION",
                                  "detail": {"z": round(z, 2)}})
        for r in meas.get("rows") or []:
            if r.get("strength_mpa") and r.get("strength_std_mpa") is not None:
                cv = r["strength_std_mpa"] / r["strength_mpa"]
                if cv > REPLICATE_CV_CONFLICT:
                    triggered.append({"route": CONFLICTING_MEASUREMENTS, "code": "REPLICATE_SPREAD",
                                      "detail": {"age_days": r.get("age_days"), "cv": round(cv, 3)}})

        high_unc = bool(mean and std is not None and mean > 0 and std / mean > REL_UNCERTAINTY_ESCALATE)
        if high_unc:
            triggered.append({"route": EXPERT_ESCALATION, "code": "HIGH_RELATIVE_UNCERTAINTY",
                              "detail": {"rel_std": round(std / mean, 3)}})

        routes = {t["route"] for t in triggered}
        if not routes:
            route = NORMAL_REVIEW
        elif EXPERT_ESCALATION in routes or len(routes) >= 2:
            route = EXPERT_ESCALATION
        else:
            route = routes.pop()
        return {"candidate_id": s.get("candidate_id"), "route": route, "route_description": TAXONOMY[route],
                "triggered": triggered, "reviewer": self.reviewer, "label": FALLBACK_LABEL,
                "adapter_model_version": self.model_version, "live_call": False,
                "numeric_fields_modified": False}


class JevHttpAdapter:
    """Server-side adapter for the observed TypeSafe/Jev contract. Requires an
    API key; never instantiated without one (see ``get_router``)."""

    reviewer = "jev"

    def __init__(self, api_key: str, *, base_url: str = JEV_BASE_URL, model: str = JEV_DEFAULT_MODEL,
                 transport=None, timeout: float = 20.0):
        import httpx
        if not api_key:
            raise ValueError("JevHttpAdapter requires an API key")
        self.model = model
        self.model_version = model
        # trust_env=False: httpx crashes parsing IPv6 entries in this sandbox's
        # no_proxy; the egress proxy (if any) is passed explicitly instead.
        proxy = None if transport else (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport, proxy=proxy,
                                    trust_env=False, headers={"Authorization": f"Bearer {api_key}"})

    def list_models(self) -> list[dict]:
        r = self._client.get("/v1/models")
        r.raise_for_status()
        return r.json()["models"]

    def build_request(self, candidate: dict) -> dict:
        return {
            "model": self.model,
            "state": json.dumps(review_summary(candidate), sort_keys=True, default=str),
            "questions": {
                "review_route": {
                    "type": "choice",
                    "instructions": ("Classify what kind of human review this already-ranked concrete "
                                     "mixture candidate needs before a lab experiment. Do not assess "
                                     "performance or rank; only the review need."),
                    "criteria": dict(TAXONOMY),
                },
            },
        }

    def classify(self, candidate: dict) -> dict:
        req = self.build_request(candidate)
        r = self._client.post("/v1/systemone", json=req)
        r.raise_for_status()
        body = r.json()
        ans = body["answers"]["review_route"]
        if ans.get("type") != "choice" or ans.get("choice") not in TAXONOMY:
            raise ValueError(f"unexpected Jev answer: {ans}")
        return {"candidate_id": candidate.get("candidate_id"), "route": ans["choice"],
                "route_description": TAXONOMY[ans["choice"]], "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities"), "reviewer": self.reviewer,
                "label": "live Jev call", "adapter_model_version": body.get("model"),
                "usage": body.get("usage"), "live_call": True, "numeric_fields_modified": False}


def get_router(env: dict | None = None):
    env = os.environ if env is None else env
    key = env.get("JEV_API_KEY")
    if key:
        return JevHttpAdapter(key, model=env.get("JEV_MODEL", JEV_DEFAULT_MODEL))
    return DeterministicFallbackRouter()


def route_ranked(ranked: list[dict], router) -> list[dict]:
    """Classify review needs for an ALREADY-RANKED list. The ranking is
    frozen before routing and verified unchanged afterwards."""
    frozen = json.dumps(ranked, sort_keys=True, default=str)
    out = [router.classify(c) for c in ranked]
    if json.dumps(ranked, sort_keys=True, default=str) != frozen:
        raise RuntimeError("router modified ranked candidates — forbidden")
    return out

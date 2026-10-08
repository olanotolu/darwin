# DARWIN — 3-minute engineering demo script

Prereqs: backend running (`uvicorn darwin_core.api:app`, ~75 s startup), frontend on
http://localhost:3000. Goal: show a real scientific loop, not slides.

## 0:00 — The problem (Discovery Lab)
Open DARWIN. Show the two facilities (banner: **FICTIONAL** fixtures).
Say: "We've gotten good at calculating the environmental impact of materials we've
already made. I wanted to see what happens if that data helps decide which material
we should discover next."

## 0:25 — Define the world
Select **Hudson Materials**. Set 28-day target **40 MPa**, a carbon budget, experiment
budget **10**. Show the design-space scatter: solid dots = measured training mixtures,
hollow = predicted candidates, grey = rejected (hover shows machine-readable rejection
codes like `WB_RATIO_OUT_OF_BOUNDS`). Nothing here is labeled construction-ready.

## 0:50 — Let the scientist reason
Run the acquisition engine (balanced mode). The inspector shows the proposed experiment:
exact composition, predicted 28-day strength ± uncertainty, carbon estimate with factor
provenance, feasibility status, acquisition score **with its components**
(p_meet_target, EI, uncertainty, carbon, budget penalty) — deterministic, seeded,
reproducible. Explain: it optimizes expected information gain under constraints, not
just lowest carbon.

## 1:15 — The revealing moment (Experiment Replay)
Show the model's prediction and 95% interval **before** revealing. Press "Reveal
held-out laboratory result": a genuine held-out BOxCrete measurement appears
(e.g. predicted 87.7 → measured 79.6 MPa, inside the interval). Show prediction error
and z-score. Press "Update scientific model": new model version, refit in ~3 s,
predictions recomputed. The search space visibly changes. The model knew X before,
now knows X + one real observation — inspectable in History.

## 1:50 — Introduce reality (Factory Reality)
Disable slag (or cut its inventory to near zero). Watch DARWIN invalidate affected
candidates and re-rank within the supported space. A scientifically attractive
candidate that isn't manufacturable at Hudson is rejected with reasons — discovering
a formulation and manufacturing it are different problems.

## 2:15 — Prove the methodology (Scientific Report)
Show the baselines panel: EI acquisition vs random selection over 5 seeded replay
trials (104.3 ± 13.8 vs 84.0 ± 21.5 MPa best-observed; EI found the pool max 5/5,
random 1/5). Then the honest caveats, stated out loud: n=5 is small; the pool max
sits in the held-out set 4/5 times so part of the win is "pick what the model rates
highest"; a plain ridge baseline beats the GP on concrete (MAE 4.05 vs 5.17); the 95%
intervals under-cover (~0.86). Show the experiment ledger: every proposal, prediction,
reveal, and model version, all timestamped.

## 2:40 — Show provenance
Open a mixture's carbon calculation: each material → quantity → factor → source,
version, geography, published-vs-assumed. Materials-only partial boundary, explicitly
**not** an A1–A3 EPD. Export the evidence packet (downloads JSON: objective, dataset
provenance, model/code version, predictions vs measurements, constraint evaluations,
baselines, limitations).

Close: "Pathways can already tell manufacturers what their materials cost the planet.
I wanted to explore whether the same underlying data infrastructure could eventually
help manufacturers discover better materials."

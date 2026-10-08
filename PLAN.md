# DARWIN — PLAN.md (Phase 0, planning only)

> Phase 0 completed 2026-10-08. Every BOxCrete fact below was **observed** on 2026-10-08, first by streaming files from `raw.githubusercontent.com` / `api.github.com`, then confirmed against a shallow clone at `/tmp/boxcrete-src` (commit `116ad287351bee2fa4742ca5d8ce1b7d44db44c9`, committed 2026-09-29). Nothing here comes from memory.

## Context
DARWIN is an autonomous concrete-discovery lab built as a layer on top of Meta's BOxCrete, for a Pathways interview demo. It adds factory-constrained discovery, sequential acquisition-driven learning, evidence-aware model updating and environmental provenance. The hard rules: no invented measurements, mixture-level splits, mortar and concrete kept separate, every value typed (measured/predicted/assumed/computed), deterministic feasibility and acquisition, Jev limited to a review classifier, and negative results kept.

---

## (a) Environment inventory (observed)

| Item | Status |
|---|---|
| Python | **3.12.3** (system, pip 24.0, `venv` available) |
| numpy | 1.26.4 ✅ |
| pandas | 2.1.4+dfsg ✅ (Debian) |
| scipy | 1.11.4 ✅ |
| torch, gpytorch, botorch | ❌ missing |
| scikit-learn | ❌ missing |
| fastapi, pydantic, uvicorn | ❌ missing |
| Node | **v24.20.0** ✅, npm/npx at `/opt/hatch-image/bin` (no pnpm) |
| git | **2.43.0** ✅ |
| PyPI reachable | ✅ latest seen: torch 2.14.1, botorch 0.18.1, gpytorch 1.15.2, scikit-learn 1.9.1, fastapi 0.142.4 |
| Machine | 2 vCPU, **7 GB RAM with ~0 GB free at check time** ⚠️, 85 GB disk free |
| Workspace | `/home/hatch/workspace/darwin` has only `BUILD_LOG.md` and is not a git repo yet |

**Install plan (Phase 1 start, not run yet):**
1. `python3 -m venv /home/hatch/workspace/darwin/.venv` (keep it off system site-packages so Debian pandas doesn't conflict).
2. CPU-only torch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`. This avoids roughly 2 GB of CUDA wheels.
3. `pip install "botorch>=0.18.0" "gpytorch>=1.15.2" "linear_operator>=0.6.0" scikit-learn fastapi "pydantic>=2" uvicorn pytest httpx matplotlib`. The lower bounds come from BOxCrete's own `pyproject.toml`, which needs Python ≥3.11.
4. `pip install -e /tmp/boxcrete-src --no-deps` after step 3, or vendor the package path. Pin the exact commit SHA in `darwin/THIRD_PARTY.md`.
5. Frontend (P2): `npx create-next-app@latest web --ts` with npm.
6. Check: `python -c "import torch, botorch, gpytorch, sklearn, fastapi; from boxcrete.concrete_model import SustainableConcreteModel"`.

If torch can't install (memory or network), fall back to the sklearn substitution described in (b) and record it in BUILD_LOG.

---

## (b) BOxCrete integration status

**Status: SOURCE and SCHEMA VERIFIED. RUNTIME NOT YET VERIFIED** because torch, botorch and gpytorch aren't installed. DARWIN may claim "BOxCrete integration" only after `SustainableConcreteModel` actually fits on the DARWIN training split inside our venv. Until then, the UI and reports must say "BOxCrete model code verified; not yet executed".

### Local clone (2026-10-08)
- `git clone --depth 1 https://github.com/facebookresearch/SustainableConcrete /tmp/boxcrete-src` → **succeeded** (exit 0).
- Pinned commit: `116ad287351bee2fa4742ca5d8ce1b7d44db44c9` (committer date 2026-09-29T15:30:21-07:00).
- `data/boxcrete_data.csv`: present, 61,491 bytes, 671 lines (header + 670 rows), header matches the schema below. sha256 prefix `8cf705f4558b941e`.
- `boxcrete/concrete_model.py`: present, 15,454 bytes, defines `class SustainableConcreteModel`.
- Quirk: the clone is owned by a different uid than the sandbox user, so git commands need `-c safe.directory=/tmp/boxcrete-src`.

### Reachability (observed, `curl -sI` → HTTP 200)
- `https://arxiv.org/abs/2603.21525`: 200. Page title: *"[2603.21525] BOxCrete: A Bayesian Optimization Open-Source AI Model for Concrete Strength Forecasting and Mix Optimization"*.
- `https://engineering.fb.com/2026/03/30/data-center-engineering/ai-for-american-produced-cement-and-concrete/`: 200. Page title: *"AI for American-Produced Cement and Concrete - Engineering at Meta"*.
- `https://github.com/facebookresearch/SustainableConcrete`: 200.
- I read no paper or blog body text, so this plan makes no claims about their content.

### Repo layout (real paths)
- `data/boxcrete_data.csv` (61,491 bytes)
- `boxcrete/`: `__init__.py`, `concrete_model.py`, `strength_model.py`, `strength_model_legacy.py`, `slump_model.py`, `features.py`, `kernels.py`, `likelihoods.py`, `priors.py`, `block_loo.py`, `model_utils.py`, `mix_naming.py`, `_mix_naming_table.csv`, `plotting.py`, `units.py`, `utils.py`
- `notebooks/`: `prediction_and_optimization_tutorial.ipynb`, `slump_prediction_demo.ipynb`, `strength_curve_prediction_demo.ipynb`
- `docs/model/`: `strength_model.pt` (pretrained), `gwp.json`, `cost.json`, `compositions.json`, `strength.json`, `mix_analyses.json`, `test_vectors.json`
- Slump code: `boxcrete/slump_model.py` (`fit_slump_gp`) and `notebooks/slump_prediction_demo.ipynb`. GWP code: `SustainableConcreteModel.fit_gwp_model` together with `DEFAULT_GWP_COEFFICIENTS` in `boxcrete/utils.py`. Cost code: `fit_cost_model` and `docs/model/cost.json`.

### CSV schema: `data/boxcrete_data.csv` (19 columns, 670 data rows)
Header, quoted exactly:
`Mix Name, Material Source, Cement (kg/m3), Fly Ash (kg/m3), Slag (kg/m3), Water (kg/m3), HRWR (kg/m3), Fine Aggregate (kg/m3), Coarse Aggregates (kg/m3), Temp (C), Time, GWP, Strength1 (psi), Strength2 (psi), Strength3 (psi), Strength (Mean), Strength (Std), # of measurements, Slump (in)`

Sample rows (first 3):
```
M1,0,353.33,46.67,266.67,140.0,6.67,1833.0,0,22.0,1,321.57,5098.0,4960.0,4943.0,5000,85,3,
M1,0,353.33,46.67,266.67,140.0,6.67,1833.0,0,22.0,3,321.57,7018.0,7293.0,7395.0,7235,195,3,
M1,0,353.33,46.67,266.67,140.0,6.67,1833.0,0,22.0,5,321.57,7498.0,7438.0,7018.0,7318,262,3,
```
Observed structure:
- **149 unique `Mix Name`s**: 69 with the `M` prefix and 80 with the `C` prefix. Each mix has 3, 4 or 5 rows, one per curing age. Composition and Temp are constant within a mix name (0 violations).
- **Mortar vs concrete:** `Coarse Aggregates (kg/m3) == 0` matches the `M` prefix exactly (69/69). Every `C` mix has coarse aggregate > 0. DARWIN gets an explicit `material_class ∈ {mortar, concrete}` column, derived from coarse = 0 and cross-checked against the prefix. Ingestion fails hard if they disagree.
- `Time`: values {1, 3, 5, 14, 28}. Bounds `(0, 28)` in `utils.py` suggest the unit is **days**, and the README talks about "1-day"/"28-day" strength.
- `Temp (C)`: {22.0: 631 rows, 4.5: 34, 10.0: 5}.
- `Material Source`: {0: 270 rows, 1: 135, 2: 265}. `utils.py` comments describe 0 = "Set 1 (Amrize cement / Class C fly ash mortar)" ∪ Concrete-Set-2 MS=0 rows, 1 = "Set 2 (Heidelberg cement / Class C fly ash concrete)", 2 = "Set 3 (Amrize cement / Class F fly ash concrete)".
- `GWP` ranges from 70.0 to 701.42. `Strength (Mean)` ranges from 79 to 16029 psi. `# of measurements` is always 3. `Slump (in)` is filled in 305 rows, covering 61 mixes, all concrete (`C`).
- Units, per the `boxcrete/units.py` docstring: composition **kg/m³**, strength **psi** (MPa factor 0.006895), slump **inches** (×25.4 → mm), GWP **kg CO₂/m³**, cost USD/m³, temperature °C.

### Real API (signatures as read)
- `boxcrete.utils`:
  - `load_concrete_strength(data_path=DATA_PATH, batch_names=None, dtype=None, device=None, mix_name_column="Mix Name", X_columns=DEFAULT_X_COLUMNS, Y_columns=DEFAULT_Y_COLUMNS, Ystd_columns=DEFAULT_YSTD_COLUMNS, process_batch_names_from_mix_name=False, bounds_dict=DEFAULT_BOUNDS_DICT) -> SustainableConcreteDataset`. It accepts a `pd.DataFrame`, **so we can pass our train-split DataFrame directly**.
  - `class SustainableConcreteDataset(X, Y, Ystd, X_columns, Y_columns, Ystd_columns, bounds=None, batch_name_to_indices=None)` exposes `.strength_data`, `.slump_data` and `.X_columns`.
  - Constants: `DEFAULT_X_COLUMNS` (the 9 composition/condition columns plus `"Time"` last), `DEFAULT_Y_COLUMNS=["GWP","Strength (Mean)"]`, `SLUMP_Y_COLUMNS=[...,"Slump (in)"]`, `DEFAULT_YSTD_COLUMNS=["Strength (Std)"]`, `MORTAR_BOUNDS_DICT`, `CONCRETE_BOUNDS_DICT`, `MORTAR_CONSTRAINTS` (binder+fine = 1875, binder 100–950, w/b 0.35–0.5), `CONCRETE_CONSTRAINTS = dict()`, `DEFAULT_GWP_COEFFICIENTS` (per Material Source, `(mean, std)` per ingredient).
  - Functions: `get_bounds`, `get_constraints`, `get_cement_replacement_constraints`, `get_total_water_reducer_constraints`, `get_aggregate_constraint`, `get_sum_constraints`, `get_sum_equality_constraint`, `get_proportional_sum_constraints`, `get_reference_point`, `get_day_zero_data(X, n=128)`, `derive_bounds_from_X`, `reduce_to_optimization_space`, `predict_pareto`.
- `boxcrete.concrete_model.SustainableConcreteModel(strength_days: list[int], strength_model=None, gwp_model=None, slump_model=None, cost_model=None, d=None)`:
  - `.fit_strength_model(data) -> SingleTaskGP`
  - `.fit_gwp_model(data, gwp_coefficients=None) -> LinearModel`. The model is built from coefficients, not fitted.
  - `.fit_slump_model(data, use_fixed_noise=False) -> SingleTaskGP`
  - `.fit_cost_model(...)`
  - `.get_model_list(fixed_features=None) -> ModelList`, ordered `[GWP, (Slump), k-day Strength...]`
  - `.model_names`, `.output_index(name)`, `.get_model_dict(...)`
- `boxcrete.strength_model.fit_strength_gp(X, Y, Yvar=None, X_bounds=None, *, seed=0, max_optimizer_iter=None, num_restarts=None) -> SingleTaskGP` ("V2"). Also `load_pretrained_strength_gp(state_dict_path=None)`.
- `boxcrete.slump_model.fit_slump_gp(X, Y, Yvar, use_fixed_noise=False, optimizer_kwargs=None) -> SingleTaskGP`
- `boxcrete.block_loo.block_loo_loss(...)`, `train_block_loo(...)`. These implement block-LOO where each block is one unique composition, which is already mixture-level.

### Leakage hazards found in the real code (must handle)
1. **`docs/model/strength_model.pt` / `load_pretrained_strength_gp` was trained on all data.** Never use it for held-out evaluation or replay. DARWIN always refits on the training split only.
2. **`DEFAULT_GWP_COEFFICIENTS` were derived by regression over the full CSV**, held-out mixes included. GWP for any candidate is therefore labeled `computed (factor model, dataset-derived)`. In strict mode we re-derive coefficients by least squares on training mixes only and report the difference.
3. **`get_day_zero_data` adds day-zero pseudo-rows.** They are typed `assumed (physical prior: strength(0)=0)` and must never be counted as measurements.
4. **Split key:** group by **composition hash** (the 9 composition/condition columns) as well as `Mix Name`, so that identical recipes under different names can't land on both sides of the split.

### Substitution fallback (used only if the BOxCrete runtime fails)
Model: `sklearn.gaussian_process.GaussianProcessRegressor` with `Matern(nu=2.5, ARD) + WhiteKernel`, `normalize_y=True`, trained on the **real CSV training split**. Inputs are the 9 composition/condition columns plus `log(Time)`, with target `Strength (Mean)` in psi. Train separate models for mortar and concrete, or one model with `material_class` as a feature. The model must be labeled "DARWIN substitute GP (sklearn), NOT BOxCrete" everywhere. Random-number generators are never used as models.

---

## (c) Architecture

```
darwin/
  backend/  (Python 3.12, FastAPI, SQLite via stdlib sqlite3 or SQLModel, pydantic v2)
    darwin/
      types.py               Provenanced[T] = {value, unit, kind: measured|predicted|assumed|computed, source, model_version?}
      data_ingestion/        load CSV → validate exact header → derive material_class → mixture table + observation table
      materials_model/       BOxCreteAdapter (refit on split) | SklearnGPSubstitute; shared interface predict(X)->(mean, std)
      constraint_solver/     deterministic feasibility; returns machine-readable rejection reasons [{code, field, limit, value}]
      acquisition_engine/    EI / UCB / constrained-EI on model posteriors; pure functions, seeded, no LLM
      experiment_replay/     held-out "oracle": reveals ONLY the recorded measurement of the selected mixture; logs every reveal
      learning_engine/       update model with revealed rows; before/after metrics snapshot
      environmental_accounting/ GWP via factor tables with per-factor provenance (BOxCrete coeffs | fictional facility | assumed)
      decision_router/       Jev adapter (bounded review classifier: approve/flag/reject + reason); deterministic fallback when JEV_API_KEY absent, labeled as such
      report_generator/      markdown/JSON export of ledger, metrics, negative results
      ledger/                SQLite: experiments(status PROPOSED→APPROVED→AWAITING_RESULT→MEASURED | REJECTED), reveals, model_versions
      api/                   FastAPI routers
    tests/
  web/      (Next.js + TypeScript, P2)
  data/fictional_facilities/  (P1, every file headed "FICTIONAL — demo only")
```
Service boundary rule: numbers (feasibility, acquisition, GWP, model fits) come only from deterministic Python. The decision_router sees a finished candidate record and returns a label. It can never change numeric fields.

---

## (d) Phased checklist

### P0 — MVP 13-step loop (CLI/pytest, no UI)
1. [ ] Import `/tmp/boxcrete-src/data/boxcrete_data.csv` (pinned SHA, copied into `darwin/data/raw/` with a checksum).
2. [ ] Verify the exact 19-column header, units (table above), Time ∈ {1,3,5,14,28}, `material_class` consistency, and constant composition within each mix. Fail hard on mismatch.
3. [ ] Mixture-level held-out split, seeded, grouped by composition hash and stratified by `material_class` and `Material Source` (e.g. 70/30). Add a test asserting zero composition overlap.
4. [ ] Train the real model on the training split only: `SustainableConcreteModel.fit_strength_model` (and `fit_gwp_model` with train-derived coefficients in strict mode). Use the fallback substitute only if this fails, and log why.
5. [ ] Evaluate on unseen mixtures: MAE, RMSE (psi and MPa) and 90%/95% interval coverage, broken down by age and by mortar/concrete. Baselines: (i) per-age training mean, (ii) linear/ridge on composition. Report honestly even if BOxCrete loses.
6. [ ] Generate feasible candidates. The **revealable pool** is the held-out real mixtures. Generated novel candidates are prediction-only and can never be "measured".
7. [ ] Score candidates with deterministic acquisition (e.g. EI on 28-day strength at a GWP cap, or a GWP-penalized UCB). Seeded and reproducible.
8. [ ] Select the top eligible held-out candidate.
9. [ ] Reveal only its recorded rows (all ages for that mixture) and write the reveal to the ledger.
10. [ ] Update the model: refit on train plus revealed rows, with a version bump.
11. [ ] Show before/after: the prediction for that mixture, and metrics on the remaining pool.
12. [ ] Repeat for N rounds.
13. [ ] Compare against random selection with the same budget across multiple seeds: best-found trade-off and regret versus the true best in the pool.

### P1
- [ ] Two fictional facilities (e.g. "Facility A — FICTIONAL", "Facility B — FICTIONAL") with available materials, max fly ash/slag %, w/b range, cement availability and a local GWP factor table. Every value is typed `assumed` with a "fictional" source.
- [ ] Deterministic feasibility solver with rejection reasons `{code: "WB_RATIO_ABOVE_MAX", field, limit, value}`, reusing BOxCrete constraint helpers (`MORTAR_CONSTRAINTS`, `get_constraints`) where it makes sense.
- [ ] Environmental accounting. Per-factor provenance covers: BOxCrete `DEFAULT_GWP_COEFFICIENTS` by Material Source, train-refit coefficients, and fictional facility factors. Show per-ingredient breakdown and uncertainty (the coefficient std).
- [ ] Jev adapter interface (`review(candidate) -> {label, reasons, reviewer: "jev"|"deterministic-fallback"}`). There's no `JEV_API_KEY`, so the fallback is a rule table, clearly labeled.
- [ ] FastAPI endpoints: `/dataset`, `/split`, `/model/train`, `/model/evaluate`, `/candidates`, `/acquisition`, `/experiments` (CRUD and status transitions), `/replay/reveal`, `/report`.
- [ ] SQLite ledger with an enforced state machine PROPOSED → APPROVED → AWAITING_RESULT → MEASURED, with REJECTED reachable from PROPOSED or APPROVED. Write an audit row on every transition.

### P2
- [ ] Next.js + TypeScript five-screen instrument UI: (1) Dataset & provenance, (2) Model & held-out evaluation, (3) Facility constraints & candidates (with rejection reasons), (4) Experiment loop / ledger (before/after), (5) Evaluation & report.
- [ ] Replay evaluation: acquisition vs random vs greedy (mean-only) over ≥10 seeds, mean ± std / CI.
- [ ] Full test suite: leakage tests, provenance-type tests, feasibility determinism, ledger state machine, API contract and frontend smoke tests.
- [ ] Report export (Markdown/JSON, with negative results included) and a demo script (`scripts/demo.sh` plus a narrated walkthrough).

---

## (e) Risks & open questions
1. **Memory:** the host showed ~0 GB available out of 7 GB at check time. Fitting torch plus the V2 strength GP (670 rows plus day-zero anchors, multi-Matérn, block-LOO) could hit OOM or run slowly on 2 vCPU. Mitigations: the CPU wheel, `max_optimizer_iter`/`num_restarts` limits, and caching fits per model version.
2. **Refit cost per loop iteration.** If a full refit is too slow for a live demo, precompute the replay trajectories and reload them, and say in the UI that they were precomputed.
3. **Small held-out pool:** about 45 mixtures at 30%. Selection-versus-random differences may not be statistically meaningful, so we report variance and keep null results.
4. **GWP coefficients leak** across the split (see hazard 2). Train-only refit gives the strict mode, and both numbers get reported.
5. **Material Source 0 mixes mortar with some concrete** (per the `utils.py` comment). Stratification and per-class GWP factors have to account for that.
6. **Slump** exists only for 61 concrete mixes. Slump-constrained acquisition is concrete-only. Mortar slump is "not measured", never imputed.
7. **Time unit** is inferred as days from the bounds and README wording, not from an explicit column header. Flag this in the data card.
8. ~~Open: demo headline objective~~ → **resolved**, see Decisions (a).
9. ~~Open: Jev API availability~~ → **resolved**, see Decisions (b).
10. ~~Open: where the frontend runs~~ → **resolved**, see Decisions (c).
11. **Phase 1 flag, torch vs sklearn:** with 2 vCPU / 7 GB RAM and low free memory, Phase 1 must *attempt* the CPU torch + botorch/gpytorch install and a real `SustainableConcreteModel` fit. If that's infeasible (install failure, OOM, or impractical fit time), fall back to the sklearn `GaussianProcessRegressor` substitute on the real CSV training split, labeled honestly as "DARWIN substitute GP (sklearn), NOT BOxCrete", and record the reason in BUILD_LOG.
12. **License:** BOxCrete is MIT. Keep the attribution and the CITATION entry (`baten2026boxcrete`) in the README and the report.

## Verification (for Phase 0 itself)
- Done 2026-10-08: the clone exists at `/tmp/boxcrete-src` and **not** inside `darwin/`. Run `head -1 data/boxcrete_data.csv`, which should match the header above, and `wc -l`, which should give 671. `PLAN.md` is present in `darwin/`, and `BUILD_LOG.md` has a Phase 0 entry. No implementation code is written.

---

## Decisions (coordinator, 2026-10-08)
- **(a) Demo objective:** the demo leads with the **28-day, 40 MPa target** (≈5,801 psi at 0.006895 MPa/psi) per spec section 3, minimizing GWP subject to that target. The 1-day strength target stays available as a **configurable secondary target** (precast-throughput scenario), not the headline.
- **(b) Jev:** there is **no `JEV_API_KEY` on this machine**. Per spec section 5, build the real Jev adapter interface (`review(candidate) -> {label, reasons, reviewer}`) plus a **deterministic rule-table fallback**, labeled `reviewer: "deterministic-fallback"` in every API response, ledger row, UI badge and report. The real adapter activates only when a key is present. Neither path can change numeric fields.
- **(c) Frontend:** build the Next.js + TypeScript app **in-repo at `darwin/web`** and verify it with a **production build (`next build`) on this VM**. Vercel deployment is **prepared** (config/README steps) but **NOT executed**.

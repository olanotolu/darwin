# DARWIN — assumptions log & functional vs incomplete

## Assumptions (all labeled in code/UI where they surface)

### Data & modeling
- BOxCrete `boxcrete_data.csv` @116ad28 is taken as ground truth for measured values.
  Strengths are stored in **psi** and converted ×0.00689476 to MPa; quantities are kg/m³;
  `Time` is curing age in days {1,3,5,14,28}; each row is a mean of 3 cylinder breaks.
- Train/held-out splits are per material class (mortar and concrete never mixed) and
  grouped by Mix Name ∪ recipe hash, so M22/M36 (same recipe, different cure temp) never
  split. Stratified by Material Source, 30% held out, fixed seed.
- The BOxCrete V2 GP is refit **only** on train-split rows; the shipped
  `strength_model.pt` (trained on all data) is never loaded.
- Replay refits are **warm-started** (same model class/objective, previous
  hyperparameters, ≤60 L-BFGS iterations, ~3 s). Cold fits take 47–105 s; `--cold-refits`
  reproduces them. The honest position: warm starts are an approximation for demo speed.
- Day-zero pseudo-rows: none are synthesized. Every training row is a measured CSV row.
- The 95% predictive intervals **under-cover** (concrete 0.858, mortar 0.885 on seed 0)
  and are reported as uncalibrated.

### Facilities & economics (ALL FICTIONAL — labeled in UI and API)
- Hudson Materials and Atlantic Concrete, their inventories, supplier prices, lab budgets,
  and GWP factors are invented fixtures. Nothing here describes a real company.
- Facility → BOxCrete `Material Source` mapping is an assumption (needed because the
  model conditions on source).
- Curing temperature per facility is an assumed constant.

### Environmental accounting
- GWP = Σ(quantity × factor) over a **materials-only partial boundary** — explicitly not
  a complete A1–A3 LCA and not an EPD. Transport and plant energy are out of scope.
- `darwin_materials_gwp` (factor-based, auditable) is kept separate from
  `boxcrete_gwp_model_output` (BOxCrete's own LinearModel) — never summed or double-counted.
- Missing factors are flagged per-line (`status`), never silently zeroed.

### Acquisition & replay
- EI is computed on latent 28-day strength from the GP posterior; deterministic given seed.
- Exploit/explore/balanced are fixed weight vectors over (p_meet_target, EI, uncertainty,
  carbon, over-budget penalty) — documented in `objective_scoring.py`.
- Replay reveals only genuinely held-out measurements; labels are structurally masked
  from acquisition (HeldOutOracle, signature-tested). Unmeasured formulations are
  prediction-only and can never be "revealed".
- Replay auto-approve is an automated operator step in the audit log, not human sign-off.

### Jev
- No JEV_API_KEY exists. The adapter implements the real TypeSafe contract
  (`POST /v1/systemone`, `choice` question, 5-route taxonomy) but has never made a live
  call; every served decision is the deterministic fallback labeled
  `SIMULATED — no live Jev call`. Jev never touches numerical optimization.

## Functional (verified)
- P0 13-step loop end-to-end; 28 P0 tests + 43 P1 unit + 7 API tests green.
- Held-out eval with MAE/RMSE/coverage vs train-mean and ridge baselines (ridge beats the
  GP on concrete — reported, not hidden).
- Seeded candidate generation with machine-readable rejection reasons; deterministic EI/UCB.
- Offline replay with zero-leakage guards; 5-trial EI-vs-random comparison.
- Facilities, 3-state feasibility solver (12 rejection codes), env accounting with factor
  provenance + immutable snapshots, SQLite ledger with valid state transitions, 17-route
  FastAPI serving real predictions, `/report/export` evidence packet.
- Next.js frontend: 5 screens wired to real endpoints via same-origin proxy; `npm run build`
  passes; provenance glyphs (M/P/A/C/S); FICTIONAL banners; measured-vs-predicted encoding.

## Incomplete / known limits
- Replay-vs-random comparison is 5 seeded trials (small n); pool max sits in held-out 4/5
  times, inflating the EI win. Single-seed in `/baselines/compare`.
- API serves concrete only (mortar → MATERIAL_CLASS_UNSUPPORTED).
- Replay uses the startup split only (seed via DARWIN_SEED); no multi-split replay in API.
- AccountingChain dependency index is in-memory (snapshots persist in SQLite).
- No Playwright/browser E2E for the frontend (build + typecheck + manual click-through).
- No live Jev (no key); no A1–A3 scope; no human approval workflow; mortar replay comparison
  not run in P0 (runnable via `--material-class mortar`).
- Vercel deploy prepared but NOT executed (awaiting deploy step).

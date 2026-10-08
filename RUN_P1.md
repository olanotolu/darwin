# DARWIN P1 — how to run

Run everything from `/home/hatch/workspace/darwin`. P0 prerequisites (venv, `/tmp/boxcrete-src` @116ad28) are covered in `RUN_P0.md`.

## Setup (already done)
```
.venv/bin/pip install fastapi 'pydantic>=2' uvicorn httpx
```
Installed versions: fastapi 0.142.4, pydantic 2.13.5, uvicorn 0.54.0, httpx 0.28.1.

## Start the API
```
.venv/bin/uvicorn darwin_core.api:app            # http://127.0.0.1:8000  (docs at /docs)
```
- At startup the server fits the real BOxCrete strength GP **once** on the concrete train split (seed 0) and caches it. Expect about 75–85 s before `Application startup complete`. Requests don't refit, with one exception: `POST /replay/update` warm-start refits the *replay-session* model on approved measurements. The startup model is never mutated.
- Env vars:
  - `DARWIN_BACKEND=auto|boxcrete|sklearn` (default `auto`, which uses BOxCrete; sklearn is the labeled "NOT BOxCrete" substitute)
  - `DARWIN_SEED` (default 0)
  - `DARWIN_DB` (default `./darwin.db`)
  - `JEV_API_KEY`: if set, the live Jev adapter is used. If not set, which is the case on this machine, the deterministic fallback runs, labeled `SIMULATED — no live Jev call`.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | /facilities, /materials | FICTIONAL Hudson Materials / Atlantic Concrete fixtures, with current GWP factors |
| GET | /dataset/metadata | data card, split, model label/version/snapshot id, training domain, router identity |
| POST/GET | /objectives | target strength/age, carbon budget, facility, mode `exploit\|explore\|balanced`, restrictions, replacement cap |
| POST | /predict | mixture → strength at all ages (mean/std/PI95) + `darwin_materials_gwp` and `boxcrete_gwp_model_output` (separate, never summed) + cost + feasibility |
| POST | /candidates/generate | seeded facility-aware candidates; rejected ones are kept with reason codes |
| POST | /experiments/rank | acquisition score + components per mode; Jev/fallback review routes run AFTER ranking; `propose_top` writes PROPOSED ledger rows |
| POST | /experiments/{id}/transition | manual state change (validated) |
| POST | /factors/update | new factor version → recomputes dependent impact snapshots |
| POST | /replay/start, /replay/next, /replay/reveal, /replay/update | held-out replay: choose + predict BEFORE reveal → reveal real measurement → refit on APPROVED measurements only |
| GET | /history | experiments, audit transitions, model snapshots, router decisions |
| POST | /baselines/compare | startup model vs train-mean and ridge on held-out; replay vs seeded random |
| GET | /report/export | JSON evidence packet (disclaimers, ledger, calc snapshots, negative results) |

Example session:
```
curl -s localhost:8000/facilities
curl -s -XPOST localhost:8000/objectives -H 'content-type: application/json' \
  -d '{"target_strength_mpa":40,"carbon_budget_kgco2e_m3":300,"facility_id":"hudson","acquisition_mode":"balanced"}'
curl -s -XPOST localhost:8000/predict -H 'content-type: application/json' \
  -d '{"composition":{"cement":300,"fly_ash":50,"slag":100,"water":180,"admixture_hrwr":1,"fine_aggregate":800,"coarse_aggregate":1100}}'
curl -s -XPOST localhost:8000/candidates/generate -H 'content-type: application/json' -d '{"n":200,"seed":0}'
curl -s -XPOST localhost:8000/experiments/rank -H 'content-type: application/json' -d '{"mode":"explore","top_k":5}'
curl -s -XPOST localhost:8000/replay/start -H 'content-type: application/json' -d '{}'
curl -s -XPOST localhost:8000/replay/next  -H 'content-type: application/json' -d '{}'      # note experiment.id
curl -s -XPOST localhost:8000/replay/reveal -H 'content-type: application/json' -d '{"experiment_id":"EXP-..."}'
curl -s -XPOST localhost:8000/replay/update
curl -s localhost:8000/report/export > results/p1_report.json
```

## Tests
```
.venv/bin/python -m pytest                      # all: 78 tests, ~4 min
.venv/bin/python -m pytest tests/test_p1.py     # 43 unit tests, ~7 s
.venv/bin/python -m pytest tests/test_api.py    # 7 API smoke tests, real BOxCrete model, ~75 s
```

## Modules (new in P1)
- `darwin_core/facilities.py`: the fictional fixtures. Every record has `fictional: true`, the FICTIONAL label, and factors typed `assumed`.
- `darwin_core/feasibility.py`: deterministic solver. It returns `COMPUTATIONALLY_FEASIBLE | REQUIRES_LAB_VALIDATION | OUTSIDE_SUPPORTED_DOMAIN` together with `{code, field, limit, value}` reasons and a not-construction-ready disclaimer.
  - Any hard rejection reason gives `OUTSIDE_SUPPORTED_DOMAIN`.
  - A mix with no rejections but a value outside the training-data range gives `REQUIRES_LAB_VALIDATION`.
- `darwin_core/env_accounting.py`: partial-boundary materials GWP, the BOxCrete GWP LinearModel wrapper, and the `AccountingChain` dependency chain (supplier→factor→mixture→impact→experiment, stored as immutable snapshots).
- `darwin_core/objective_scoring.py`: exploit/balanced/explore weighted acquisition with components. It reuses the P0 EI and label guard.
- `darwin_core/jev_router.py`: the live adapter for the observed TypeSafe/Jev contract (`POST /v1/systemone`, Bearer auth; snapshot in `docs/jev_openapi_snapshot.json`) and the deterministic fallback.
- `darwin_core/ledger.py`: SQLite ledger. The state machine is enforced in Python and also by DB triggers. `approved_measurements_for_training()` is the only route from the ledger into training data.
- `darwin_core/api.py`: the FastAPI app.

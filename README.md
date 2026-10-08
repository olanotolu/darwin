# DARWIN — autonomous scientific discovery engine for concrete

DARWIN is an experimental research tool that proposes concrete-mixture experiments,
learns from real held-out measurements, and produces a traceable evidence trail of what
was actually learned. It extends Meta's BOxCrete (real GP models, real data) with
factory-constrained discovery, sequential experimental learning, evidence-aware model
updates, and auditable environmental accounting.

**Not** an EPD generator, carbon calculator, structural-approval system, or certified
engineering tool. Not a BOxCrete rebrand: BOxCrete is used as the underlying model
with attribution; DARWIN is the layer around it (constraints → acquisition → replay →
provenance).

## Quick start

```bash
cd /home/hatch/workspace/darwin

# 0. BOxCrete source (re-clone if /tmp was wiped; durable backup in vendor/)
git clone https://github.com/facebookresearch/SustainableConcrete /tmp/boxcrete-src
git -c safe.directory=/tmp/boxcrete-src -C /tmp/boxcrete-src checkout 116ad287351bee2fa4742ca5d8ce1b7d44db44c9

# 1. Python env (already built; rebuild only if .venv is gone)
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install "botorch>=0.18.0" "gpytorch>=1.15.2" "linear_operator>=0.6.0" \
  scikit-learn pytest matplotlib pandas numpy scipy fastapi 'pydantic>=2' uvicorn httpx

# 2. Tests (78 pass, ~3 min)
.venv/bin/python -m pytest -q

# 3. P0 scientific loop — full 13-step MVP on the real data (~15 min)
PYTHONUNBUFFERED=1 .venv/bin/python -m darwin_core.run_p0 all --backend boxcrete | tee results/p0_run_all_concrete.log
# EI vs random, 10 seeded trials:
.venv/bin/python -m darwin_core.run_p0 compare --backend boxcrete --k 10 --trials 10

# 4. API backend (startup ~75 s: fits the BOxCrete GP once, then caches)
.venv/bin/uvicorn darwin_core.api:app --host 127.0.0.1 --port 8000
# API docs: http://127.0.0.1:8000/docs

# 5. Frontend (separate terminal; NEVER build while the backend is running npm install)
cd web && NEXT_PUBLIC_DARWIN_API=http://127.0.0.1:8000 npm run dev -- -p 3000
# → http://localhost:3000
```

## Repo map
| Path | What |
|---|---|
| `darwin_core/` | Scientific backend: ingestion, leakage-safe splits, BOxCrete GP wrapper, baselines, candidates, acquisition, replay, facilities (FICTIONAL), feasibility solver, env accounting, Jev router, SQLite ledger, FastAPI app |
| `web/` | Next.js 16 + TS frontend: 5 screens wired to real API via `/api/darwin/*` proxy |
| `tests/` | 78 pytest tests (isolation, no-leakage, units, determinism, API smoke) |
| `results/` | Replay/eval JSON outputs (real numbers) |
| `docs/` | ASSUMPTIONS.md, jev_openapi_snapshot.json, replay_chart.png |
| `vendor/boxcrete/` | Durable backup of BOxCrete CSV + code @116ad28 (survives /tmp wipes) |
| `PLAN.md` / `BUILD_LOG.md` | Architecture, decisions, full build history |
| `RUN_P0.md` / `RUN_P1.md` / `RUN_WEB.md` | Exact run commands per phase |
| `DEMO.md` | 3-minute live demo script |

## Headline results (honest)
- Held-out (concrete, seed 0): GP MAE 5.17 MPa, 95% PI coverage 0.858 (under-calibrated).
  **Ridge baseline beats the GP on concrete (MAE 4.05)** — reported, not hidden.
- Replay (K=10, 10 seeded trials): EI best-observed 104.3±13.8 vs random 84.0±21.5 MPa;
  EI found the pool max 5/5, random 1/5. Caveat: pool max present in held-out 4/5 seeds;
  n is small.
- Prediction error on unrevealed held-out rows fell over replay steps every trial.

## Key limits
- Facilities, prices, inventories, lab budgets, GWP factors: **invented fixtures**.
- GWP is materials-only partial boundary — **not** an A1–A3 LCA or EPD.
- No `JEV_API_KEY`: Jev routing is a labeled deterministic fallback, never live-called.
- Concrete only in the API; replay uses the startup split; no browser E2E.

## Deploy
Prepared but **not executed**. Frontend: `cd web && npm run build && npm start` (needs
`NEXT_PUBLIC_DARWIN_API` pointing at a reachable backend). Backend: uvicorn on any host
with the venv; startup fit ~75 s on 2 vCPU.

# DARWIN web frontend — how to run

Next.js 16 (App Router) + TypeScript + recharts, in `darwin/web`. All data comes from the
real FastAPI backend (`darwin_core/api.py`) through a same-origin proxy — no mock data,
no hardcoded predictions, no fake telemetry.

## Prerequisites
- Backend venv at `darwin/.venv` (built in Phase 1/2).
- `darwin/web/node_modules` (installed; if missing: `cd web && npm install`).
- **Memory rule on this VM: NEVER run the FastAPI backend (uvicorn) at the same time as
  `npm install` or `npm run build`.** The box is memory-tight and the OOM killer will
  SIGTERM one of them. Run them strictly sequentially.

## 1. Start the backend (terminal 1)
```
cd /home/hatch/workspace/darwin
DARWIN_SEED=0 .venv/bin/uvicorn darwin_core.api:app --host 127.0.0.1 --port 8000
```
Startup takes ~75 s (cold BOxCrete GP fit). Wait for `Application startup complete`,
then verify:
```
curl -s http://127.0.0.1:8000/facilities | head -c 300
curl -s -X POST http://127.0.0.1:8000/objectives \
  -H 'content-type: application/json' \
  -d '{"target_strength_mpa":40,"facility_id":"hudson"}' | head -c 300
```

## 2. Start the frontend (terminal 2)
```
cd /home/hatch/workspace/darwin/web
NEXT_PUBLIC_DARWIN_API=http://127.0.0.1:8000 npm run dev -- -p 3000
```
Open http://localhost:3000. The page shows a backend-status indicator; if the backend
is still fitting, it reports `backend_unreachable` (HTTP 503 from the proxy) until
uvicorn is ready.

`NEXT_PUBLIC_DARWIN_API` defaults to `http://127.0.0.1:8000` when unset. The browser
never talks to the backend directly: all calls go to `/api/darwin/*`, which the
Next.js server forwards (the backend sends no CORS headers).

## 3. Production build (verified on this VM)
```
cd /home/hatch/workspace/darwin/web
npm run build     # includes tsc --noEmit; must pass with zero errors
npm start         # serves the production build on :3000 (backend still required)
```

## Screens (all five, all wired to real endpoints)
| Screen | Backend endpoints used |
|---|---|
| Discovery Lab (search space + inspector + config) | `/ui/space`, `/objectives`, `/candidates/generate`, `/experiments/rank`, `/predict` |
| Material Genome (composition inspector, 2-mix compare) | `/predict` (strength by age, GWP lines, cost, feasibility) |
| Experiment Replay (predict → reveal → update loop) | `/replay/start`, `/replay/next`, `/replay/reveal`, `/replay/update`, `/history` |
| Factory Reality (facility what-if overrides) | `/facilities`, `/ui/facility_overrides`, `/ui/facility_reset`, `/ui/space` |
| Scientific Report (exportable evidence packet) | `/report/export`, `/history`, `/baselines/compare` |

## Conventions enforced in the UI
- Every value carries a provenance glyph: **M** measured · **P** predicted · **A** assumed ·
  **C** computed · **S** simulated (Jev fallback only).
- Predicted candidates render as hollow markers; measured mixtures as solid. An unmeasured
  candidate can never look like a confirmed result.
- Both facilities and all prices/inventories/factors carry a **FICTIONAL** banner.
- GWP figures are labeled "materials-only partial boundary — not an A1–A3 EPD".
- Jev routing decisions are labeled `SIMULATED — no live Jev call`.
- Cold-start: if the backend isn't up, screens show the unreachable state, never fake data.

## Tests / checks
```
npm run typecheck   # tsc --noEmit
npm run build       # full production build (acceptance gate)
```
There is no browser E2E; verification is build + typecheck + manual click-through
against the live backend (see DEMO.md for the scripted path).

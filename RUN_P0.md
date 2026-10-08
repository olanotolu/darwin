# DARWIN P0 — how to run

All commands run from `/home/hatch/workspace/darwin`. Data is the real BOxCrete CSV at
`/tmp/boxcrete-src/data/boxcrete_data.csv` (commit `116ad28`). BOxCrete code is imported from
`/tmp/boxcrete-src` (added to `sys.path` at runtime). If that clone is missing, re-clone it:
`git clone https://github.com/facebookresearch/SustainableConcrete /tmp/boxcrete-src && git -c safe.directory=/tmp/boxcrete-src -C /tmp/boxcrete-src checkout 116ad287351bee2fa4742ca5d8ce1b7d44db44c9`.

## Setup (already done)
```
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install "botorch>=0.18.0" "gpytorch>=1.15.2" "linear_operator>=0.6.0" scikit-learn pytest matplotlib pandas numpy scipy
```
Installed: torch 2.14.1+cpu, botorch 0.18.1, gpytorch 1.15.2, scikit-learn 1.9.1.

## Full loop (steps 1–13)
```
PYTHONUNBUFFERED=1 .venv/bin/python -m darwin_core.run_p0 all --backend boxcrete | tee results/p0_run_all_concrete.log
```
This takes about 10–20 minutes on 2 vCPU. Each trial does one cold BOxCrete fit (~70 s for concrete), and each replay refit is warm-started (~5 s).

## Individual steps
| Step(s) | Command |
|---|---|
| 1–2 import + schema/units data card | `.venv/bin/python -m darwin_core.run_p0 data` |
| 3–5 split, train, held-out eval vs baselines (both classes) | `.venv/bin/python -m darwin_core.run_p0 evaluate --backend boxcrete --seed 0` |
| 6–7 seeded candidate generation + rejection reasons + EI ranking | `.venv/bin/python -m darwin_core.run_p0 candidates --backend boxcrete --seed 0` |
| 8–12 single replay, K=10 | `.venv/bin/python -m darwin_core.run_p0 replay --backend boxcrete --seed 0 --k 10` |
| 13 EI vs random, 5 seeded trials | `.venv/bin/python -m darwin_core.run_p0 compare --backend boxcrete --k 10 --trials 5` |

Options: `--material-class {concrete,mortar}` (default concrete; a run only ever covers one class), `--backend {auto,boxcrete,sklearn}`, and `--cold-refits`, which makes every replay refit a full cold BOxCrete fit (slow, ~70 s per step).
`--backend sklearn` is the fallback, labeled "DARWIN substitute GP (sklearn), NOT BOxCrete".

Results are written as JSON to `results/` (`p0_data_card.json`, `p0_evaluate_seed0.json`, `p0_candidates_*.json`,
`p0_compare_concrete_K10_T5.json`).

## Tests
```
.venv/bin/python -m pytest
```
The suite has 28 tests and takes about 2–6 minutes, mostly in the sklearn and BOxCrete fits. They cover mixture/recipe isolation for both classes across 5 seeds, no-leakage (pool has no label columns, the acquisition signature can't take the oracle, label columns are rejected, the ranking stays the same when hidden labels are scrambled, and a predict spy confirms no label columns are passed), psi→MPa, rejection reasons, deterministic acquisition and candidates, a model update that actually changes predictions (sklearn and real BOxCrete), and provenance kinds.

## Design notes / honesty
- **Model**: `BoxcreteStrengthModel` calls BOxCrete's `load_concrete_strength(train_df)` and then `SustainableConcreteModel.fit_strength_model` on train-split rows only. The pretrained `docs/model/strength_model.pt` (trained on all data) is never loaded. Predictions are `posterior.mean × y_max` (BOxCrete's max-scaling). Predictive std adds the gated noise σ²·h(t)².
- **Replay refits are warm-started.** BOxCrete's own `fit_strength_gp(..., max_optimizer_iter=0)` rebuilds the V2 GP on the enlarged data, the previous hyperparameters are copied in, and the MLL is re-optimised for ≤60 L-BFGS iterations. The model class, kernel, likelihood and objective are unchanged; only the starting point and iteration cap differ from a cold fit. Pass `--cold-refits` for full cold fits.
- **Day-zero rows**: BOxCrete's V2 fit path (`strength_model.py`, `concrete_model.py`) does not call `get_day_zero_data`, and DARWIN adds no synthetic rows. Every training row is a measured CSV row.
- **Split**: per material class (mortar and concrete are never mixed). Groups are the union of Mix Name and the recipe hash of the 7 mass quantities, so M22 and M36 (the same recipe cured at 22 °C and 4.5 °C) always land together. The split is stratified by Material Source with 30% held out and a fixed seed.
- **Pool eligibility** uses only the feasibility rules (w/b in [0.25, 0.65], no negative quantities, binder > 0). It doesn't depend on any held-out label, including whether a 28-day row exists.
- **EI incumbent** is the best measured 28-day strength in train plus revealed mixtures only.
- **Time** is the curing age in days, values {1, 3, 5, 14, 28}. Each row is the mean of 3 cylinder breaks at that age.

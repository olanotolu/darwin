"""P0 entry point.

    .venv/bin/python -m darwin_core.run_p0 all
    .venv/bin/python -m darwin_core.run_p0 {data,evaluate,candidates,replay,compare} [opts]
"""

from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path

import numpy as np

from darwin_core.acquisition import rank_candidates
from darwin_core.candidates import generate_candidates
from darwin_core.evaluation import evaluate_with_baselines
from darwin_core.ingestion import data_card, load_dataset, mixture_table
from darwin_core.model import boxcrete_available, make_model
from darwin_core.replay import best_28d, compare, run_acquisition_replay
from darwin_core.split import make_split

RESULTS = Path(__file__).resolve().parent.parent / "results"


def _save(name: str, obj) -> Path:
    RESULTS.mkdir(exist_ok=True)
    p = RESULTS / name
    p.write_text(json.dumps(obj, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    return p


def cmd_data(a, obs):
    card = data_card(obs)
    print(json.dumps({k: v for k, v in card.items() if k != "provenance"}, indent=1))
    print("saved", _save("p0_data_card.json", card))


def _split_rows(obs, cls, seed):
    sp = make_split(obs, cls, seed)
    return sp, obs[obs["Mix Name"].isin(sp.train_mixes)], obs[obs["Mix Name"].isin(sp.heldout_mixes)]


def cmd_evaluate(a, obs):
    out = {}
    for cls in a.classes:
        sp, tr, te = _split_rows(obs, cls, a.seed)
        m = make_model(a.backend, a.seed, sp.split_id).fit(tr)
        res = evaluate_with_baselines(m, tr, te)
        res["split"] = {"split_id": sp.split_id, "train_mixes": len(sp.train_mixes),
                        "heldout_mixes": len(sp.heldout_mixes), "fit_seconds": m.fit_seconds}
        out[cls] = res
        r = res["model"]
        print(f"[{cls}] {sp.split_id} train={len(sp.train_mixes)} mixes/{len(tr)} rows, "
              f"held-out={len(sp.heldout_mixes)} mixes/{len(te)} rows; model: {m.label}")
        print(f"   GP     MAE {r['mae_mpa']:.2f}  RMSE {r['rmse_mpa']:.2f} MPa  95%PI coverage {r['pi95_coverage']:.3f}"
              f"  (28d MAE {r['by_age'].get(28, {}).get('mae_mpa')})")
        for b in ("train_mean_per_age", "ridge"):
            print(f"   {b:<18} MAE {res[b]['mae_mpa']:.2f}  RMSE {res[b]['rmse_mpa']:.2f} MPa")
    print("saved", _save(f"p0_evaluate_seed{a.seed}.json", out))


def cmd_candidates(a, obs):
    cls = a.material_class
    sp, tr, _ = _split_rows(obs, cls, a.seed)
    feas, rej = generate_candidates(mixture_table(tr), a.n_candidates, a.seed, cls, sp.split_id)
    codes = {}
    for r in rej:
        for x in r["reasons"]:
            codes[x["code"]] = codes.get(x["code"], 0) + 1
    print(f"[{cls}] generated {a.n_candidates}: feasible {len(feas)}, rejected {len(rej)}; reason counts {codes}")
    if rej:
        print("   example rejection:", json.dumps(rej[0]["reasons"]))
    m = make_model(a.backend, a.seed, sp.split_id).fit(tr)
    rank = rank_candidates(m, feas.drop(columns=["provenance"]), best_28d(tr))
    print("   top-3 generated (PREDICTION-ONLY, never measured):")
    for r in rank.head(3).to_dict("records"):
        print(f"     {r['Mix Name']}  pred28 {r['pred_28d_mpa']:.2f}±{r['std_28d_mpa']:.2f} MPa  EI {r['ei']:.3f}")
    _save(f"p0_candidates_{cls}_seed{a.seed}.json",
          {"feasible": feas.to_dict("records"), "rejected": rej,
           "ranking": rank.drop(columns=["rejection_reasons"]).to_dict("records")})


def cmd_replay(a, obs):
    res = run_acquisition_replay(obs, a.material_class, a.seed, a.k, a.backend, a.cold_refits)
    print("saved", _save(f"p0_replay_{a.material_class}_seed{a.seed}.json", res))


def cmd_compare(a, obs):
    res = compare(obs, a.material_class, tuple(range(a.trials)), a.k, a.backend, a.cold_refits)
    s = res["summary"]
    print(f"\n=== acquisition (EI) vs random — {a.material_class}, K={a.k}, {a.trials} seeded trials ===")
    print(f"model: {s['model_label']}")
    print("step | EI best28 mean±std | random best28 mean±std (MPa)")
    for i in range(len(s["acq_best_mean"])):
        print(f"{i+1:4d} | {s['acq_best_mean'][i]:7.2f} ± {s['acq_best_std'][i]:5.2f}  | "
              f"{s['rand_best_mean'][i]:7.2f} ± {s['rand_best_std'][i]:5.2f}")
    print(f"final diff (EI - random) {s['final_diff_mean']:+.2f} ± {s['final_diff_std']:.2f} MPa; "
          f"found pool max: EI {s['acq_found_pool_max']}/{a.trials}, random {s['rand_found_pool_max']}/{a.trials}")
    print("verdict:", s["verdict"])
    print("saved", _save(f"p0_compare_{a.material_class}_K{a.k}_T{a.trials}.json", res))


def main(argv=None):
    p = argparse.ArgumentParser(prog="darwin_core.run_p0")
    p.add_argument("command", choices=["data", "evaluate", "candidates", "replay", "compare", "all"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--material-class", default="concrete", choices=["concrete", "mortar"])
    p.add_argument("--backend", default="auto", choices=["auto", "boxcrete", "sklearn"])
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--trials", type=int, default=5)
    p.add_argument("--n-candidates", type=int, default=200)
    p.add_argument("--cold-refits", action="store_true", help="cold BOxCrete refit at every replay step (slow)")
    a = p.parse_args(argv)
    a.classes = ["concrete", "mortar"]
    warnings.filterwarnings("ignore")
    np.set_printoptions(precision=3)
    ok, why = boxcrete_available()
    print(f"BOxCrete runtime: {'available' if ok else 'UNAVAILABLE (' + why + ')'}; backend={a.backend}")
    t0 = time.time()
    obs = load_dataset()
    cmds = {"data": cmd_data, "evaluate": cmd_evaluate, "candidates": cmd_candidates,
            "replay": cmd_replay, "compare": cmd_compare}
    for c in (["data", "evaluate", "candidates", "compare"] if a.command == "all" else [a.command]):
        print(f"\n##### {c} #####")
        cmds[c](a, obs)
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

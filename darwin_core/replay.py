"""Steps 8-13: held-out oracle, sequential replay, acquisition vs random.

Label isolation: ``HeldOutOracle`` keeps held-out measurements in a private
attribute. The acquisition path only ever receives ``oracle.pool_features()``
(feature columns, no outcome columns) plus the incumbent computed from
TRAIN + already-revealed rows. Held-out labels reach code only through
``reveal()`` (one mixture, logged) or ``_evaluation_rows()`` (metrics only,
called by the evaluator, never by acquisition).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from darwin_core.acquisition import TARGET_AGE_DAYS, rank_candidates
from darwin_core.candidates import check_feasibility
from darwin_core.evaluation import evaluate_model
from darwin_core.ingestion import FEATURE_COLUMNS, mixture_table
from darwin_core.model import BoxcreteStrengthModel, make_model
from darwin_core.split import Split, make_split


class HeldOutOracle:
    def __init__(self, obs: pd.DataFrame, split: Split):
        held = obs[obs["Mix Name"].isin(split.heldout_mixes)]
        self.__labels = held.reset_index(drop=True).copy()  # private: name-mangled
        self._features = mixture_table(held)[["Mix Name"] + FEATURE_COLUMNS].reset_index(drop=True)
        self.split = split
        self.revealed: list[str] = []
        self.reveal_log: list[dict] = []

    def pool_features(self) -> pd.DataFrame:
        """Feature-only view of the held-out pool (no outcome columns)."""
        return self._features[~self._features["Mix Name"].isin(self.revealed)].copy()

    def reveal(self, mix_name: str, step: int | None = None) -> pd.DataFrame:
        if mix_name not in self.split.heldout_mixes:
            raise KeyError(f"{mix_name} is not in the held-out pool")
        if mix_name in self.revealed:
            raise ValueError(f"{mix_name} already revealed")
        rows = self.__labels[self.__labels["Mix Name"] == mix_name].copy()
        self.revealed.append(mix_name)
        self.reveal_log.append({"step": step, "mix": mix_name, "rows": int(len(rows)),
                                "ages_days": sorted(int(t) for t in rows["Time"]),
                                "provenance": {"kind": "measured", "source": "BOxCrete CSV (held-out, revealed by oracle)",
                                               "split_id": self.split.split_id}})
        return rows

    def _evaluation_rows(self, exclude: list[str] | None = None) -> pd.DataFrame:
        """Metrics-only access (evaluator). Never passed to acquisition."""
        ex = set(self.revealed) | set(exclude or [])
        return self.__labels[~self.__labels["Mix Name"].isin(ex)].copy()

    def _pool_max_28d(self, names) -> float:
        """Post-hoc regret reference only — computed after the run."""
        r = self.__labels[(self.__labels["Mix Name"].isin(names)) & (self.__labels["Time"] == TARGET_AGE_DAYS)]
        return float(r["strength_mpa"].max())


def best_28d(rows: pd.DataFrame) -> float:
    r = rows[rows["Time"] == TARGET_AGE_DAYS]["strength_mpa"]
    return float(r.max()) if len(r) else float("-inf")


def eligible_pool(pool: pd.DataFrame) -> list[str]:
    return [r["Mix Name"] for r in pool.to_dict("records") if not check_feasibility(r)]


def run_acquisition_replay(obs, material_class="concrete", seed=0, k=10, backend="auto",
                           cold_refits=False, warm_maxiter=60, log=print) -> dict:
    split = make_split(obs, material_class, seed)
    train = obs[obs["Mix Name"].isin(split.train_mixes)].copy()
    train["provenance_kind"] = "measured"
    oracle = HeldOutOracle(obs, split)
    eligible = set(eligible_pool(oracle.pool_features()))
    model = make_model(backend, seed, split.split_id).fit(train)
    log(f"[{split.split_id}] initial fit {model.label} v={model.version} ({model.fit_seconds}s); "
        f"pool={len(split.heldout_mixes)} eligible={len(eligible)}")
    incumbent = best_28d(train)
    steps, best_revealed = [], float("-inf")
    for step in range(1, k + 1):
        pool = oracle.pool_features()
        pool = pool[pool["Mix Name"].isin(eligible)]
        if pool.empty:
            log("pool exhausted")
            break
        ranking = rank_candidates(model, pool, incumbent)
        top = ranking.iloc[0]
        name = top["Mix Name"]
        mix_feats = pool[pool["Mix Name"] == name]
        pre_mu, pre_sd = model.predict(mix_feats, TARGET_AGE_DAYS, latent=False)
        rows = oracle.reveal(name, step)                       # step 9: only this mixture
        rows["provenance_kind"] = "measured"
        test_rows = oracle._evaluation_rows()                  # evaluator-only, excludes revealed
        mae_before = evaluate_model(model, test_rows)["mae_mpa"]
        train = pd.concat([train, rows], ignore_index=True)    # step 10
        if backend != "sklearn" and isinstance(model, BoxcreteStrengthModel) and not cold_refits:
            new = BoxcreteStrengthModel(seed, split.split_id, warm_start_from=model, warm_maxiter=warm_maxiter).fit(train)
        else:
            new = make_model(model.backend, seed, split.split_id).fit(train)
        post_mu, post_sd = new.predict(mix_feats, TARGET_AGE_DAYS, latent=False)
        mae_after = evaluate_model(new, test_rows)["mae_mpa"]
        measured = best_28d(rows)
        best_revealed = max(best_revealed, measured)
        incumbent = max(incumbent, measured)
        rec = {
            "step": step, "selected": name, "ei": float(top["ei"]),
            "pred_28d_before_mpa": {"value": round(float(pre_mu[0]), 3), "std": round(float(pre_sd[0]), 3),
                                    "provenance": model.provenance()},
            "measured_28d_mpa": {"value": None if measured == float("-inf") else round(measured, 3),
                                 "provenance": {"kind": "measured", "source": "BOxCrete CSV via oracle.reveal",
                                                "split_id": split.split_id, "seed": seed}},
            "pred_28d_after_mpa": {"value": round(float(post_mu[0]), 3), "std": round(float(post_sd[0]), 3),
                                   "provenance": new.provenance(fit_mode=getattr(new, "fit_mode", None))},
            "test_mae_before_mpa": mae_before, "test_mae_after_mpa": mae_after, "n_test_rows": int(len(test_rows)),
            "best_revealed_28d_mpa": round(best_revealed, 3),
            "metrics_provenance": {"kind": "computed", "source": "MAE on unrevealed held-out rows",
                                   "split_id": split.split_id, "seed": seed},
        }
        steps.append(rec)
        log(f"  step {step:2d} pick {name:>5}  pred28 {pre_mu[0]:6.2f}±{pre_sd[0]:5.2f} -> {post_mu[0]:6.2f}  "
            f"measured {rec['measured_28d_mpa']['value']}  test MAE {mae_before:.3f} -> {mae_after:.3f}  "
            f"({new.fit_seconds}s)")
        model = new
    return {"split_id": split.split_id, "seed": seed, "material_class": material_class,
            "model_label": model.label, "final_model_version": model.version,
            "eligible_pool": sorted(eligible), "pool_size": len(split.heldout_mixes),
            "pool_max_28d_mpa": oracle._pool_max_28d(sorted(eligible)),
            "train_best_28d_mpa": best_28d(obs[obs["Mix Name"].isin(split.train_mixes)]),
            "steps": steps, "reveal_log": oracle.reveal_log,
            "best_curve": [s["best_revealed_28d_mpa"] for s in steps]}


def run_random_replay(obs, material_class="concrete", seed=0, k=10) -> dict:
    """Same split/pool/eligibility/budget; selection by seeded RNG (no model)."""
    split = make_split(obs, material_class, seed)
    oracle = HeldOutOracle(obs, split)
    eligible = sorted(eligible_pool(oracle.pool_features()))
    rng = np.random.default_rng(10_000 + seed)
    order = list(rng.permutation(eligible))[:k]
    curve, best = [], float("-inf")
    for step, name in enumerate(order, 1):
        best = max(best, best_28d(oracle.reveal(name, step)))
        curve.append(round(best, 3))
    return {"split_id": split.split_id, "seed": seed, "order": order, "best_curve": curve,
            "pool_max_28d_mpa": oracle._pool_max_28d(eligible)}


def compare(obs, material_class="concrete", seeds=(0, 1, 2, 3, 4), k=10, backend="auto",
            cold_refits=False, log=print) -> dict:
    acq, rnd = [], []
    for s in seeds:
        acq.append(run_acquisition_replay(obs, material_class, s, k, backend, cold_refits, log=log))
        rnd.append(run_random_replay(obs, material_class, s, k))
        log(f"  seed {s}: acquisition best28 {acq[-1]['best_curve'][-1]:.2f}  random {rnd[-1]['best_curve'][-1]:.2f}  "
            f"pool max {acq[-1]['pool_max_28d_mpa']:.2f} MPa")
    A = np.array([a["best_curve"] for a in acq]); R = np.array([r["best_curve"] for r in rnd])
    pm = np.array([a["pool_max_28d_mpa"] for a in acq])[:, None]
    summary = {
        "material_class": material_class, "k": k, "seeds": list(seeds), "model_label": acq[0]["model_label"],
        "acq_best_mean": A.mean(0).round(3).tolist(), "acq_best_std": A.std(0, ddof=1).round(3).tolist(),
        "rand_best_mean": R.mean(0).round(3).tolist(), "rand_best_std": R.std(0, ddof=1).round(3).tolist(),
        "acq_regret_mean": (pm - A).mean(0).round(3).tolist(), "rand_regret_mean": (pm - R).mean(0).round(3).tolist(),
        "acq_found_pool_max": int((np.isclose(A[:, -1], pm[:, 0])).sum()),
        "rand_found_pool_max": int((np.isclose(R[:, -1], pm[:, 0])).sum()),
        "provenance": {"kind": "computed", "source": "replay over held-out real mixtures", "seed": list(seeds)},
    }
    d = A[:, -1] - R[:, -1]
    summary["final_diff_mean"] = round(float(d.mean()), 3)
    summary["final_diff_std"] = round(float(d.std(ddof=1)), 3)
    summary["verdict"] = ("acquisition better than random" if d.mean() > 0 and (d >= 0).all()
                          else "acquisition NOT clearly better than random" if d.mean() <= 0
                          else "acquisition better on average but not on every seed")
    return {"summary": summary, "acquisition_trials": acq, "random_trials": rnd}

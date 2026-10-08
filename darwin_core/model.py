"""Step 4: probabilistic strength models trained on TRAIN mixtures only.

Backends:
  * ``boxcrete``  — Meta BOxCrete ``SustainableConcreteModel.fit_strength_model``
    (V2 gated GP, real code from /tmp/boxcrete-src). Refit from scratch on the
    DARWIN train split; the pretrained ``strength_model.pt`` (trained on all
    data) is NEVER used.
  * ``sklearn``   — "DARWIN substitute GP (sklearn), NOT BOxCrete": Matern-2.5
    ARD + White kernel on the same real training rows. Used only if the torch
    stack is unavailable or explicitly requested.

All predictions are in MPa. ``predict(..., latent=True)`` returns the latent
mean strength std (used by acquisition); ``latent=False`` adds observation
noise (used for prediction-interval coverage).
"""

from __future__ import annotations

import hashlib
import sys
import time
import warnings

import numpy as np
import pandas as pd

from darwin_core.ingestion import FEATURE_COLUMNS, PSI_TO_MPA

BOXCRETE_SRC = "/tmp/boxcrete-src"
X_COLUMNS = FEATURE_COLUMNS + ["Time"]  # == boxcrete.utils.DEFAULT_X_COLUMNS
BOXCRETE_LABEL = "BOxCrete SustainableConcreteModel (V2 strength GP), refit on DARWIN train split"
SKLEARN_LABEL = "DARWIN substitute GP (sklearn), NOT BOxCrete"


def boxcrete_available() -> tuple[bool, str]:
    try:
        if BOXCRETE_SRC not in sys.path:
            sys.path.insert(0, BOXCRETE_SRC)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import torch  # noqa: F401
            from boxcrete.concrete_model import SustainableConcreteModel  # noqa: F401
        return True, "ok"
    except Exception as e:  # pragma: no cover - depends on env
        return False, f"{type(e).__name__}: {e}"


def _train_digest(train_rows: pd.DataFrame) -> str:
    key = ",".join(sorted(f"{m}@{t}" for m, t in zip(train_rows["Mix Name"], train_rows["Time"])))
    return hashlib.sha1(key.encode()).hexdigest()[:8]


class StrengthModel:
    backend: str = ""
    label: str = ""

    def __init__(self, seed: int = 0, split_id: str | None = None):
        self.seed = seed
        self.split_id = split_id
        self.version = None
        self.n_train_rows = 0
        self.fit_seconds = None

    def fit(self, train_rows: pd.DataFrame) -> "StrengthModel":
        """train_rows: observation rows (features + Time + 'Strength (Mean)' psi)."""
        t0 = time.time()
        self._fit(train_rows.reset_index(drop=True))
        self.fit_seconds = round(time.time() - t0, 2)
        self.n_train_rows = len(train_rows)
        self.version = f"{self.backend}-{_train_digest(train_rows)}-n{len(train_rows)}"
        return self

    def predict(self, mixes: pd.DataFrame, age_days, latent: bool = False):
        """mixes: feature frame (one row per prediction). age_days scalar or array."""
        X = mixes[FEATURE_COLUMNS].to_numpy(dtype=float)
        t = np.broadcast_to(np.asarray(age_days, dtype=float), (len(X),))
        return self._predict(np.column_stack([X, t]), latent)

    def provenance(self, **extra) -> dict:
        return {"kind": "predicted", "source": self.label, "model_version": self.version,
                "seed": self.seed, "split_id": self.split_id, **extra}


class BoxcreteStrengthModel(StrengthModel):
    backend = "boxcrete"
    label = BOXCRETE_LABEL

    def __init__(self, seed: int = 0, split_id: str | None = None, warm_start_from=None,
                 warm_maxiter: int = 60):
        super().__init__(seed, split_id)
        self._warm_from = warm_start_from
        self._warm_maxiter = warm_maxiter
        self.fit_mode = None

    def _fit(self, rows: pd.DataFrame) -> None:
        ok, why = boxcrete_available()
        if not ok:
            raise RuntimeError(f"BOxCrete runtime unavailable: {why}")
        import torch
        from boxcrete.concrete_model import SustainableConcreteModel
        from boxcrete.utils import load_concrete_strength

        torch.manual_seed(self.seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = load_concrete_strength(rows, dtype=torch.float64)
            assert list(data.X_columns) == X_COLUMNS, data.X_columns
            # load_concrete_strength silently drops rows with NaN in any required
            # column (incl. GWP); refuse to fit on fewer rows than were given.
            if data.X.shape[0] != len(rows):
                raise RuntimeError(f"BOxCrete loader dropped {len(rows) - data.X.shape[0]} of {len(rows)} "
                                   "rows (missing values in required columns)")
            m = SustainableConcreteModel(strength_days=[1, 28])
            if self._warm_from is None:
                # Cold fit: BOxCrete's own production fit path, untouched.
                m.fit_strength_model(data)
                self.fit_mode = "cold (SustainableConcreteModel.fit_strength_model)"
            else:
                m.strength_model = self._warm_refit(data)
                self.fit_mode = f"warm-start refit (maxiter={self._warm_maxiter})"
        self._scm = m
        self._gp = m.strength_model
        self._gp.eval()
        self._y_scale = float(self._gp._study_y_std.item())  # psi (max-scaling, mean 0)

    def _warm_refit(self, data):
        """Rebuild BOxCrete's V2 GP on the new data (fit_strength_gp with
        max_optimizer_iter=0, the same route BOxCrete's pretrained loader uses),
        copy the previous model's hyperparameters in, then re-optimise the MLL
        for a capped number of L-BFGS iterations. Same model class, kernel,
        likelihood and objective — only the starting point differs."""
        import torch
        from botorch.fit import fit_gpytorch_mll
        from boxcrete.strength_model import fit_strength_gp
        from gpytorch.mlls import ExactMarginalLogLikelihood

        X, Y, Yvar, X_bounds = data.strength_data
        gp = fit_strength_gp(X=X, Y=Y, Yvar=Yvar, X_bounds=X_bounds, seed=self.seed,
                             max_optimizer_iter=0)
        prev = dict(self._warm_from._gp.named_parameters())
        with torch.no_grad():
            for name, p in gp.named_parameters():
                if name in prev and prev[name].shape == p.shape:
                    p.copy_(prev[name])
        mll = ExactMarginalLogLikelihood(gp.likelihood, gp)
        fit_gpytorch_mll(mll, optimizer_kwargs={"options": {"maxiter": self._warm_maxiter}},
                         max_attempts=1)
        gp.eval()
        return gp

    def _predict(self, X: np.ndarray, latent: bool):
        import torch

        with torch.no_grad(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            post = self._gp.posterior(torch.as_tensor(X, dtype=torch.float64))
            mean = post.mean.squeeze(-1).numpy()
            var = post.variance.squeeze(-1).clamp_min(0).numpy()
            if not latent:
                # V2 gated noise: sigma^2 * h(t)^2, h ~= 1 for t >= 1 day.
                lik = self._gp.likelihood
                sigma2 = float(lik.noise_covar.noise.flatten()[0])
                h = lik._gate(torch.as_tensor(X[:, -1], dtype=torch.float64)).numpy()
                var = var + sigma2 * h**2
        s = self._y_scale * PSI_TO_MPA
        return mean * s, np.sqrt(var) * s


class SklearnSubstituteModel(StrengthModel):
    backend = "sklearn"
    label = SKLEARN_LABEL

    @staticmethod
    def _tx(X: np.ndarray) -> np.ndarray:
        X = X.copy()
        X[:, -1] = np.log1p(X[:, -1])
        return X

    def _fit(self, rows: pd.DataFrame) -> None:
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        from sklearn.preprocessing import StandardScaler

        X = self._tx(rows[X_COLUMNS].to_numpy(float))
        y = rows["Strength (Mean)"].to_numpy(float) * PSI_TO_MPA
        self._sc = StandardScaler().fit(X)
        Xs = np.nan_to_num(self._sc.transform(X))
        k = ConstantKernel(1.0) * Matern(length_scale=np.ones(X.shape[1]), nu=2.5,
                                         length_scale_bounds=(1e-2, 1e3)) + WhiteKernel(1e-2, (1e-6, 1.0))
        self._gp = GaussianProcessRegressor(k, normalize_y=True, n_restarts_optimizer=2,
                                            random_state=self.seed).fit(Xs, y)

    def _predict(self, X: np.ndarray, latent: bool):
        Xs = np.nan_to_num(self._sc.transform(self._tx(X)))
        mean, std = self._gp.predict(Xs, return_std=True)
        if latent:
            noise = self._gp.kernel_.k2.noise_level * self._gp._y_train_std**2
            std = np.sqrt(np.maximum(std**2 - noise, 0))
        return mean, std


def make_model(backend: str = "auto", seed: int = 0, split_id: str | None = None) -> StrengthModel:
    if backend == "auto":
        backend = "boxcrete" if boxcrete_available()[0] else "sklearn"
    cls = {"boxcrete": BoxcreteStrengthModel, "sklearn": SklearnSubstituteModel}[backend]
    return cls(seed=seed, split_id=split_id)

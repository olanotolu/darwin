"""DARWIN core — P0 MVP scientific loop on the real BOxCrete dataset.

Modules:
    provenance   typed values {measured, predicted, assumed, computed}
    ingestion    load + verify the real CSV, psi->MPa, mixture/observation tables
    split        leakage-safe mixture-level held-out split
    model        strength models (BOxCrete SustainableConcreteModel | sklearn substitute)
    baselines    train-mean and ridge baselines
    evaluation   MAE / RMSE (MPa) / 95% PI coverage on unseen mixtures
    candidates   deterministic feasibility + seeded candidate generation
    acquisition  expected improvement on predicted 28-day strength (numpy/scipy only)
    replay       held-out oracle, sequential replay loop, acquisition-vs-random
"""

__version__ = "0.2.0-p1"

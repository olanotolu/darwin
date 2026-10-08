"""Provenance typing for every value DARWIN emits."""

from __future__ import annotations

from typing import Any

KINDS = ("measured", "predicted", "assumed", "computed")


def prov(
    kind: str,
    source: str,
    *,
    model_version: str | None = None,
    seed: int | None = None,
    split_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a provenance record. Raises on an unknown kind."""
    if kind not in KINDS:
        raise ValueError(f"provenance kind must be one of {KINDS}, got {kind!r}")
    rec = {
        "kind": kind,
        "source": source,
        "model_version": model_version,
        "seed": seed,
        "split_id": split_id,
    }
    rec.update(extra)
    return rec


def typed(value: Any, unit: str | None, provenance: dict[str, Any]) -> dict[str, Any]:
    """A single typed value: {value, unit, provenance}."""
    if provenance.get("kind") not in KINDS:
        raise ValueError("typed() requires a provenance record from prov()")
    return {"value": value, "unit": unit, "provenance": provenance}

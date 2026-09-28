"""VLM answer → likelihood (stage E)."""

from __future__ import annotations

import numpy as np

from ..cells import CellTree
from ..models.vlm import VlmResult
from .base import Evidence, WeightedPoint


def vlm_evidence(result: VlmResult, tree: CellTree, weight: float) -> Evidence | None:
    a = result.answer
    if a.confidence <= 0.01 and not a.country_code:
        return None
    n = len(tree)
    c = float(a.confidence)
    r = np.ones(n)
    points: list[WeightedPoint] = []
    if a.latitude is not None and a.longitude is not None:
        # "confidence = P(within radius_km)": a Gaussian bump whose sigma matches the radius.
        d = tree.distances_km(a.latitude, a.longitude)
        sigma = np.sqrt(a.radius_km**2 + tree.scale_km**2)
        r = r * (0.05 + 40.0 * np.exp(-0.5 * (d / sigma) ** 2))
        points.append(WeightedPoint(a.latitude, a.longitude, c, sigma_km=a.radius_km))
    cc = a.country_code.strip().upper()
    if len(cc) == 2:
        r = r * np.array([6.0 if iso == cc else 0.3 for iso in tree.iso2])
    place = ", ".join(p for p in (a.city, a.region, cc) if p) or "an unspecified location"
    clues = "; ".join(f"{cl.clue} → {cl.implies}" for cl in a.clues[:4])
    return Evidence(
        source="vlm",
        key="vlm",
        label=f"Visual reasoning ({result.model})",
        loglik=np.log((1.0 - c) + c * r),
        weight=weight,
        points=points,
        detail=f"Suggests {place} ({c:.0%}, ±{a.radius_km:.0f} km). {clues}".strip(),
    )

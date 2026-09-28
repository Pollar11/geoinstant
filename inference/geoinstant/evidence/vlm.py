"""VLM answer → likelihood: a mixture over its candidate places (stage E)."""

from __future__ import annotations

import numpy as np

from ..cells import CellTree
from ..models.vlm import VlmResult
from .base import Evidence, WeightedPoint


def vlm_evidence(result: VlmResult, tree: CellTree, weight: float) -> Evidence | None:
    cands = [c for c in result.answer.candidates if c.probability > 0.01]
    if not cands:
        return None
    n = len(tree)
    total = min(sum(c.probability for c in cands), 1.0)
    mix = np.full(n, 1.0 - total)  # remaining mass: "somewhere else"
    points: list[WeightedPoint] = []
    for c in cands:
        r = np.ones(n)
        if c.latitude is not None and c.longitude is not None:
            d = tree.distances_km(c.latitude, c.longitude)
            sigma = np.sqrt(c.radius_km**2 + tree.scale_km**2)
            r = 0.05 + 40.0 * np.exp(-0.5 * (d / sigma) ** 2)
            points.append(WeightedPoint(c.latitude, c.longitude, c.probability, sigma_km=c.radius_km))
        cc = c.country_code.strip().upper()
        if len(cc) == 2:
            r = r * np.array([6.0 if iso == cc else 0.3 for iso in tree.iso2])
        mix += c.probability * r
    strong = [cl for cl in result.answer.clues if cl.strength == "strong"][:3]
    best = cands[0]
    detail = f"Suggests {best.label} ({best.probability:.0%}, ±{best.radius_km:.0f} km)"
    if strong:
        detail += ". " + "; ".join(f"{cl.clue} → {cl.implies}" for cl in strong)
    return Evidence(
        source="vlm",
        key="vlm",
        label=f"Visual reasoning ({result.model})",
        loglik=np.log(mix),
        weight=weight,
        points=points,
        detail=detail,
    )

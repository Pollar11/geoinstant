"""Product-of-experts fusion → hierarchy decision → mean-shift coordinate + radius."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .cells import LEVELS, CellTree
from .evidence.base import Evidence, WeightedPoint
from .gazetteer import Gazetteer
from .geo import haversine_km, log_softmax, logsumexp, mean_shift_mode, weighted_quantile
from .schemas import Candidate, EvidenceItem, HierarchyNode, Place, RegionBox, Resolution

LEVEL_TO_API = {"continent": "continent", "country": "country", "admin1": "region", "leaf": "city"}
LEVEL_TO_RESOLUTION: dict[str, Resolution] = {
    "continent": "continent",
    "country": "country",
    "admin1": "region",
    "leaf": "city",
}
MIN_RADIUS_KM = {"world": 5000.0, "continent": 1000.0, "country": 100.0, "admin1": 25.0}


@dataclass
class Fused:
    latitude: float
    longitude: float
    radius_km: float
    confidence: float  # 0..100
    resolution: Resolution
    place: Place
    hierarchy: list[HierarchyNode]
    candidates: list[Candidate]
    evidence: list[EvidenceItem]
    regions: list[RegionBox]
    explanation: str
    posterior: NDArray[np.float64]


def fuse(
    tree: CellTree,
    gaz: Gazetteer,
    evidences: list[Evidence],
    min_mass: float = 0.3,
    temperature: float = 1.0,
    min_lift: float = 1.5,
) -> Fused:
    n = len(tree)
    total = tree.log_prior.copy()
    for ev in evidences:
        total += ev.weight * ev.loglik
    logp = log_softmax(total / max(temperature, 1e-3))
    p = np.exp(logp)
    p0 = np.exp(log_softmax(tree.log_prior))

    # ---- 2. hierarchical decision ---------------------------------------------------------
    mask = np.ones(n, dtype=bool)
    hierarchy: list[HierarchyNode] = []
    accepted_level: str | None = None
    accepted_mask = mask.copy()
    accepted_mass = 0.0
    for level in LEVELS:
        lv = tree.levels[level]
        marg = tree.marginal(p, level, mask)
        node = int(np.argmax(marg))
        mass = float(marg[node])
        prior_mass = float(tree.marginal(p0, level, mask)[node])
        mask = mask & (lv.parent == node)
        code = lv.keys[node] if level in ("continent", "country") else ""
        hierarchy.append(HierarchyNode(level=LEVEL_TO_API[level], name=lv.labels[node], code=code, probability=min(mass, 1.0)))
        # Require enough mass and a lift over the prior.
        if mass >= min_mass and mass >= min_lift * prior_mass:
            accepted_level, accepted_mask, accepted_mass = level, mask.copy(), mass

    best_leaf = int(np.argmax(np.where(accepted_mask, p, -1.0)))

    # ---- 3. refinement --------------------------------------------------------------------
    lat, lon, radius = _refine(tree, evidences, p, accepted_level, accepted_mask, best_leaf)
    if accepted_level == "leaf" and radius > 2.0 * float(tree.scale_km[best_leaf]):
        # Points spread wider than the cell: back off to region level.
        admin = tree.levels["admin1"]
        accepted_level, accepted_mask = "admin1", admin.parent == admin.parent[best_leaf]
        accepted_mass = float(p[accepted_mask].sum())
        hierarchy[3] = hierarchy[3].model_copy(update={"probability": min(hierarchy[3].probability, accepted_mass)})
        lat, lon, radius = _refine(tree, evidences, p, accepted_level, accepted_mask, best_leaf)

    resolution: Resolution = LEVEL_TO_RESOLUTION.get(accepted_level or "", "world")
    if resolution == "city" and radius < 1.0:
        resolution = "street"
    confidence = round(100.0 * accepted_mass, 1) if accepted_level else round(100.0 * float(p.max()), 1)

    place = _place(gaz, lat, lon)
    # Name the place at the claimed precision.
    place = place.model_copy(update={"display_name": _display_name(resolution, place)})
    items = _attribution(evidences, accepted_mask)
    regions = [
        RegionBox(box=r.box, label=r.label, score=round(r.score, 3), source=r.source) for ev in evidences for r in ev.regions
    ]
    top = np.argsort(-p)[:5]
    candidates = [
        Candidate(
            latitude=round(float(tree.lat[i]), 6),
            longitude=round(float(tree.lon[i]), 6),
            name=f"{tree.name[i]}, {tree.levels['country'].labels[tree.levels['country'].parent[i]]}",
            country_code=tree.iso2[i],
            probability=round(float(p[i]), 4),
        )
        for i in top
    ]
    return Fused(
        latitude=lat,
        longitude=lon,
        radius_km=float(radius),
        confidence=confidence,
        resolution=resolution,
        place=place,
        hierarchy=hierarchy,
        candidates=candidates,
        evidence=items,
        regions=regions,
        explanation=_explain(resolution, place, confidence, items, bool(evidences)),
        posterior=p,
    )


def _refine(
    tree: CellTree,
    evidences: list[Evidence],
    p: NDArray[np.float64],
    level: str | None,
    mask: NDArray[np.bool_],
    best_leaf: int,
) -> tuple[float, float, float]:
    """Coordinate = densest cluster of candidate points inside the chosen node; radius = spread."""
    points = _points_in(evidences, tree, mask, p)
    bandwidth = float(np.clip(tree.scale_km[best_leaf] / 3.0, 0.3, 50.0))
    if points and level is not None:
        lat, lon = mean_shift_mode([q.lat for q in points], [q.lon for q in points], [q.weight for q in points], bandwidth)
    else:
        lat, lon = float(tree.lat[best_leaf]), float(tree.lon[best_leaf])

    if level == "leaf":
        if not points:
            return lat, lon, float(tree.scale_km[best_leaf])
        d = haversine_km(lat, lon, [q.lat for q in points], [q.lon for q in points]) + np.array([q.sigma_km for q in points])
        return lat, lon, max(weighted_quantile(d, [q.weight for q in points], 0.68), bandwidth)
    d_leaves = tree.distances_km(lat, lon)[mask] + tree.scale_km[mask]
    spread = weighted_quantile(d_leaves, p[mask], 0.68)
    return lat, lon, max(spread, MIN_RADIUS_KM[level or "world"])


def _points_in(evidences: list[Evidence], tree: CellTree, mask: NDArray[np.bool_], p: NDArray[np.float64]) -> list[WeightedPoint]:
    # Normalise point mass per source to its fusion weight.
    raw = [(q, ev.weight / sum(x.weight for x in ev.points)) for ev in evidences for q in ev.points if q.weight > 0]
    if not raw:
        return []
    unknown = [i for i, (q, _) in enumerate(raw) if q.leaf < 0]
    leaves = np.array([q.leaf for q, _ in raw], dtype=np.int64)
    if unknown:
        leaves[unknown] = tree.nearest_leaf(
            np.array([raw[i][0].lat for i in unknown]), np.array([raw[i][0].lon for i in unknown])
        )
    pmax = float(p[mask].max()) if mask.any() else 1.0
    out = []
    for (q, w_ev), leaf in zip(raw, leaves, strict=True):
        if mask[leaf]:
            # Weight each point by its own score, its source's weight, and how probable its cell is.
            out.append(WeightedPoint(q.lat, q.lon, q.weight * w_ev * float(p[leaf]) / pmax, int(leaf), q.sigma_km))
    return out


def _attribution(evidences: list[Evidence], mask: NDArray[np.bool_]) -> list[EvidenceItem]:
    """Per-source likelihood ratio of the chosen node vs. the whole world."""
    items: list[tuple[float, EvidenceItem]] = []
    n_in = int(mask.sum()) or 1
    n = mask.shape[0]
    for ev in evidences:
        weighted = ev.weight * ev.loglik
        inside = float(logsumexp(weighted[mask]) - np.log(n_in)) if mask.any() else 0.0
        everywhere = float(logsumexp(weighted) - np.log(n))
        c = inside - everywhere
        direction = "supports" if c > 0.05 else "contradicts" if c < -0.05 else "neutral"
        items.append(
            (
                c,
                EvidenceItem(
                    source=ev.source,
                    label=ev.label,
                    detail=ev.detail,
                    likelihood_ratio=round(float(np.exp(np.clip(c, -50, 50))), 3),
                    direction=direction,
                ),
            )
        )
    items.sort(key=lambda t: -abs(t[0]))
    return [it for _, it in items]


def _place(gaz: Gazetteer, lat: float, lon: float) -> Place:
    pl = gaz.reverse(lat, lon)
    return Place(
        name=pl.name,
        admin1=pl.admin1,
        country=pl.country,
        country_code=pl.country_code,
        continent=pl.continent,
        display_name=pl.display_name,
        distance_km=round(pl.distance_km, 2),
    )


def _display_name(resolution: Resolution, place: Place) -> str:
    if resolution in ("exact", "street", "city"):
        return place.display_name
    if resolution == "region":
        return f"{place.admin1}, {place.country}" if place.admin1 and place.admin1 != place.country else place.country
    if resolution == "country":
        return place.country
    if resolution == "continent":
        return place.continent
    return "Unknown location"


def _fmt_lr(x: float) -> str:
    return f"×{x:,.0f}" if x >= 10 else f"×{x:.1f}"


def _explain(resolution: Resolution, place: Place, confidence: float, items: list[EvidenceItem], had_evidence: bool) -> str:
    if not had_evidence:
        return "No usable location evidence was found in this image."
    where = place.display_name
    head = (
        f"Best estimate: {where} ({resolution}-level, {confidence:.0f}% confidence)."
        if resolution != "world"
        else "The evidence is too weak to commit to a region; showing the single most likely area."
    )
    pro = [i for i in items if i.direction == "supports"][:3]
    con = [i for i in items if i.direction == "contradicts"][:1]
    parts = [head]
    if pro:
        parts.append("Key evidence: " + "; ".join(f"{i.label} ({_fmt_lr(i.likelihood_ratio)})" for i in pro) + ".")
    if con:
        parts.append(f"Conflicting: {con[0].label}.")
    return " ".join(parts)

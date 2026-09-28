"""Evidence = log-likelihood over leaf cells; every stage emits one."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from ..cells import CellTree


@dataclass(frozen=True)
class WeightedPoint:
    """A candidate coordinate used for sub-cell refinement (e.g. a retrieved neighbour)."""

    lat: float
    lon: float
    weight: float
    leaf: int = -1
    sigma_km: float = 0.0  # the point's own spatial uncertainty (0 for a geotagged photo)


@dataclass(frozen=True)
class Region:
    """An image region that contributed to the prediction (drawn by the UI)."""

    box: tuple[float, float, float, float]
    label: str
    score: float
    source: str


@dataclass
class Evidence:
    source: str  # "classifier" | "retrieval" | "cue" | "text" | "vlm"
    key: str  # stable id, e.g. "cue:joshua_tree", "text:script:cyrillic"
    label: str  # human-readable description
    loglik: NDArray[np.float64]  # (L,)
    weight: float = 1.0
    points: list[WeightedPoint] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class Observation:
    """A discrete clue with a likelihood-ratio description (see data/cue_priors.json)."""

    key: str
    label: str
    score: float  # detector / OCR confidence in [0, 1]
    countries: dict[str, float] = field(default_factory=dict)
    regions: tuple[tuple[float, float, float, float], ...] = ()  # (lat, lon, sigma_km, peak)
    lat_band: tuple[float, float, bool, float] | None = None  # (min, max, use_abs, lr)
    other: float = 0.2
    box: tuple[float, float, float, float] | None = None
    detail: str = ""


def observation_loglik(obs: Observation, tree: CellTree) -> NDArray[np.float64]:
    """ℓ(c) = log((1 - s) + s · r(c)), r = likelihood ratio, s = detection score (Jeffrey's rule)."""
    n = len(tree)
    r = np.ones(n)
    if obs.countries:
        r *= np.array([obs.countries.get(c, obs.other) for c in tree.iso2])
    if obs.regions:
        bump = np.full(n, obs.other)
        for lat, lon, sigma, peak in obs.regions:
            # Distances from the region centre to each leaf, softened by the leaf's own size
            # so a big cell that overlaps the region still gets credit.
            d = tree.distances_km(lat, lon)
            spread = np.sqrt(sigma**2 + tree.scale_km**2)
            bump += peak * np.exp(-0.5 * (d / spread) ** 2)
        r *= bump
    if obs.lat_band is not None:
        lo, hi, use_abs, lr = obs.lat_band
        leaf_lat = np.abs(tree.lat) if use_abs else tree.lat
        r *= np.where((leaf_lat >= lo) & (leaf_lat <= hi), lr, obs.other)
    s = float(np.clip(obs.score, 0.0, 1.0))
    return np.log((1.0 - s) + s * r)


def observation_evidence(obs: Observation, tree: CellTree, source: str, weight: float) -> Evidence:
    regions = [Region(obs.box, obs.label, obs.score, source)] if obs.box else []
    points = [WeightedPoint(lat, lon, obs.score * peak, sigma_km=sigma) for lat, lon, sigma, peak in obs.regions]
    return Evidence(
        source=source,
        key=f"{source}:{obs.key}",
        label=obs.label,
        loglik=observation_loglik(obs, tree),
        weight=weight,
        points=points,
        regions=regions,
        detail=obs.detail,
    )

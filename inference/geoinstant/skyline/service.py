"""Skyline search: coarse match over a viewpoint index, then refine the best candidates on the DEM."""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..geo import haversine_km
from .dem import Raster, TileStore, mosaic
from .extract import Profile
from .horizon import KM_PER_DEG, grid, panorama
from .match import Match, match, relief

log = logging.getLogger(__name__)

INDEX_STEP = 1.0  # azimuth step of stored panoramas (deg)
REFINE_STEP = 0.5
MIN_RELIEF_DEG = 0.3

BBox = tuple[float, float, float, float]  # south, west, north, east


@dataclass
class ViewIndex:
    lat: NDArray[np.float64]
    lon: NDArray[np.float64]
    elev: NDArray[np.float32]
    pano: NDArray[np.float16]
    spacing_km: float
    bbox: BBox
    prebuilt: bool = True

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path, lat=self.lat, lon=self.lon, elev=self.elev, pano=self.pano, spacing_km=self.spacing_km, bbox=np.array(self.bbox)
        )

    @classmethod
    def load(cls, path: Path) -> ViewIndex:
        z = np.load(path)
        b = z["bbox"]
        return cls(
            z["lat"], z["lon"], z["elev"], z["pano"], float(z["spacing_km"]), (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        )

    def covers(self, bbox: BBox) -> bool:
        s, w, n, e = self.bbox
        return s <= bbox[0] and w <= bbox[1] and n >= bbox[2] and e >= bbox[3]

    def within(self, bbox: BBox) -> NDArray[np.bool_]:
        s, w, n, e = bbox
        return (self.lat >= s) & (self.lat <= n) & (self.lon >= w) & (self.lon <= e)


@dataclass
class Candidate:
    lat: float
    lon: float
    elev: float
    m: Match


@dataclass
class SearchResult:
    status: str  # ok | too_flat | no_area | area_too_large | no_data
    message: str
    relief: float
    bbox: BBox | None = None
    viewpoints: int = 0
    spacing_km: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    heat: list[tuple[float, float, float]] = field(default_factory=list)
    confidence: float = 0.0  # 0..1: how much better the best candidate fits than the runner-up
    timings: dict[str, float] = field(default_factory=dict)


def area_km2(b: BBox) -> float:
    s, w, n, e = b
    return (n - s) * KM_PER_DEG * (e - w) * KM_PER_DEG * math.cos(math.radians((s + n) / 2))


def margin_box(b: BBox, km: float) -> BBox:
    s, w, n, e = b
    dl = km / KM_PER_DEG
    do = km / (KM_PER_DEG * math.cos(math.radians((s + n) / 2)))
    return (s - dl, w - do, n + dl, e + do)


def build_index(dem: Raster, bbox: BBox, spacing_km: float, max_km: float, workers: int = 4) -> ViewIndex:
    lat, lon = grid(*bbox, spacing_km)
    elev = dem.sample(lat, lon)
    keep = elev > 0  # skip open sea
    lat, lon, elev = lat[keep], lon[keep], elev[keep]

    def one(i: int) -> NDArray[np.float32]:
        return panorama(dem, float(lat[i]), float(lon[i]), step_deg=INDEX_STEP, max_km=max_km, n_dist=200)

    with ThreadPoolExecutor(workers) as pool:
        panos = list(pool.map(one, range(len(lat))))
    pano = np.stack(panos).astype(np.float16) if panos else np.zeros((0, int(360 / INDEX_STEP)), np.float16)
    return ViewIndex(lat, lon, elev, pano, spacing_km, bbox)


class SkylineService:
    def __init__(
        self, index_dir: Path, dem_dir: Path, tile_url: str | None, max_area_km2: float, max_km: float, max_viewpoints: int = 3000
    ) -> None:
        self.index_dir = index_dir
        self.store = TileStore(dem_dir, tile_url or None)
        self.dem_available = bool(tile_url) or any(dem_dir.glob("*.hgt"))
        self.max_area = max_area_km2
        self.max_km = max_km
        self.max_viewpoints = max_viewpoints
        self.indexes: list[ViewIndex] = []
        for p in sorted(index_dir.glob("*.npz")) if index_dir.exists() else []:
            ix = ViewIndex.load(p)
            ix.prebuilt = not p.name.startswith("ondemand_")
            self.indexes.append(ix)
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._mosaic: tuple[BBox, Raster] | None = None
        log.info("Skyline: %d prebuilt regions, DEM %s", len(self.indexes), "on" if self.dem_available else "off")

    @property
    def coverage(self) -> list[BBox]:
        return [ix.bbox for ix in self.indexes if ix.prebuilt]

    def _dem(self, need: BBox, step: int) -> Raster:
        """Mosaic covering ``need`` (cached; tile loads take seconds)."""
        with self._lock:
            if self._mosaic:
                (s, w, n, e), r = self._mosaic
                if r.res * 3600 <= step * 1.01 and s <= need[0] and w <= need[1] and n >= need[2] and e >= need[3]:
                    return r
            r = mosaic(self.store, *need, step=step)
            h, w = r.data.shape  # remember the full tile-aligned extent, not just what was asked
            self._mosaic = ((r.north - (h - 1) * r.res, r.west, r.north, r.west + (w - 1) * r.res), r)
            return r

    def _build_for(self, bbox: BBox) -> ViewIndex | None:
        """Build (and cache) a viewpoint index for an area not covered by a prebuilt region."""
        if not self.dem_available:
            return None
        with self._build_lock:
            existing = next((i for i in self.indexes if i.covers(bbox)), None)
            if existing:
                return existing
            return self._build_locked(bbox)

    def _build_locked(self, bbox: BBox) -> ViewIndex:
        key = hashlib.sha1(",".join(f"{v:.3f}" for v in bbox).encode()).hexdigest()[:12]
        path = self.index_dir / f"ondemand_{key}.npz"
        if path.exists():
            ix = ViewIndex.load(path)
        else:
            spacing = max(0.5, math.sqrt(area_km2(bbox) / self.max_viewpoints))
            dem = self._dem(margin_box(bbox, self.max_km + 2), step=2)
            ix = build_index(dem, bbox, spacing, self.max_km)
            ix.save(path)
        ix.prebuilt = False
        self.indexes.append(ix)
        return ix

    def search(self, profile: Profile, bbox: BBox | None, top: int = 5) -> SearchResult:
        t0 = time.perf_counter()
        rel = relief(profile)
        if rel < MIN_RELIEF_DEG:
            return SearchResult("too_flat", "The skyline is too flat to match. Trace the mountain ridge by hand.", rel)
        if bbox is None:
            if not self.indexes:
                return SearchResult("no_area", "Choose a search area on the map (zoom to where you think it is).", rel)
            bbox = self.indexes[0].bbox
        ix = next((i for i in self.indexes if i.covers(bbox)), None) or next(
            (i for i in self.indexes if i.prebuilt and i.within(bbox).any()), None
        )
        if ix is None:
            if area_km2(bbox) > self.max_area:
                return SearchResult("area_too_large", f"Zoom in: search areas must be under {self.max_area:,.0f} km².", rel, bbox)
            ix = self._build_for(bbox)
        if ix is None:
            return SearchResult("no_data", "No elevation data for this area.", rel, bbox)
        t_index = time.perf_counter()
        sel = np.where(ix.within(bbox))[0]
        if sel.size == 0:
            return SearchResult("no_data", "No land viewpoints in this area.", rel, bbox)

        coarse = match(profile, ix.pano[sel], INDEX_STEP)
        t_match = time.perf_counter()
        heat = [(float(ix.lat[sel[m.index]]), float(ix.lon[sel[m.index]]), round(m.score, 4)) for m in coarse[:400]]

        # Non-maximum suppression, then refine each on a finer grid around it.
        picks: list[Match] = []
        for m in coarse:
            la, lo = ix.lat[sel[m.index]], ix.lon[sel[m.index]]
            if all(haversine_km(la, lo, ix.lat[sel[p.index]], ix.lon[sel[p.index]]) > 2.5 * ix.spacing_km for p in picks):
                picks.append(m)
            if len(picks) == top:
                break
        cands = [
            Candidate(float(ix.lat[sel[m.index]]), float(ix.lon[sel[m.index]]), float(ix.elev[sel[m.index]]), m) for m in picks
        ]
        if self.dem_available:
            cands = [self._refine(profile, c, ix.spacing_km) for c in cands]
            cands.sort(key=lambda c: c.m.rmse)
        # A unique fit beats the runner-up clearly; ambiguous skylines produce near-ties.
        conf = 0.0
        if len(cands) > 1 and cands[1].m.rmse > 0:
            conf = float(np.clip((1 - cands[0].m.rmse / cands[1].m.rmse - 0.15) / 0.45, 0, 1))
        best = cands[0].m.rmse if cands else 1.0
        for c in cands:  # scores relative to the best candidate
            c.m = Match(c.m.index, c.m.azimuth, c.m.fov, c.m.rmse, float(np.exp(-0.5 * ((c.m.rmse - best) / 0.1) ** 2)))
        t_end = time.perf_counter()
        return SearchResult(
            "ok",
            "",
            rel,
            bbox,
            int(sel.size),
            ix.spacing_km,
            cands,
            heat,
            conf,
            {
                "index": round((t_index - t0) * 1000, 1),
                "match": round((t_match - t_index) * 1000, 1),
                "refine": round((t_end - t_match) * 1000, 1),
                "total": round((t_end - t0) * 1000, 1),
            },
        )

    def _refine(self, profile: Profile, c: Candidate, spacing_km: float, n: int = 7) -> Candidate:
        box = margin_box((c.lat, c.lon, c.lat, c.lon), spacing_km)
        dem = self._dem(margin_box(box, self.max_km + 2), step=1)
        lat, lon = grid(*box, 2 * spacing_km / (n - 1))
        with ThreadPoolExecutor(4) as pool:
            panos = np.stack(
                list(
                    pool.map(
                        lambda ab: panorama(dem, float(ab[0]), float(ab[1]), step_deg=REFINE_STEP, max_km=self.max_km),
                        zip(lat, lon, strict=True),
                    )
                )
            )
        fovs = tuple(float(f) for f in np.arange(max(20.0, c.m.fov - 8), c.m.fov + 9, 2.0))
        m = match(profile, panos, REFINE_STEP, fovs)[0]
        if m.rmse >= c.m.rmse:
            return c
        i = m.index
        return Candidate(float(lat[i]), float(lon[i]), float(dem.sample(lat[i : i + 1], lon[i : i + 1])[0]), m)

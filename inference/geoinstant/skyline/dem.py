"""Elevation data: SRTM .hgt tiles (1° × 1°), fetched on demand and mosaicked for sampling."""

from __future__ import annotations

import gzip
import logging
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

# Public SRTM-derived tiles (AWS Open Data "Terrain Tiles", 1 arc-second where available).
DEFAULT_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{ns}/{name}.hgt.gz"
VOID = -32768


def tile_name(lat: int, lon: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def read_hgt(data: bytes) -> NDArray[np.int16]:
    n = round(math.sqrt(len(data) // 2))
    if n * n * 2 != len(data):
        raise ValueError("Not an .hgt tile")
    a = np.frombuffer(data, dtype=">i2").reshape(n, n).astype(np.int16)
    return np.where(a == VOID, 0, a).astype(np.int16)


class TileStore:
    """Local cache of .hgt tiles; downloads missing ones if a URL template is set."""

    def __init__(self, cache_dir: Path, url_template: str | None = DEFAULT_TILE_URL) -> None:
        self.dir = cache_dir
        self.url = url_template

    def path(self, lat: int, lon: int) -> Path:
        return self.dir / f"{tile_name(lat, lon)}.hgt"

    def get(self, lat: int, lon: int) -> NDArray[np.int16] | None:
        p = self.path(lat, lon)
        if not p.exists():
            if not self.url:
                return None
            name = tile_name(lat, lon)
            try:
                with urllib.request.urlopen(self.url.format(ns=name[:3], name=name), timeout=120) as r:
                    raw = gzip.decompress(r.read())
            except Exception as e:  # noqa: BLE001 - ocean tiles 404; treat as sea level
                log.info("No DEM tile %s (%s)", name, e)
                return None
            self.dir.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        return read_hgt(p.read_bytes())


@dataclass
class Raster:
    """Elevation grid (row 0 = north edge) with bilinear sampling."""

    data: NDArray[np.int16]
    north: float
    west: float
    res: float  # degrees per cell

    def sample(self, lat: NDArray[np.float64], lon: NDArray[np.float64]) -> NDArray[np.float32]:
        r = (self.north - lat) / self.res
        c = (lon - self.west) / self.res
        h, w = self.data.shape
        r = np.clip(r, 0, h - 1.001)
        c = np.clip(c, 0, w - 1.001)
        r0 = r.astype(np.int64)
        c0 = c.astype(np.int64)
        fr = (r - r0).astype(np.float32)
        fc = (c - c0).astype(np.float32)
        d = self.data
        top = d[r0, c0] * (1 - fc) + d[r0, c0 + 1] * fc
        bot = d[r0 + 1, c0] * (1 - fc) + d[r0 + 1, c0 + 1] * fc
        return (top * (1 - fr) + bot * fr).astype(np.float32)


def mosaic(store: TileStore, south: float, west: float, north: float, east: float, step: int = 1) -> Raster:
    """Mosaic tiles covering the box. ``step`` > 1 decimates (e.g. 3 → ~90 m) to save memory."""
    lat0, lat1 = math.floor(south), math.ceil(north)
    lon0, lon1 = math.floor(west), math.ceil(east)
    tiles: dict[tuple[int, int], NDArray[np.int16]] = {}
    n = 3601
    for la in range(lat0, lat1):
        for lo in range(lon0, lon1):
            t = store.get(la, lo)
            if t is not None:
                tiles[(la, lo)] = t
                n = t.shape[0]
    cells = (n - 1) // step
    out = np.zeros(((lat1 - lat0) * cells + 1, (lon1 - lon0) * cells + 1), dtype=np.int16)
    for (la, lo), t in tiles.items():
        r = (lat1 - 1 - la) * cells
        c = (lo - lon0) * cells
        out[r : r + cells + 1, c : c + cells + 1] = t[::step, ::step][: cells + 1, : cells + 1]
    return Raster(out, float(lat1), float(lon0), step / (n - 1))

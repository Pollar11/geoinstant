"""Build the retrieval index from geotagged images."""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .cells import CellTree
from .imageio import ImageError, decode, extract_gps
from .models.embedder import Embedder

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


@dataclass(frozen=True)
class GeoImage:
    path: Path
    lat: float
    lon: float


def from_folder(folder: Path) -> Iterator[GeoImage]:
    """Every image under ``folder`` that carries EXIF/XMP GPS (e.g. a phone photo library)."""
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        fix = extract_gps(p.read_bytes(), 200_000_000)
        if fix:
            yield GeoImage(p, fix.latitude, fix.longitude)


def from_manifest(manifest: Path) -> Iterator[GeoImage]:
    """CSV with columns path,lat,lon (paths relative to the manifest)."""
    with manifest.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            yield GeoImage(manifest.parent / row["path"], float(row["lat"]), float(row["lon"]))


def embed_images(
    items: Iterable[GeoImage], embedder: Embedder, work_size: int = 1024
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    embs, lats, lons = [], [], []
    for it in items:
        try:
            img = decode(it.path.read_bytes(), work_size, 200_000_000).image
        except (ImageError, OSError) as e:
            log.warning("skip %s: %s", it.path, e)
            continue
        embs.append(embedder.embed(img))
        lats.append(it.lat)
        lons.append(it.lon)
    if not embs:
        raise ValueError("No usable geo-tagged images")
    return np.stack(embs), np.asarray(lats), np.asarray(lons)


def write_index(out: Path, emb: np.ndarray, lat: np.ndarray, lon: np.ndarray, tree: CellTree) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        emb=emb.astype(np.float16),
        lat=lat.astype(np.float64),
        lon=lon.astype(np.float64),
        leaf=tree.nearest_leaf(lat, lon),
    )


def embed_pil(
    images: Iterable[tuple[Image.Image, float, float]], embedder: Embedder
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """In-memory variant (tests, notebooks)."""
    rows = [(embedder.embed(img.convert("RGB")), lat, lon) for img, lat, lon in images]
    return np.stack([r[0] for r in rows]), np.array([r[1] for r in rows]), np.array([r[2] for r in rows])

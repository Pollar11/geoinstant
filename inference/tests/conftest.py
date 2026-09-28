from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from geoinstant.cells import CellTree
from geoinstant.config import Settings
from geoinstant.gazetteer import Gazetteer
from geoinstant.indexing import embed_pil, write_index
from geoinstant.models.embedder import HashEmbedder

# Photos "taken" in these seed cities make up the synthetic retrieval index.
INDEXED = [
    ("Oslo", 59.9139, 10.7522),
    ("Lisbon", 38.7223, -9.1393),
    ("Kyoto-ish", 35.0116, 135.7681),
    ("Cape Town", -33.9249, 18.4241),
]


def synthetic_photo(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A textured image unique to ``seed`` (stands in for a real photo)."""
    rng = np.random.default_rng(seed)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3))
    for c in range(3):
        fx, fy, ph = *rng.uniform(0.005, 0.05, 2).tolist(), rng.uniform(0, 6.28)
        img[..., c] = 0.5 + 0.5 * np.sin(xx * fx + yy * fy + ph)
    img += rng.normal(0, 0.03, img.shape)
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def jpeg_bytes(img: Image.Image, exif: Image.Exif | None = None, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    if exif is not None:
        img.save(buf, "JPEG", quality=quality, exif=exif)
    else:
        img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _dms(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    return IFDRational(d, 1), IFDRational(m, 1), IFDRational(round(s * 1000), 1000)


def gps_exif(lat: float, lon: float) -> Image.Exif:
    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    gps[1] = "N" if lat >= 0 else "S"
    gps[2] = _dms(lat)
    gps[3] = "E" if lon >= 0 else "W"
    gps[4] = _dms(lon)
    return exif


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("artifacts")
    tree = CellTree.from_gazetteer(Gazetteer())
    photos = [(synthetic_photo(i), lat, lon) for i, (_, lat, lon) in enumerate(INDEXED)]
    # Plus unrelated distractors spread around the world.
    rng = np.random.default_rng(0)
    for j in range(40):
        photos.append((synthetic_photo(1000 + j), float(rng.uniform(-50, 60)), float(rng.uniform(-170, 170))))
    emb, lat, lon = embed_pil(photos, HashEmbedder())
    write_index(root / "index.npz", emb, lat, lon, tree)
    return root


@pytest.fixture()
def settings(artifacts: Path, tmp_path: Path) -> Settings:
    return Settings(
        artifacts_dir=artifacts,
        vlm_mode="off",
        feedback_dir=tmp_path / "feedback",
        rate_limit_per_minute=600,
        rate_limit_burst=100,
        fast_deadline_ms=5000,  # CI machines are slow; the latency budget is tested separately
    )

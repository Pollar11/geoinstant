"""Skyline matching on synthetic terrain with a known camera position."""

import numpy as np
from PIL import Image

from geoinstant.geo import haversine_km
from geoinstant.skyline.dem import Raster, read_hgt
from geoinstant.skyline.extract import Profile, detect, from_trace
from geoinstant.skyline.horizon import grid, panorama
from geoinstant.skyline.match import match, relief

STEP = 0.5


def terrain(seed: int = 3) -> Raster:
    """1.5° × 1.5° box of random Gaussian peaks at ~90 m resolution."""
    rng = np.random.default_rng(seed)
    n = 1801
    lat = np.linspace(47.5, 46.0, n)[:, None]
    lon = np.linspace(7.0, 8.5, n)[None, :]
    h = np.full((n, n), 500.0)
    for _ in range(90):
        c_lat, c_lon = rng.uniform(46.0, 47.5), rng.uniform(7.0, 8.5)
        s = rng.uniform(0.01, 0.06)
        h += rng.uniform(500, 3000) * np.exp(-((lat - c_lat) ** 2 + ((lon - c_lon) * 0.68) ** 2) / (2 * s * s))
    return Raster(h.astype(np.int16), 47.5, 7.0, 1.5 / (n - 1))


def photo_profile(pano: np.ndarray, az: float, fov: float, aspect: float = 1.5, pitch: float = 2.0) -> Profile:
    """What a camera at that viewpoint would see: skyline in image coordinates."""
    f = 0.5 / np.tan(np.radians(fov) / 2)
    x = (np.arange(320) + 0.5) / 320
    theta = np.degrees(np.arctan((x - 0.5) / f))
    angles = np.interp((az + theta) % 360, np.arange(0, 360, STEP), pano, period=360)
    y = 0.5 - np.tan(np.radians(angles - pitch)) * f * aspect
    return Profile(x, y, np.ones_like(x), aspect)


DEM = terrain()
TRUE = (46.78, 7.71)


def test_panorama_sees_peaks() -> None:
    p = panorama(DEM, *TRUE)
    assert p.shape == (720,)
    assert p.max() > 1.0  # mountains above the horizon
    assert np.ptp(p) > 2.0


def test_match_recovers_position_and_direction() -> None:
    lat, lon = grid(46.6, 7.5, 46.95, 7.95, 2.0)
    lat = np.append(lat, TRUE[0])
    lon = np.append(lon, TRUE[1])
    panos = np.stack([panorama(DEM, a, b) for a, b in zip(lat, lon, strict=True)])
    prof = photo_profile(panos[-1], az=130.0, fov=46.0)
    assert relief(prof) > 0.3
    best = match(prof, panos, STEP)[0]
    assert haversine_km(lat[best.index], lon[best.index], *TRUE) < 0.5
    assert abs((best.azimuth - 130.0 + 180) % 360 - 180) < 2.0
    assert best.fov == 46.0
    assert best.rmse < 0.05


def test_partial_trace_still_matches() -> None:
    lat, lon = grid(46.6, 7.5, 46.95, 7.95, 2.0)
    lat = np.append(lat, TRUE[0])
    lon = np.append(lon, TRUE[1])
    panos = np.stack([panorama(DEM, a, b) for a, b in zip(lat, lon, strict=True)])
    full = photo_profile(panos[-1], az=250.0, fov=54.0)
    pts = [(float(x), float(y)) for x, y in zip(full.x[40:260:10], full.y[40:260:10], strict=True)]
    best = match(from_trace(pts, full.aspect), panos, STEP)[0]
    assert haversine_km(lat[best.index], lon[best.index], *TRUE) < 0.5


def test_detect_finds_rendered_ridge() -> None:
    pano = panorama(DEM, *TRUE)
    truth = photo_profile(pano, az=130.0, fov=46.0, aspect=4 / 3)
    w, h = 640, 480
    rows = np.arange(h)[:, None]
    ridge = np.interp((np.arange(w) + 0.5) / w, truth.x, truth.y) * h
    sky = 0.75 + 0.2 * (1 - rows / h)
    rng = np.random.default_rng(0)
    ground = 0.35 + 0.08 * rng.standard_normal((h, w))
    img = np.where(rows < ridge[None, :], sky, ground)
    prof = detect(Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).convert("RGB"))
    err = np.abs(np.interp(truth.x, prof.x, prof.y) - truth.y)
    assert np.median(err) < 0.01


def test_read_hgt_voids() -> None:
    raw = np.array([[100, -32768], [5, 7]], dtype=">i2").tobytes()
    assert read_hgt(raw).tolist() == [[100, 0], [5, 7]]


def write_tile(folder, dem: Raster = DEM) -> None:
    """Cut the synthetic terrain into an SRTM3-style tile N46E007 (1201 × 1201)."""
    folder.mkdir(parents=True, exist_ok=True)
    tile = dem.data[600:1801, 0:1201]
    (folder / "N46E007.hgt").write_bytes(tile.astype(">i2").tobytes())


def test_service_on_demand_search(tmp_path) -> None:
    from geoinstant.skyline.service import SkylineService

    write_tile(tmp_path / "dem")
    svc = SkylineService(tmp_path / "sky", tmp_path / "dem", None, max_area_km2=2000, max_km=25)
    prof = photo_profile(panorama(DEM, *TRUE, max_km=25), az=130.0, fov=46.0)
    r = svc.search(prof, (46.70, 7.60, 46.86, 7.82))
    assert r.status == "ok"
    best = r.candidates[0]
    assert haversine_km(best.lat, best.lon, *TRUE) < 1.0
    assert abs((best.m.azimuth - 130.0 + 180) % 360 - 180) < 5
    assert list((tmp_path / "sky").glob("ondemand_*.npz"))  # cached for next time

    assert svc.search(prof, (40.0, 0.0, 50.0, 20.0)).status == "area_too_large"
    flat = Profile(prof.x, np.full_like(prof.y, 0.5), prof.w, prof.aspect)
    assert svc.search(flat, (46.70, 7.60, 46.86, 7.82)).status == "too_flat"

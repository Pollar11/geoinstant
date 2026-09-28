"""Render the 360° skyline (horizon elevation angle per azimuth) seen from a point."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from .dem import Raster

EARTH_R_M = 6_371_000.0
REFRACTION_K = 0.13  # standard atmospheric refraction coefficient
KM_PER_DEG = 111.32


def azimuths(step_deg: float) -> NDArray[np.float64]:
    return np.arange(0.0, 360.0, step_deg)


def panorama(
    dem: Raster,
    lat: float,
    lon: float,
    step_deg: float = 0.5,
    max_km: float = 40.0,
    n_dist: int = 320,
    eye_m: float = 1.7,
    min_km: float = 0.5,
) -> NDArray[np.float32]:
    """Horizon elevation angle (deg) per azimuth (0 = north, clockwise).

    Terrain closer than ``min_km`` is ignored: photo skylines are distant ridges, and the DEM
    is too coarse to render the ground at the photographer's feet reliably.
    """
    az = np.radians(azimuths(step_deg))
    d_km = np.geomspace(min_km, max_km, n_dist)
    dlat = np.outer(np.cos(az), d_km) / KM_PER_DEG
    dlon = np.outer(np.sin(az), d_km) / (KM_PER_DEG * np.cos(np.radians(lat)))
    h = dem.sample(lat + dlat, lon + dlon)
    h0 = float(dem.sample(np.array([lat]), np.array([lon]))[0]) + eye_m
    d_m = d_km * 1000.0
    drop = d_m**2 * (1 - REFRACTION_K) / (2 * EARTH_R_M)  # curvature minus refraction
    ang = np.degrees(np.arctan2(h - h0 - drop, d_m))
    out: NDArray[np.float32] = ang.max(axis=1).astype(np.float32)
    return out


def grid(
    south: float, west: float, north: float, east: float, spacing_km: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Viewpoints on a roughly uniform km grid inside the box."""
    lats = np.arange(south, north + 1e-9, spacing_km / KM_PER_DEG)
    mid = np.radians((south + north) / 2)
    lons = np.arange(west, east + 1e-9, spacing_km / (KM_PER_DEG * np.cos(mid)))
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    return la.ravel(), lo.ravel()

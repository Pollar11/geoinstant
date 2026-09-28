"""Vectorised spherical geometry."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

EARTH_RADIUS_KM = 6371.0088

FloatArray = NDArray[np.float64]


def haversine_km(lat1: ArrayLike, lon1: ArrayLike, lat2: ArrayLike, lon2: ArrayLike) -> FloatArray:
    """Great-circle distance in km. Broadcasts like numpy."""
    p1, l1, p2, l2 = (np.radians(np.asarray(x, dtype=np.float64)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin((l2 - l1) / 2) ** 2
    d: FloatArray = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return d


def to_unit_vectors(lat: ArrayLike, lon: ArrayLike) -> FloatArray:
    """(lat, lon) in degrees → (N, 3) unit vectors on the sphere."""
    p = np.radians(np.asarray(lat, dtype=np.float64))
    lm = np.radians(np.asarray(lon, dtype=np.float64))
    return np.stack([np.cos(p) * np.cos(lm), np.cos(p) * np.sin(lm), np.sin(p)], axis=-1)


def from_unit_vector(v: FloatArray) -> tuple[float, float]:
    x, y, z = (float(c) for c in v / (np.linalg.norm(v) + 1e-12))
    return float(np.degrees(np.arcsin(np.clip(z, -1, 1)))), float(np.degrees(np.arctan2(y, x)))


def spherical_mean(lat: ArrayLike, lon: ArrayLike, weights: ArrayLike) -> tuple[float, float]:
    """Weighted mean on the sphere (handles the antimeridian, unlike averaging degrees)."""
    w = np.asarray(weights, dtype=np.float64)
    v = (to_unit_vectors(lat, lon) * w[:, None]).sum(axis=0)
    return from_unit_vector(v)


def mean_shift_mode(
    lat: ArrayLike,
    lon: ArrayLike,
    weights: ArrayLike,
    bandwidth_km: float,
    iters: int = 12,
) -> tuple[float, float]:
    """Densest point of a weighted point cloud (Gaussian kernel mean-shift)."""
    lat_a = np.asarray(lat, dtype=np.float64)
    lon_a = np.asarray(lon, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if lat_a.size == 0:
        raise ValueError("mean_shift_mode needs at least one point")
    # Seed = the point with the most kernel-weighted mass around it.
    d_all = haversine_km(lat_a[:, None], lon_a[:, None], lat_a[None, :], lon_a[None, :])
    density = (np.exp(-0.5 * (d_all / bandwidth_km) ** 2) * w[None, :]).sum(axis=1)
    c_lat, c_lon = float(lat_a[np.argmax(density)]), float(lon_a[np.argmax(density)])
    for _ in range(iters):
        d = haversine_km(c_lat, c_lon, lat_a, lon_a)
        k = w * np.exp(-0.5 * (d / bandwidth_km) ** 2)
        if k.sum() <= 1e-12:
            break
        n_lat, n_lon = spherical_mean(lat_a, lon_a, k)
        moved = float(haversine_km(c_lat, c_lon, n_lat, n_lon))
        c_lat, c_lon = n_lat, n_lon
        if moved < 0.001:
            break
    return c_lat, c_lon


def weighted_quantile(values: ArrayLike, weights: ArrayLike, q: float) -> float:
    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if v.size == 0 or w.sum() <= 0:
        return 0.0
    order = np.argsort(v)
    cw = np.cumsum(w[order]) / w.sum()
    return float(v[order][min(int(np.searchsorted(cw, q)), v.size - 1)])


def logsumexp(x: FloatArray, axis: int | None = None) -> FloatArray:
    m = np.max(x, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    out = m + np.log(np.sum(np.exp(x - m), axis=axis, keepdims=True))
    result: FloatArray = np.squeeze(out, axis=axis) if axis is not None else out.reshape(())
    return result


def log_softmax(x: FloatArray) -> FloatArray:
    return x - logsumexp(x)

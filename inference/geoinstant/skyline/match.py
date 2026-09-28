"""Match a photo skyline against rendered panoramas over azimuth and field of view.

For each viewpoint and FOV: weighted, mean-removed RMSE (relative to the skyline's spread) at
every azimuth shift, via FFT cross-correlation. Mean removal absorbs camera pitch.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .extract import Profile

DEFAULT_FOVS = (30.0, 38.0, 46.0, 54.0, 63.0, 75.0)  # common lenses, horizontal degrees


@dataclass(frozen=True)
class Match:
    index: int  # viewpoint row
    azimuth: float  # direction of the photo centre, degrees from north
    fov: float
    rmse: float  # error relative to the skyline's own spread (0 = perfect)
    score: float  # 0..1 relative likelihood


def photo_angles(p: Profile, fov: float, step: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Resample the skyline to elevation angles on the panorama's azimuth grid (left edge = 0)."""
    f = 0.5 / np.tan(np.radians(fov) / 2)  # focal length in units of image width
    theta = np.degrees(np.arctan((p.x - 0.5) / f))
    phi = np.degrees(np.arctan((0.5 - p.y) / (f * p.aspect)))
    grid = np.arange(-fov / 2, fov / 2, step)
    q = np.interp(grid, theta, phi)
    w = np.interp(grid, theta, p.w)
    return q, w


def relief(p: Profile) -> float:
    """Weighted angular spread of the skyline at a mid FOV (too flat = unmatchable)."""
    q, w = photo_angles(p, 50.0, 0.5)
    if w.sum() < 1:
        return 0.0
    m = (w * q).sum() / w.sum()
    return float(np.sqrt((w * (q - m) ** 2).sum() / w.sum()))


def _corr(a: NDArray[np.float64], fb: NDArray[np.complex128]) -> NDArray[np.float64]:
    """Circular cross-correlation of a (len n) with each row of B (given as FFT) → (N, n)."""
    return np.fft.irfft(np.conj(np.fft.rfft(a))[None, :] * fb, n=fb.shape[1] * 2 - 2)


def match(
    p: Profile,
    panos: NDArray[np.floating],
    step: float,
    fovs: tuple[float, ...] = DEFAULT_FOVS,
    sigma: float = 0.15,
) -> list[Match]:
    """Best (azimuth, FOV) per viewpoint, sorted by RMSE."""
    n_vp, n_az = panos.shape
    P = panos.astype(np.float64)
    fP = np.fft.rfft(P, axis=1)
    fP2 = np.fft.rfft(P * P, axis=1)
    best_rmse = np.full(n_vp, np.inf)
    best_shift = np.zeros(n_vp, dtype=np.int64)
    best_fov = np.zeros(n_vp)
    for fov in fovs:
        q, w = photo_angles(p, fov, step)
        m = len(q)
        if m >= n_az:
            continue
        qa = np.zeros(n_az)
        wa = np.zeros(n_az)
        qa[:m], wa[:m] = q, w
        W = wa.sum()
        if W < 3:
            continue
        s_wq2 = (wa * qa * qa).sum()
        s_wq = (wa * qa).sum()
        c1 = _corr(wa * qa, fP)
        c2 = _corr(wa, fP)
        c3 = _corr(wa, fP2)
        ssd = s_wq2 - 2 * c1 + c3 - (s_wq - c2) ** 2 / W
        # Normalise by the photo's own spread so narrow and wide lens guesses compete fairly.
        spread = np.sqrt(max(s_wq2 / W - (s_wq / W) ** 2, 1e-6))
        rmse = np.sqrt(np.maximum(ssd, 0) / W) / spread
        shift = rmse.argmin(axis=1)
        r = rmse[np.arange(n_vp), shift]
        better = r < best_rmse
        best_rmse[better], best_shift[better], best_fov[better] = r[better], shift[better], fov
    lik = np.exp(-0.5 * (best_rmse / sigma) ** 2)
    lik /= lik.max() if lik.max() > 0 else 1.0
    order = np.argsort(best_rmse)
    return [
        Match(
            index=int(i),
            azimuth=float((best_shift[i] * step + best_fov[i] / 2) % 360),
            fov=float(best_fov[i]),
            rmse=float(best_rmse[i]),
            score=float(lik[i]),
        )
        for i in order
        if np.isfinite(best_rmse[i])
    ]

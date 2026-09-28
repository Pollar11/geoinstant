"""Find the sky/terrain boundary in a photo (dynamic programming), or use a user-traced line."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter

WIDTH = 320


@dataclass
class Profile:
    """Skyline in normalised image coords: y[i] at x[i] (both 0..1, y down), weight 0..1."""

    x: NDArray[np.float64]
    y: NDArray[np.float64]
    w: NDArray[np.float64]
    aspect: float  # width / height of the photo
    traced: bool = False


def detect(image: Image.Image, jump_px: int = 8) -> Profile:
    w0, h0 = image.size
    scale = WIDTH / w0
    h = max(16, round(h0 * scale))
    g = (
        np.asarray(
            image.convert("L").resize((WIDTH, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(1.2)),
            dtype=np.float64,
        )
        / 255.0
    )

    # Edge strength across rows, rewarded when the region above looks like sky (smooth).
    grad = np.zeros_like(g)
    grad[1:-1] = np.abs(g[2:] - g[:-2])
    cs = np.cumsum(g, axis=0)
    cs2 = np.cumsum(g * g, axis=0)
    n = np.arange(1, h + 1)[:, None]
    std_above = np.sqrt(np.maximum(cs2 / n - (cs / n) ** 2, 0))
    smooth = np.clip(1 - std_above / 0.12, 0, 1)
    score = grad / (grad.max() + 1e-9) * (0.3 + 0.7 * smooth)
    score[: max(2, h // 50)] = 0  # ignore the frame edge
    score[int(h * 0.9) :] = 0

    # DP left→right: maximise edge score minus a penalty on vertical jumps.
    offs = np.arange(-jump_px, jump_px + 1)
    pen = 0.02 * np.abs(offs)
    acc = score[:, 0].copy()
    back = np.zeros((h, WIDTH), dtype=np.int64)
    rows = np.arange(h)
    for x in range(1, WIDTH):
        prev = np.full((len(offs), h), -np.inf)
        for k, o in enumerate(offs):
            src = rows + o
            ok = (src >= 0) & (src < h)
            prev[k, ok] = acc[src[ok]] - pen[k]
        best = prev.argmax(axis=0)
        back[:, x] = rows + offs[best]
        acc = prev[best, rows] + score[:, x]
    y = np.empty(WIDTH, dtype=np.int64)
    y[-1] = int(acc.argmax())
    for x in range(WIDTH - 1, 0, -1):
        y[x - 1] = back[y[x], x]
    conf = score[y, np.arange(WIDTH)]
    wts = np.clip(conf / (np.median(conf) + 1e-9), 0, 1)
    xs = (np.arange(WIDTH) + 0.5) / WIDTH
    return Profile(xs, (y + 0.5) / h, wts, w0 / h0)


def from_trace(points: list[tuple[float, float]], aspect: float, n: int = WIDTH) -> Profile:
    """User clicked points along the ridge (normalised x, y). Columns outside the trace get weight 0."""
    pts = sorted(points)
    px = np.array([p[0] for p in pts])
    py = np.array([p[1] for p in pts])
    xs = (np.arange(n) + 0.5) / n
    ys = np.interp(xs, px, py)
    ws = ((xs >= px[0]) & (xs <= px[-1])).astype(np.float64)
    return Profile(xs, ys, ws, aspect, traced=True)

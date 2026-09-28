"""Point-by-point check: do the same windows, corners and rooflines appear in both photos?

Keypoint matches → RANSAC fundamental matrix → inlier count. The inlier count is what separates
"looks similar" from "is the same place": unrelated streets score under ~10.

- LightGlue (transformer matcher, ONNX) when `matcher.onnx` exists: more robust to decades of
  change, lighting and old-print quality.
- SIFT + ratio test + mutual best match otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

MAX_SIDE = 1024


def _gray(img: Image.Image) -> np.ndarray:
    g = img.convert("L")
    g.thumbnail((MAX_SIDE, MAX_SIDE))
    # Equalise contrast so faded old prints and modern photos are comparable.
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(np.asarray(g))


def geometric_inliers(src: np.ndarray, dst: np.ndarray) -> int:
    """Matches that agree on one camera geometry."""
    if len(src) == 0:
        return 0
    # Count each location once (SIFT repeats a point per orientation).
    _, keep = np.unique(np.round(np.hstack([src, dst])).astype(int), axis=0, return_index=True)
    src, dst = src[keep].astype(np.float32), dst[keep].astype(np.float32)
    if len(src) < 8:
        return 0
    f, mask = cv2.findFundamentalMat(src, dst, cv2.FM_RANSAC, 3.0, 0.999)
    if f is None or mask is None:
        return 0
    return int(mask.ravel()[: len(src)].astype(bool).sum())


class Matcher(Protocol):
    name: str

    def prepare(self, img: Image.Image) -> Any: ...
    def inliers(self, a: Any, b: Any) -> int: ...


@dataclass
class Features:
    kps: np.ndarray  # (N, 2) point coordinates
    desc: np.ndarray | None  # (N, 128) float32


class SiftMatcher:
    name = "sift"

    def __init__(self) -> None:
        self._sift = cv2.SIFT_create(nfeatures=4000)  # type: ignore[attr-defined]  # missing from the stubs

    def prepare(self, img: Image.Image) -> Features:
        kps, desc = self._sift.detectAndCompute(_gray(img), None)
        return Features(np.array([k.pt for k in kps], dtype=np.float32).reshape(-1, 2), desc)

    def inliers(self, a: Features, b: Features, ratio: float = 0.8) -> int:
        if a.desc is None or b.desc is None or len(a.kps) < 8 or len(b.kps) < 8:
            return 0
        m = cv2.BFMatcher(cv2.NORM_L2)
        back = {x.queryIdx: x.trainIdx for x in m.match(b.desc, a.desc)}
        # Ratio test + mutual best match: stops many points piling onto one (a degenerate "fit").
        good = [
            p[0]
            for p in m.knnMatch(a.desc, b.desc, k=2)
            if len(p) == 2 and p[0].distance < ratio * p[1].distance and back.get(p[0].trainIdx) == p[0].queryIdx
        ]
        return geometric_inliers(a.kps[[g.queryIdx for g in good]], b.kps[[g.trainIdx for g in good]])


class LightGlueMatcher:
    """Extractor + LightGlue pipeline exported by LightGlue-ONNX (docs/DESIGN.md).

    Input `images` (2, C, H, W) in [0, 1]. Outputs keypoints (2, N, 2), matches (M, 3) = [batch, i0, i1], scores (M,).
    """

    name = "lightglue"

    def __init__(self, path: Path, providers: list[str], session: Any = None) -> None:
        if session is None:
            import onnxruntime as ort

            available = set(ort.get_available_providers())
            session = ort.InferenceSession(str(path), providers=[p for p in providers if p in available])
        self.session = session
        inp = session.get_inputs()[0]
        self.input = inp.name
        _, c, h, w = inp.shape
        self.channels = c if isinstance(c, int) else 1
        self.size = (w if isinstance(w, int) else 1024, h if isinstance(h, int) else 768)

    def prepare(self, img: Image.Image) -> np.ndarray:
        # Fixed size for the exported graph; resizing each photo doesn't change which points agree geometrically.
        g = Image.fromarray(_gray(img)).resize(self.size, Image.Resampling.BILINEAR)
        x = np.asarray(g, dtype=np.float32)[None] / 255.0
        return np.repeat(x, self.channels, axis=0)

    def inliers(self, a: np.ndarray, b: np.ndarray, min_score: float = 0.2) -> int:
        kps, matches, scores = self.session.run(None, {self.input: np.stack([a, b])})[:3]
        m = np.asarray(matches)[np.asarray(scores) >= min_score]
        if len(m) < 8:
            return 0
        return geometric_inliers(np.asarray(kps[0])[m[:, 1]], np.asarray(kps[1])[m[:, 2]])


def load_matcher(path: Path, providers: list[str]) -> Matcher:
    if path.exists():
        try:
            return LightGlueMatcher(path, providers)
        except Exception:
            log.exception("Failed to load %s; using SIFT", path)
    return SiftMatcher()

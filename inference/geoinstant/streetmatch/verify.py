"""Point-by-point check: do the same windows, corners and rooflines appear in both photos?

SIFT keypoints + ratio test + RANSAC. The inlier count is what separates "looks similar"
from "is the same place": unrelated streets rarely give more than ~10 consistent matches.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

MAX_SIDE = 1024


@dataclass
class Features:
    kps: np.ndarray  # (N, 2) point coordinates
    desc: np.ndarray | None  # (N, 128) float32


_sift = cv2.SIFT_create(nfeatures=4000)  # type: ignore[attr-defined]  # missing from the stubs


def features(img: Image.Image) -> Features:
    g = img.convert("L")
    g.thumbnail((MAX_SIDE, MAX_SIDE))
    arr = np.asarray(g)
    # Equalise contrast so faded old prints and modern photos are comparable.
    arr = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(arr)
    kps, desc = _sift.detectAndCompute(arr, None)
    pts = np.array([k.pt for k in kps], dtype=np.float32).reshape(-1, 2)
    return Features(pts, desc)


def inliers(a: Features, b: Features, ratio: float = 0.8) -> int:
    """Matches that agree on one camera geometry (unrelated streets score under ~10)."""
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
    src = a.kps[[g.queryIdx for g in good]]
    dst = b.kps[[g.trainIdx for g in good]]
    # SIFT repeats a point once per orientation; count each location once.
    _, keep = np.unique(np.round(np.hstack([src, dst])).astype(int), axis=0, return_index=True)
    src, dst = src[keep], dst[keep]
    if len(src) < 8:
        return 0
    f, mask = cv2.findFundamentalMat(src, dst, cv2.FM_RANSAC, 3.0, 0.999)
    if f is None or mask is None:
        return 0
    return int(mask.ravel()[: len(src)].astype(bool).sum())

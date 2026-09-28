"""kNN over geotagged photo embeddings (numpy or FAISS). Index: index.npz {emb, lat, lon, leaf}."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..cells import CellTree
from .base import Evidence, WeightedPoint

log = logging.getLogger(__name__)


class VectorIndex:
    def __init__(
        self,
        lat: NDArray[np.float64],
        lon: NDArray[np.float64],
        leaf: NDArray[np.int64],
        emb: NDArray[np.float32] | None = None,
        faiss_index: object | None = None,
    ) -> None:
        if emb is None and faiss_index is None:
            raise ValueError("VectorIndex needs embeddings or a FAISS index")
        self.lat, self.lon, self.leaf = lat, lon, leaf
        self.emb = emb
        self.faiss = faiss_index
        self.dim = int(emb.shape[1]) if emb is not None else int(faiss_index.d)  # type: ignore[union-attr]

    def __len__(self) -> int:
        return int(self.lat.shape[0])

    def search(self, q: NDArray[np.float32], k: int) -> tuple[NDArray[np.float32], NDArray[np.int64]]:
        k = min(k, len(self))
        if self.faiss is not None:
            sims, idx = self.faiss.search(q[None].astype(np.float32), k)  # type: ignore[attr-defined]
            keep = idx[0] >= 0
            return sims[0][keep], idx[0][keep].astype(np.int64)
        assert self.emb is not None
        sims = self.emb @ q.astype(self.emb.dtype)
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return sims[idx].astype(np.float32), idx.astype(np.int64)

    @classmethod
    def load(cls, npz_path: Path, faiss_path: Path, tree: CellTree) -> VectorIndex | None:
        if not npz_path.exists():
            log.warning("No retrieval index at %s; retrieval disabled", npz_path)
            return None
        z = np.load(npz_path, allow_pickle=False)
        lat, lon = z["lat"].astype(np.float64), z["lon"].astype(np.float64)
        leaf = z["leaf"].astype(np.int64) if "leaf" in z and int(z["leaf"].max(initial=-1)) < len(tree) else None
        if leaf is None:
            leaf = tree.nearest_leaf(lat, lon)
        faiss_index = None
        if faiss_path.exists():
            try:
                import faiss

                # mmap the inverted lists: boot in seconds and share pages between replicas on one host.
                faiss_index = faiss.read_index(str(faiss_path), faiss.IO_FLAG_MMAP)
            except ImportError:
                log.warning("faiss not installed; falling back to exact numpy search")
        emb = None
        if faiss_index is None:
            e = z["emb"].astype(np.float32)
            emb = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-12)
        log.info("Loaded retrieval index: %d rows", lat.shape[0])
        return cls(lat, lon, leaf, emb, faiss_index)


def retrieval_evidence(
    index: VectorIndex,
    tree: CellTree,
    q: NDArray[np.float32],
    k: int,
    temperature: float,
    weight: float,
) -> Evidence | None:
    if q.shape[0] != index.dim:
        return None
    sims, idx = index.search(q, k)
    if sims.size == 0:
        return None
    w = np.exp((sims - sims.max()) / temperature).astype(np.float64)
    leaves = index.leaf[idx]
    n = len(tree)

    # Neighbour histogram smoothed over leaf, country and a uniform floor.
    h_leaf = np.bincount(leaves, weights=w, minlength=n) / w.sum()
    country = tree.levels["country"].parent
    h_country = np.bincount(country[leaves], weights=w, minlength=int(country.max()) + 1) / w.sum()
    leaves_per_country = np.bincount(country, minlength=int(country.max()) + 1)
    lik = 0.6 * h_leaf + 0.3 * h_country[country] / leaves_per_country[country] + 0.1 / n

    # Trust weak matches less (thresholds tuned for CLIP-style cosine similarity).
    best = float(sims.max())
    trust = float(np.clip((best - 0.35) / (0.85 - 0.35), 0.25, 1.0))

    top = int(np.argmax(h_country))
    top_country = tree.levels["country"].labels[top]
    n_top = int((country[leaves] == top).sum())
    share = float(h_country.max())
    return Evidence(
        source="retrieval",
        key="retrieval",
        label="Visually similar geo-tagged photos",
        loglik=np.log(lik * n),
        weight=weight * trust,
        points=[
            WeightedPoint(float(index.lat[i]), float(index.lon[i]), float(wi), int(li))
            for i, wi, li in zip(idx, w, leaves, strict=True)
        ],
        detail=(
            f"{n_top} of the {len(idx)} most similar photos are in {top_country}; "
            f"{share:.0%} of the similarity-weighted vote (best match {best:.2f})"
        ),
    )

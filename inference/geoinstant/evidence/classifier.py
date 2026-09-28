"""Geocell classifier: prototypes (L×D) · embedding → log-probs over leaves."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..cells import CellTree
from ..geo import log_softmax
from .base import Evidence

log = logging.getLogger(__name__)


class CellClassifier:
    def __init__(self, prototypes: NDArray[np.float32], logit_scale: float) -> None:
        self.prototypes = prototypes / (np.linalg.norm(prototypes, axis=1, keepdims=True) + 1e-12)
        self.logit_scale = logit_scale
        self.dim = int(prototypes.shape[1])

    @classmethod
    def load(cls, path: Path, tree: CellTree) -> CellClassifier | None:
        if not path.exists():
            log.warning("No cell prototypes at %s; geocell classifier disabled", path)
            return None
        z = np.load(path, allow_pickle=False)
        protos = z["prototypes"].astype(np.float32)
        if protos.shape[0] != len(tree):
            log.error("cell_prototypes has %d rows but the cell tree has %d leaves; ignoring", protos.shape[0], len(tree))
            return None
        return cls(protos, float(z["logit_scale"]) if "logit_scale" in z else 100.0)

    def evidence(self, q: NDArray[np.float32], tree: CellTree, weight: float) -> Evidence | None:
        if q.shape[0] != self.dim:
            return None
        logp = log_softmax((self.prototypes @ q).astype(np.float64) * self.logit_scale)
        country = tree.levels["country"]
        p_country = np.bincount(country.parent, weights=np.exp(logp), minlength=len(country.keys))
        top = int(np.argmax(p_country))
        # Subtract the prior; fusion adds it back once.
        return Evidence(
            source="classifier",
            key="classifier",
            label="Overall scene appearance (geocell classifier)",
            loglik=logp - tree.log_prior,
            weight=weight,
            detail=f"Most likely country from appearance: {country.labels[top]} ({p_country[top]:.0%})",
        )

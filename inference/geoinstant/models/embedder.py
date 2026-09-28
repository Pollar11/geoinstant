"""Image encoders: ONNX (CLIP/SigLIP) or a dev hash embedder."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image

log = logging.getLogger(__name__)

# CLIP / SigLIP normalisation constants. SigLIP uses 0.5/0.5; override per model if needed.
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, image: Image.Image) -> NDArray[np.float32]:
        """Return an L2-normalised (dim,) float32 vector."""


def _l2(v: NDArray[np.float32]) -> NDArray[np.float32]:
    return (v / (np.linalg.norm(v) + 1e-12)).astype(np.float32)


class OnnxImageEncoder:
    def __init__(
        self,
        path: Path,
        size: int,
        providers: list[str],
        threads: int = 0,
        mean: NDArray[np.float32] = CLIP_MEAN,
        std: NDArray[np.float32] = CLIP_STD,
    ) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            so.intra_op_num_threads = threads
        available = set(ort.get_available_providers())
        self.session = ort.InferenceSession(str(path), so, providers=[p for p in providers if p in available])
        self.input_name = self.session.get_inputs()[0].name
        self.size = size
        self.mean, self.std = mean, std
        self.name = f"onnx:{path.name}"
        self.dim = int(self.session.get_outputs()[0].shape[-1])
        self.embed(Image.new("RGB", (size, size)))  # warm-up: builds TensorRT engines / CUDA graphs

    def preprocess(self, image: Image.Image) -> NDArray[np.float32]:
        # Resize shortest side then centre-crop: what CLIP-family encoders were trained on.
        w, h = image.size
        s = self.size / min(w, h)
        img = image.resize((max(self.size, round(w * s)), max(self.size, round(h * s))), Image.Resampling.BICUBIC)
        w, h = img.size
        left, top = (w - self.size) // 2, (h - self.size) // 2
        img = img.crop((left, top, left + self.size, top + self.size))
        x = (np.asarray(img, dtype=np.float32) / 255.0 - self.mean) / self.std
        return x.transpose(2, 0, 1)[None]

    def embed(self, image: Image.Image) -> NDArray[np.float32]:
        out = self.session.run(None, {self.input_name: self.preprocess(image)})[0]
        return _l2(np.asarray(out, dtype=np.float32).reshape(-1))


class HashEmbedder:
    """Deterministic, model-free descriptor for dev/tests (see module docstring)."""

    def __init__(self, dim: int = 256, seed: int = 7) -> None:
        self.name = "dev:hash"
        self.dim = dim
        rng = np.random.default_rng(seed)
        self._proj = rng.standard_normal((16 * 16 * 3 + 64, dim)).astype(np.float32)

    def embed(self, image: Image.Image) -> NDArray[np.float32]:
        small = np.asarray(image.convert("RGB").resize((16, 16), Image.Resampling.BOX), dtype=np.float32) / 255.0
        g = np.asarray(image.convert("L").resize((64, 64), Image.Resampling.BOX), dtype=np.float32) / 255.0
        gx, gy = np.diff(g, axis=1)[:-1], np.diff(g, axis=0)[:, :-1]
        ang = np.arctan2(gy, gx)
        mag = np.hypot(gx, gy)
        hog, _ = np.histogram(ang, bins=64, range=(-np.pi, np.pi), weights=mag)
        feat = np.concatenate([small.reshape(-1) - small.mean(), hog / (hog.sum() + 1e-6)]).astype(np.float32)
        return _l2(feat @ self._proj)


def load_embedder(path: Path, size: int, providers: list[str], threads: int) -> Embedder:
    if path.exists():
        try:
            return OnnxImageEncoder(path, size, providers, threads)
        except Exception:
            log.exception("Failed to load image encoder %s; using dev embedder", path)
    else:
        log.warning("No image encoder at %s; using dev hash embedder (no real geolocation)", path)
    return HashEmbedder()

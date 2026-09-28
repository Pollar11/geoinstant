"""Small runtime utilities: result cache, rate limiter, feedback store, privacy policy."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Generic, TypeVar

import numpy as np
from numpy.typing import NDArray

from .models.detector import Detection
from .schemas import FeedbackRequest, FeedbackResponse

T = TypeVar("T")


class TtlLru(Generic[T]):
    """Thread-safe LRU with per-entry expiry. Holds results and embeddings, never images."""

    def __init__(self, maxsize: int, ttl_s: float) -> None:
        self.maxsize, self.ttl = maxsize, ttl_s
        self._d: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> T | None:
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            if item[0] < time.monotonic():
                del self._d[key]
                return None
            self._d.move_to_end(key)
            return item[1]

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._d[key] = (time.monotonic() + self.ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)


class TokenBucket:
    """In-process per-client token bucket (use Redis or the edge for multiple replicas)."""

    def __init__(self, per_minute: int, burst: int, max_clients: int = 100_000) -> None:
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self._b: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_clients = max_clients

    def take(self, client: str, cost: float = 1.0) -> float:
        """Consume ``cost`` tokens. Returns 0 if allowed, else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._b.get(client, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= cost:
                self._b[client] = (tokens - cost, now)
                self._b.move_to_end(client)
                if len(self._b) > self.max_clients:
                    self._b.popitem(last=False)
                return 0.0
            self._b[client] = (tokens, now)
            return (cost - tokens) / self.rate if self.rate > 0 else 60.0


class FeedbackStore:
    """Append-only corrections store: embedding + lat/lon; pixels only with consent."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def save(self, req: FeedbackRequest, embedding: NDArray[np.float32] | None, embedder: str) -> FeedbackResponse:
        fid = secrets.token_hex(8)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        stored_image = False
        if req.consent_store_image and req.image_base64:
            try:
                raw = base64.b64decode(req.image_base64, validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if raw:
                (folder / f"{fid}.jpg").write_bytes(raw)
                stored_image = True
        record = {
            "feedback_id": fid,
            "request_id": req.request_id,
            "received_at": datetime.now(UTC).isoformat(),
            "latitude": req.latitude,
            "longitude": req.longitude,
            "place_name": req.place_name,
            "comment": req.comment,
            "was_correct": req.was_correct,
            "embedder": embedder,
            "embedding": base64.b64encode(embedding.astype(np.float16).tobytes()).decode() if embedding is not None else None,
            "image": f"{fid}.jpg" if stored_image else None,
            "status": "pending_review",  # moderated before use
        }
        with self._lock, (folder / "feedback.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return FeedbackResponse(accepted=True, feedback_id=fid, stored_image=stored_image, stored_embedding=embedding is not None)


def people_are_main_subject(detections: list[Detection], min_area: float = 0.08) -> bool:
    """True when a person/face box covers a meaningful part of the frame."""
    return any(d.key in ("person", "face") and d.score >= 0.5 and d.area >= min_area for d in detections)


def coarsen(lat: float, lon: float, radius_km: float, min_radius_km: float) -> tuple[float, float, float]:
    """Snap to a ~11 km grid and widen the radius: city-level at best."""
    return round(lat, 1), round(lon, 1), max(radius_km, min_radius_km)

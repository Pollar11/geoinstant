"""Street match (visual place recognition), the GeoSpy/Rainbolt approach.

photo → compare with every street-level photo of an area (global descriptor, fast)
      → re-check the best 60 point-by-point (SIFT + RANSAC)
      → the verified match gives the exact spot and camera direction.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel

from ..models.embedder import Embedder
from .mapillary import MapillaryClient, StreetImage
from .verify import features, inliers

log = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # south, west, north, east
VERIFY_TOP = 60
VERIFIED_INLIERS = 30  # below this, a match is "possible", not confirmed
SAME_SPOT_M = 60


class Match(BaseModel):
    image_id: str
    latitude: float
    longitude: float
    heading: float
    captured_at: str
    image_url: str
    similarity: float
    inliers: int


class StreetResult(BaseModel):
    verified: bool
    best: Match | None
    candidates: list[Match]
    searched: int
    bbox: BBox
    message: str = ""


class Job(BaseModel):
    id: str
    status: Literal["queued", "listing", "downloading", "matching", "verifying", "done", "error"] = "queued"
    progress: float = 0.0
    message: str = ""
    result: StreetResult | None = None
    photo_id: str | None = None


def area_km2(b: BBox) -> float:
    s, w, n, e = b
    return abs(n - s) * 111.32 * abs(e - w) * 111.32 * math.cos(math.radians((s + n) / 2))


def _dist_m(a: Match, b: Match) -> float:
    dy = (a.latitude - b.latitude) * 111_320
    dx = (a.longitude - b.longitude) * 111_320 * math.cos(math.radians(a.latitude))
    return math.hypot(dx, dy)


class EmbeddingCache:
    """Descriptors by street-photo id, so overlapping or repeated searches only embed new photos."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (id TEXT PRIMARY KEY, v BLOB NOT NULL)")
        self._lock = threading.Lock()

    def get(self, ids: list[str]) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        with self._lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                q = f"SELECT id, v FROM emb WHERE id IN ({','.join('?' * len(chunk))})"
                out.update((k, np.frombuffer(v, dtype=np.float16)) for k, v in self._db.execute(q, chunk))
        return out

    def put(self, items: dict[str, np.ndarray]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO emb VALUES (?, ?)", [(k, v.astype(np.float16).tobytes()) for k, v in items.items()]
            )
            self._db.commit()


class StreetMatchService:
    def __init__(
        self,
        cache_dir: Path,
        embedder: Embedder,
        client: MapillaryClient | None,
        max_area_km2: float = 6.0,
        max_images: int = 25_000,
    ) -> None:
        self.embedder = embedder
        self.client = client
        self.max_area_km2 = max_area_km2
        self.max_images = max_images
        self.cache = EmbeddingCache(cache_dir / f"emb_{embedder.name.replace(':', '_')}.sqlite")
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._slot = asyncio.Semaphore(1)  # one search at a time: they are download-heavy

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def start(
        self, img: Image.Image, bbox: BBox, photo_id: str | None = None, on_done: Callable[[Job], Awaitable[None]] | None = None
    ) -> Job:
        self.check(bbox)
        job = Job(id=uuid.uuid4().hex[:12], photo_id=photo_id, message="Waiting for the previous search…")
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, img, bbox, on_done))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def check(self, bbox: BBox) -> None:
        if self.client is None:
            raise ValueError("Street match needs GEOINSTANT_MAPILLARY_TOKEN")
        if area_km2(bbox) > self.max_area_km2:
            raise ValueError(f"Zoom in: street match searches up to {self.max_area_km2:g} km² at a time")

    async def _run(self, job: Job, img: Image.Image, bbox: BBox, on_done: Callable[[Job], Awaitable[None]] | None) -> None:
        async with self._slot:
            try:
                job.result = await self.search(img, bbox, job)
                job.status, job.progress, job.message = "done", 1.0, job.result.message
            except Exception as e:
                log.exception("street match failed")
                job.status, job.message = "error", str(e)[:300] or "Street match failed"
        if on_done:
            await on_done(job)

    async def _index(self, bbox: BBox, job: Job) -> tuple[list[StreetImage], np.ndarray]:
        assert self.client is not None
        job.status, job.message = "listing", "Finding street photos in this area…"
        imgs = await self.client.list_images(bbox)
        if not imgs:
            raise ValueError("No street-level photos exist for this area yet")
        if len(imgs) > self.max_images:
            raise ValueError(f"{len(imgs):,} street photos here - zoom in to a smaller area")

        known = await asyncio.to_thread(self.cache.get, [i.id for i in imgs])
        todo = [i for i in imgs if i.id not in known]
        job.status = "downloading"
        new: dict[str, np.ndarray] = {}
        done = 0

        async def one(im: StreetImage) -> None:
            nonlocal done
            try:
                data = await self.client.fetch(im.thumb_url)  # type: ignore[union-attr]
                new[im.id] = await asyncio.to_thread(lambda: self.embedder.embed(Image.open(io.BytesIO(data)).convert("RGB")))
            except Exception:  # noqa: BLE001 - a missing thumbnail is not fatal
                pass
            done += 1
            job.progress = 0.7 * done / len(todo)
            job.message = f"Reading street photos {done:,}/{len(todo):,}"

        await asyncio.gather(*(one(im) for im in todo))
        await asyncio.to_thread(self.cache.put, new)
        known.update(new)
        kept = [i for i in imgs if i.id in known]
        if not kept:
            raise ValueError("Could not download any street photos for this area")
        return kept, np.stack([known[i.id] for i in kept]).astype(np.float32)

    async def search(self, img: Image.Image, bbox: BBox, job: Job | None = None) -> StreetResult:
        job = job or Job(id="inline")
        self.check(bbox)
        assert self.client is not None
        images, emb = await self._index(bbox, job)

        job.status, job.message = "matching", "Finding the most similar views…"
        q = await asyncio.to_thread(self.embedder.embed, img)
        sims = emb @ q
        top = [int(i) for i in np.argsort(-sims)[:VERIFY_TOP]]

        job.status, job.message = "verifying", "Checking the best matches point by point…"
        qf = await asyncio.to_thread(features, img)
        scores: dict[int, int] = {}
        t0 = time.perf_counter()

        async def verify(i: int) -> None:
            try:
                data = await self.client.fetch(images[i].full_url)  # type: ignore[union-attr]
                cf = await asyncio.to_thread(lambda: features(Image.open(io.BytesIO(data)).convert("RGB")))
                scores[i] = await asyncio.to_thread(inliers, qf, cf)
            except Exception:  # noqa: BLE001
                scores[i] = 0
            job.progress = 0.7 + 0.3 * len(scores) / len(top)
            job.message = f"Checking the best matches point by point {len(scores)}/{len(top)}"

        await asyncio.gather(*(verify(i) for i in top))
        log.info("verified %d candidates in %.1fs", len(top), time.perf_counter() - t0)

        ranked = sorted(top, key=lambda i: (-scores[i], -sims[i]))
        cands = [
            Match(
                image_id=images[i].id,
                latitude=images[i].lat,
                longitude=images[i].lon,
                heading=images[i].heading,
                captured_at=images[i].captured_at,
                image_url=images[i].full_url,
                similarity=round(float(sims[i]), 3),
                inliers=scores[i],
            )
            for i in ranked
        ]
        best = cands[0] if cands else None
        # Neighbouring frames of the same spot also match; the runner-up is the best *elsewhere*.
        runner = max((c.inliers for c in cands[1:] if best and _dist_m(best, c) > SAME_SPOT_M), default=0)
        cands = cands[:5]
        verified = bool(best and best.inliers >= VERIFIED_INLIERS and best.inliers >= 1.5 * max(runner, 1))
        if verified and best:
            msg = f"Same place: {best.inliers} matching points with a street photo from {best.captured_at or 'an unknown date'}."
        else:
            msg = f"No street photo here matches for certain ({len(images):,} checked). Try a neighbouring area."
        return StreetResult(verified=verified, best=best, candidates=cands, searched=len(images), bbox=bbox, message=msg)

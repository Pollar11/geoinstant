"""Street match (visual place recognition), the GeoSpy/Rainbolt approach.

photo → compare with every street-level photo of an area (global descriptor, fast)
      → re-check the best 60 point-by-point (LightGlue or SIFT, + RANSAC)
      → the verified match gives the exact spot and camera direction
      → OpenStreetMap gives the address of the building it faces.
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
from typing import Any, Literal

import httpx
import numpy as np
from PIL import Image
from pydantic import BaseModel

from ..models.embedder import Embedder
from .address import Building, facing_building
from .mapillary import StreetImage
from .sources import StreetSource
from .verify import Matcher, SiftMatcher

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
    source: str = "Mapillary"
    page_url: str = ""


class StreetResult(BaseModel):
    verified: bool
    best: Match | None
    candidates: list[Match]
    searched: int
    bbox: BBox
    message: str = ""
    building: Building | None = None


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


def rings_around(lat: float, lon: float, max_km2: float, cell_km: float = 1.0) -> list[list[BBox]]:
    """1 km squares around a point, grouped in rings: ring 0 is the centre square, ring r the squares r away."""
    dlat = cell_km / 111.32
    dlon = cell_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
    n = max(0, int((math.sqrt(max_km2 / cell_km**2) - 1) // 2))

    def cell(i: int, j: int) -> BBox:
        s, w = lat + (i - 0.5) * dlat, lon + (j - 0.5) * dlon
        return (s, w, s + dlat, w + dlon)

    return [[cell(i, j) for i in range(-r, r + 1) for j in range(-r, r + 1) if max(abs(i), abs(j)) == r] for r in range(n + 1)]


def outer_box(cells: list[BBox]) -> BBox:
    return (min(c[0] for c in cells), min(c[1] for c in cells), max(c[2] for c in cells), max(c[3] for c in cells))


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
        client: StreetSource | None,
        max_area_km2: float = 6.0,
        max_images: int = 25_000,
        matcher: Matcher | None = None,
        overpass_url: str = "",
    ) -> None:
        self.embedder = embedder
        self.client = client
        self.matcher: Matcher = matcher or SiftMatcher()
        self.overpass_url = overpass_url
        self._http = httpx.AsyncClient(timeout=30)
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
        return self._start(lambda job: self.search(img, bbox, job), photo_id, on_done)

    def start_around(
        self,
        img: Image.Image,
        lat: float,
        lon: float,
        max_km2: float,
        photo_id: str | None = None,
        on_done: Callable[[Job], Awaitable[None]] | None = None,
    ) -> Job:
        if self.client is None:
            raise ValueError("No street photo source is configured")
        return self._start(lambda job: self.search_around(img, lat, lon, max_km2, job), photo_id, on_done)

    def _start(
        self,
        work: Callable[[Job], Awaitable[StreetResult]],
        photo_id: str | None,
        on_done: Callable[[Job], Awaitable[None]] | None,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], photo_id=photo_id, message="Waiting for the previous search…")
        for old in [k for k, j in self.jobs.items() if j.status in ("done", "error")][: max(0, len(self.jobs) - 500)]:
            del self.jobs[old]  # keep memory bounded; results that matter are saved elsewhere
        self.jobs[job.id] = job
        task = asyncio.create_task(self._run(job, work, on_done))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def check(self, bbox: BBox) -> None:
        if self.client is None:
            raise ValueError("No street photo source is configured")
        if area_km2(bbox) > self.max_area_km2:
            raise ValueError(f"Zoom in: street match searches up to {self.max_area_km2:g} km² at a time")

    async def _run(
        self, job: Job, work: Callable[[Job], Awaitable[StreetResult]], on_done: Callable[[Job], Awaitable[None]] | None
    ) -> None:
        async with self._slot:
            try:
                job.result = await work(job)
                job.status, job.progress, job.message = "done", 1.0, job.result.message
            except Exception as e:
                log.exception("street match failed")
                job.status, job.message = "error", str(e)[:300] or "Street match failed"
        if on_done:
            await on_done(job)

    async def _index(
        self, bbox: BBox, job: Job, prog: tuple[float, float] = (0.0, 0.7), required: bool = True
    ) -> tuple[list[StreetImage], np.ndarray]:
        assert self.client is not None
        job.status, job.message = "listing", "Finding street photos in this area…"
        imgs = await self.client.list_images(bbox)
        if not imgs:
            if required:
                raise ValueError("No street-level photos exist for this area yet")
            return [], np.zeros((0, 1), np.float32)
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
            job.progress = prog[0] + (prog[1] - prog[0]) * done / len(todo)
            job.message = f"Reading street photos {done:,}/{len(todo):,}"

        await asyncio.gather(*(one(im) for im in todo))
        await asyncio.to_thread(self.cache.put, new)
        known.update(new)
        kept = [i for i in imgs if i.id in known]
        if not kept:
            if required:
                raise ValueError("Could not download any street photos for this area")
            return [], np.zeros((0, 1), np.float32)
        return kept, np.stack([known[i.id] for i in kept]).astype(np.float32)

    async def _verify(
        self, images: list[StreetImage], sims: np.ndarray, qf: Any, scores: dict[int, int], job: Job, prog: tuple[float, float]
    ) -> None:
        """Point-by-point check of the most similar views not checked yet."""
        top = [int(i) for i in np.argsort(-sims) if int(i) not in scores][:VERIFY_TOP]
        job.status, job.message = "verifying", "Checking the best matches point by point…"
        mt, done = self.matcher, 0
        t0 = time.perf_counter()

        async def one(i: int) -> None:
            nonlocal done
            try:
                data = await self.client.fetch(images[i].full_url)  # type: ignore[union-attr]
                cf = await asyncio.to_thread(lambda: mt.prepare(Image.open(io.BytesIO(data)).convert("RGB")))
                scores[i] = await asyncio.to_thread(mt.inliers, qf, cf)
            except Exception:  # noqa: BLE001
                scores[i] = 0
            done += 1
            job.progress = prog[0] + (prog[1] - prog[0]) * done / len(top)
            job.message = f"Checking the best matches point by point {done}/{len(top)}"

        await asyncio.gather(*(one(i) for i in top))
        log.info("verified %d candidates with %s in %.1fs", len(top), mt.name, time.perf_counter() - t0)

    async def search(self, img: Image.Image, bbox: BBox, job: Job | None = None) -> StreetResult:
        job = job or Job(id="inline")
        self.check(bbox)
        images, emb = await self._index(bbox, job)
        job.status, job.message = "matching", "Finding the most similar views…"
        q = await asyncio.to_thread(self.embedder.embed, img)
        qf = await asyncio.to_thread(self.matcher.prepare, img)
        sims = emb @ q
        scores: dict[int, int] = {}
        await self._verify(images, sims, qf, scores, job, (0.7, 1.0))
        return await self._result(images, sims, scores, bbox)

    async def search_around(
        self, img: Image.Image, lat: float, lon: float, max_km2: float, job: Job | None = None
    ) -> StreetResult:
        """Search 1 km squares in rings outward from a lead until one street photo is verified."""
        job = job or Job(id="inline")
        if self.client is None:
            raise ValueError("No street photo source is configured")
        q = await asyncio.to_thread(self.embedder.embed, img)
        qf = await asyncio.to_thread(self.matcher.prepare, img)
        rings = rings_around(lat, lon, max_km2)
        images: list[StreetImage] = []
        embs: list[np.ndarray] = []
        seen: set[str] = set()
        scores: dict[int, int] = {}
        result: StreetResult | None = None
        for r, ring in enumerate(rings):
            lo, hi = r / len(rings), (r + 1) / len(rings)
            for cell in ring:
                imgs, e = await self._index(cell, job, (lo, lo + 0.7 * (hi - lo)), required=False)
                for im, v in zip(imgs, e, strict=True):
                    if im.id not in seen:
                        seen.add(im.id)
                        images.append(im)
                        embs.append(v)
                if len(images) > self.max_images * 4:
                    break
            if not images:
                continue
            sims = np.stack(embs) @ q
            await self._verify(images, sims, qf, scores, job, (lo + 0.7 * (hi - lo), hi))
            result = await self._result(images, sims, scores, outer_box(ring + [c for rg in rings[:r] for c in rg]))
            if result.verified:
                return result
            job.message = f"Not found within {(2 * r + 1) ** 2} km² yet, widening the search…"
        if result is None:
            raise ValueError("No street-level photos exist around this place yet")
        return result

    async def _result(self, images: list[StreetImage], sims: np.ndarray, scores: dict[int, int], bbox: BBox) -> StreetResult:
        ranked = sorted(scores, key=lambda i: (-scores[i], -sims[i]))
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
                source=images[i].source,
                page_url=images[i].page_url,
            )
            for i in ranked
        ]
        best = cands[0] if cands else None
        # Neighbouring frames of the same spot also match; the runner-up is the best *elsewhere*.
        runner = max((c.inliers for c in cands[1:] if best and _dist_m(best, c) > SAME_SPOT_M), default=0)
        cands = cands[:5]
        verified = bool(best and best.inliers >= VERIFIED_INLIERS and best.inliers >= 1.5 * max(runner, 1))
        building = None
        if verified and best and self.overpass_url:
            try:
                building = await facing_building(self._http, self.overpass_url, best.latitude, best.longitude, best.heading)
            except (httpx.HTTPError, ValueError, KeyError):  # the spot stands without an address
                log.warning("address lookup failed", exc_info=True)
        if verified and best:
            msg = f"Same place: {best.inliers} matching points with a street photo from {best.captured_at or 'an unknown date'}."
        else:
            msg = f"No street photo here matches for certain ({len(images):,} checked). Try a neighbouring area."
        return StreetResult(
            verified=verified, best=best, candidates=cands, searched=len(images), bbox=bbox, message=msg, building=building
        )

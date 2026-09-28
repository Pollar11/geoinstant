"""Street-level photos of an area from Mapillary (CC-BY-SA; free token from mapillary.com/dashboard/developers)."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

import httpx

GRAPH = "https://graph.mapillary.com/images"
LIMIT = 2000
FIELDS = "id,computed_geometry,compass_angle,captured_at,thumb_256_url,thumb_1024_url"


@dataclass(frozen=True)
class StreetImage:
    id: str
    lat: float
    lon: float
    heading: float
    captured_at: str
    thumb_url: str
    full_url: str


def tiles(bbox: tuple[float, float, float, float], step: float = 0.004) -> list[tuple[float, float, float, float]]:
    """Split the box into ~400 m tiles (the API returns at most 2,000 images per request)."""
    s, w, n, e = bbox
    out = []
    for i in range(max(1, math.ceil((n - s) / step))):
        for j in range(max(1, math.ceil((e - w) / step))):
            out.append((s + i * step, w + j * step, min(n, s + (i + 1) * step), min(e, w + (j + 1) * step)))
    return out


def _date(ms: object) -> str:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(float(ms) / 1000, UTC).date().isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""


class MapillaryClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None, concurrency: int = 16) -> None:
        self.token = token
        self.http = http or httpx.AsyncClient(timeout=30)
        self.sem = asyncio.Semaphore(concurrency)

    async def _tile(self, t: tuple[float, float, float, float]) -> list[StreetImage]:
        s, w, n, e = t
        params = {"access_token": self.token, "fields": FIELDS, "bbox": f"{w},{s},{e},{n}", "limit": str(LIMIT)}
        async with self.sem:
            r = await self.http.get(GRAPH, params=params)
        r.raise_for_status()
        data = r.json().get("data", [])
        if len(data) >= LIMIT and n - s > 0.0005:  # full page: split so nothing is cut off
            ms, mw = (s + n) / 2, (w + e) / 2
            parts = [(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]
            return [im for p in await asyncio.gather(*(self._tile(q) for q in parts)) for im in p]
        out = []
        for d in data:
            geom = (d.get("computed_geometry") or {}).get("coordinates")
            if not geom or not d.get("thumb_256_url"):
                continue
            out.append(
                StreetImage(
                    id=str(d["id"]),
                    lat=float(geom[1]),
                    lon=float(geom[0]),
                    heading=float(d.get("compass_angle") or 0.0),
                    captured_at=_date(d.get("captured_at")),
                    thumb_url=d["thumb_256_url"],
                    full_url=d.get("thumb_1024_url") or d["thumb_256_url"],
                )
            )
        return out

    async def list_images(self, bbox: tuple[float, float, float, float]) -> list[StreetImage]:
        found = await asyncio.gather(*(self._tile(t) for t in tiles(bbox)))
        seen: dict[str, StreetImage] = {}
        for batch in found:
            for im in batch:
                seen.setdefault(im.id, im)
        return list(seen.values())

    async def fetch(self, url: str) -> bytes:
        async with self.sem:
            r = await self.http.get(url)
        r.raise_for_status()
        return r.content

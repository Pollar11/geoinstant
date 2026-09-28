"""Street address for a coordinate (OpenStreetMap Nominatim, max 1 request/second)."""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)


class PlaceHit(BaseModel):
    name: str
    latitude: float
    longitude: float


class Nominatim:
    def __init__(self, url: str, user_agent: str, http: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=15, headers={"User-Agent": user_agent})
        self._lock = asyncio.Lock()

    async def _get(self, path: str, params: dict[str, str | int | float]) -> object:
        async with self._lock:
            try:
                r = await self.http.get(f"{self.url}/{path}", params={**params, "format": "jsonv2"})
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError):
                log.warning("nominatim %s failed", path, exc_info=True)
                return None
            finally:
                await asyncio.sleep(1.0)  # usage policy

    async def address(self, lat: float, lon: float) -> str | None:
        """e.g. 'Hotel Danieli, Riva degli Schiavoni 4196, Venezia, 30122, Italia'; None if unknown or unreachable."""
        body = await self._get("reverse", {"lat": lat, "lon": lon, "zoom": 18})
        name = body.get("display_name") if isinstance(body, dict) else None
        return str(name) if name else None

    async def search(self, q: str, limit: int = 6) -> list[PlaceHit]:
        """A hotel, restaurant, street or town by name."""
        body = await self._get("search", {"q": q, "limit": limit})
        return [
            PlaceHit(name=str(h["display_name"]), latitude=float(h["lat"]), longitude=float(h["lon"]))
            for h in (body if isinstance(body, list) else [])
            if h.get("display_name") and h.get("lat") and h.get("lon")
        ]

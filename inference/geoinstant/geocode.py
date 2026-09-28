"""Street address for a coordinate (OpenStreetMap Nominatim, max 1 request/second)."""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class Nominatim:
    def __init__(self, url: str, user_agent: str, http: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=15, headers={"User-Agent": user_agent})
        self._lock = asyncio.Lock()

    async def address(self, lat: float, lon: float) -> str | None:
        """e.g. 'Hotel Danieli, Riva degli Schiavoni 4196, Venezia, 30122, Italia'; None if unknown or unreachable."""
        async with self._lock:
            try:
                r = await self.http.get(f"{self.url}/reverse", params={"lat": lat, "lon": lon, "zoom": 18, "format": "jsonv2"})
                r.raise_for_status()
                name = r.json().get("display_name")
            except (httpx.HTTPError, ValueError):
                log.warning("reverse geocoding failed", exc_info=True)
                name = None
            await asyncio.sleep(1.0)  # usage policy
        return str(name) if name else None

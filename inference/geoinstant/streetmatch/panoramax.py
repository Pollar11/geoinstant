"""Street-level photos from Panoramax, the open (CC-BY-SA) geo-commons. No token needed."""

from __future__ import annotations

import asyncio

import httpx

from .mapillary import StreetImage

PAGE = 1000
MAX_PAGES = 30


class PanoramaxClient:
    def __init__(
        self, api: str = "https://api.panoramax.xyz/api", http: httpx.AsyncClient | None = None, concurrency: int = 8
    ) -> None:
        self.api = api.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self.sem = asyncio.Semaphore(concurrency)

    async def list_images(self, bbox: tuple[float, float, float, float]) -> list[StreetImage]:
        s, w, n, e = bbox
        url: str | None = f"{self.api}/search"
        params: dict[str, str] | None = {"bbox": f"{w},{s},{e},{n}", "limit": str(PAGE)}
        out: list[StreetImage] = []
        for _ in range(MAX_PAGES):
            if url is None:
                break
            async with self.sem:
                r = await self.http.get(url, params=params)
            r.raise_for_status()
            body = r.json()
            for f in body.get("features", []):
                assets = f.get("assets") or {}
                thumb = (assets.get("thumb") or {}).get("href")
                full = (assets.get("sd") or assets.get("hd") or {}).get("href") or thumb
                coords = (f.get("geometry") or {}).get("coordinates")
                if not thumb or not coords:
                    continue
                props = f.get("properties") or {}
                out.append(
                    StreetImage(
                        id=f"pnx:{f['id']}",
                        lat=float(coords[1]),
                        lon=float(coords[0]),
                        heading=float(props.get("view:azimuth") or 0.0),
                        captured_at=str(props.get("datetime") or "")[:10],
                        thumb_url=thumb,
                        full_url=str(full),
                        source="Panoramax",
                        page_url=f"https://api.panoramax.xyz/#focus=pic&pic={f['id']}",
                    )
                )
            url = next((lk["href"] for lk in body.get("links", []) if lk.get("rel") == "next"), None)
            params = None  # the next link carries its own query
        return out

    async def fetch(self, url: str) -> bytes:
        async with self.sem:
            r = await self.http.get(url)
        r.raise_for_status()
        return r.content

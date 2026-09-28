"""Reverse image search (Google Cloud Vision web detection) and a safe image fetcher.

How interiors get found: a café's lamp, ceiling or tiles also appear in the venue's photos on maps,
review and booking sites. Web detection returns pages with matching or similar images, best-guess
labels and landmarks; the investigator reads those pages and compares the candidate photos.
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

log = logging.getLogger(__name__)

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _jpeg(img: Image.Image, max_side: int = 1600) -> bytes:
    im = img.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class ReverseImage:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None, resolve: Any = None) -> None:
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=30)
        self._resolve = resolve or _resolve_public

    async def search(self, img: Image.Image) -> dict[str, Any]:
        """Web matches for the image: labels, entities, pages, similar images, landmarks."""
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(_jpeg(img)).decode()},
                    "features": [{"type": "WEB_DETECTION", "maxResults": 20}, {"type": "LANDMARK_DETECTION", "maxResults": 5}],
                    "imageContext": {"webDetectionParams": {"includeGeoResults": True}},
                }
            ]
        }
        r = await self.http.post(VISION_URL, params={"key": self.api_key}, json=body)
        r.raise_for_status()
        res = (r.json().get("responses") or [{}])[0]
        if res.get("error"):
            raise RuntimeError(res["error"].get("message", "Vision API error"))
        web = res.get("webDetection") or {}
        return {
            "best_guess": [x.get("label") for x in web.get("bestGuessLabels", []) if x.get("label")],
            "entities": [
                {"name": e["description"], "score": round(float(e.get("score", 0)), 2)}
                for e in web.get("webEntities", [])
                if e.get("description")
            ][:12],
            "pages": [
                {"title": p.get("pageTitle", "")[:160], "url": p["url"]}
                for p in web.get("pagesWithMatchingImages", [])
                if p.get("url")
            ][:10],
            "matching_images": [
                x["url"] for x in web.get("fullMatchingImages", []) + web.get("partialMatchingImages", []) if x.get("url")
            ][:8],
            "similar_images": [x["url"] for x in web.get("visuallySimilarImages", []) if x.get("url")][:10],
            "landmarks": [
                {
                    "name": lm.get("description", ""),
                    "score": round(float(lm.get("score", 0)), 2),
                    "latitude": lm["locations"][0]["latLng"]["latitude"],
                    "longitude": lm["locations"][0]["latLng"]["longitude"],
                }
                for lm in res.get("landmarkAnnotations", [])
                if lm.get("locations")
            ],
        }

    async def fetch_image(self, url: str) -> Image.Image:
        """Download a candidate photo so the investigator can compare it."""
        return await fetch_public_image(self.http, url, self._resolve)


async def fetch_public_image(http: httpx.AsyncClient, url: str, resolve: Any = None) -> Image.Image:
    """Download an image from a public http(s) host (re-checked on every redirect), capped at 8 MB."""
    check = resolve or _resolve_public
    for _ in range(4):  # follow up to 3 redirects, re-checking each hop
        await check(url)
        async with http.stream("GET", url, follow_redirects=False, headers={"User-Agent": "GeoInstant/1.0"}) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                raise ValueError("Not an image")
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("Image too large")
        img = Image.open(io.BytesIO(bytes(data)))
        img.draft("RGB", (1600, 1600))
        return img.convert("RGB")
    raise ValueError("Too many redirects")


async def _resolve_public(url: str) -> None:
    """Refuse anything that isn't a public internet host (no localhost, LAN or cloud metadata)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Only http(s) URLs")
    infos = await asyncio.get_running_loop().getaddrinfo(
        u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM
    )
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("Not a public address")

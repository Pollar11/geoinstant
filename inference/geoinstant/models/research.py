"""Candidate research, the step after "this looks like an Orlando resort": list and check every candidate.

- find_places: every place in an area matching OpenStreetMap tags (water slides, lighthouses, churches,
  hotels, piers...), with coordinates.
- satellite: the aerial view of a candidate, to compare layouts (pool shape, roofs, roads, coastline).
- street_photos: real ground-level photos at a candidate (Mapillary), to confirm from the street.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx
from PIL import Image

from ..nearby import NearbyImage, nearby
from .reverse_image import fetch_public_image

TAG = re.compile(r"^[a-z0-9_:]{1,40}(=[A-Za-z0-9_ :.\-]{1,60})?$")
NAME = re.compile(r"^[\w .,'&\-]{1,60}$")
MAX_AREA_KM2 = 20_000  # a metro area or a small region; Overpass times out beyond that
KEEP_TAGS = ("name", "tourism", "leisure", "amenity", "attraction", "building", "brand", "website", "operator")

BBox = tuple[float, float, float, float]  # south, west, north, east


def area_km2(b: BBox) -> float:
    s, w, n, e = b
    return abs(n - s) * 111.32 * abs(e - w) * 111.32 * math.cos(math.radians((s + n) / 2))


def overpass_query(bbox: BBox, tags: list[str], name: str | None) -> str:
    s, w, n, e = bbox
    box = f"({s:.5f},{w:.5f},{n:.5f},{e:.5f})"
    name_filter = f'["name"~"{name}",i]' if name else ""
    parts = []
    for t in tags:
        k, _, v = t.partition("=")
        cond = f'["{k}"="{v}"]' if v else f'["{k}"]'
        parts.append(f"nwr{cond}{name_filter}{box};")
    return f"[out:json][timeout:25];({''.join(parts)});out center tags 60;"


class Research:
    def __init__(
        self,
        overpass_url: str = "",
        mapillary_token: str = "",
        satellite_template: str = "",
        http: httpx.AsyncClient | None = None,
        resolve: Any = None,
    ) -> None:
        self.overpass_url = overpass_url
        self.mapillary_token = mapillary_token
        self.satellite_template = satellite_template
        self.http = http or httpx.AsyncClient(timeout=40)
        self._resolve = resolve

    def tools(self) -> list[dict[str, Any]]:
        return [t for t in TOOLS if self._has(t["name"])]

    def _has(self, name: str) -> bool:
        return {
            "find_places": bool(self.overpass_url),
            "street_photos": bool(self.mapillary_token),
            "view_satellite": bool(self.satellite_template),
        }[name]

    async def find_places(self, bbox: BBox, tags: list[str], name: str | None = None) -> list[dict[str, Any]]:
        tags = [t.strip() for t in tags if t.strip()][:6]
        if not tags or not all(TAG.match(t) for t in tags):
            raise ValueError('tags must look like "leisure=water_park" or "attraction=water_slide"')
        if name is not None and not NAME.match(name):
            raise ValueError("name may only contain letters, digits and spaces")
        if area_km2(bbox) > MAX_AREA_KM2:
            raise ValueError(f"Area too large (max {MAX_AREA_KM2:,} km²): narrow it to a city or county")
        r = await self.http.post(self.overpass_url, data={"data": overpass_query(bbox, tags, name)})
        r.raise_for_status()
        out = []
        for el in r.json().get("elements", []):
            c = el.get("center") or el
            if "lat" not in c:
                continue
            t = el.get("tags") or {}
            out.append(
                {
                    "latitude": round(float(c["lat"]), 6),
                    "longitude": round(float(c["lon"]), 6),
                    **{k: t[k] for k in KEEP_TAGS if k in t},
                    **{k: v for k, v in t.items() if k.startswith("addr:")},
                }
            )
        return out[:60]

    async def satellite(self, lat: float, lon: float, zoom: int) -> Image.Image:
        url = self.satellite_template.format(lat=f"{lat:.6f}", lon=f"{lon:.6f}", zoom=max(12, min(zoom, 20)))
        return await fetch_public_image(self.http, url, self._resolve)

    async def street_photos(self, lat: float, lon: float, radius_m: float) -> list[tuple[dict[str, Any], Image.Image]]:
        near = await nearby(lat, lon, max(20.0, min(radius_m, 300.0)), self.mapillary_token)
        picked: list[NearbyImage] = []
        headings: list[float] = []
        for im in sorted(near.images, key=lambda i: i.distance_m):
            h = im.compass_angle or 0.0
            if all(abs((h - o + 180) % 360 - 180) > 45 for o in headings):  # different directions
                picked.append(im)
                headings.append(h)
            if len(picked) == 3:
                break
        out = []
        for im in picked:
            try:
                img = await fetch_public_image(self.http, im.thumb_url, self._resolve)
            except (httpx.HTTPError, ValueError):
                continue
            meta = {"distance_m": im.distance_m, "heading": im.compass_angle, "captured_at": im.captured_at}
            out.append((meta, img))
        return out


TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_places",
        "description": "List every place in an area that matches OpenStreetMap tags, with coordinates - the "
        "candidates to check. Area: a named place (e.g. 'Kissimmee, Florida') or a bbox. Tags, any of: "
        "'attraction=water_slide', 'leisure=water_park', 'leisure=swimming_pool', 'man_made=lighthouse', "
        "'man_made=pier', 'amenity=place_of_worship', 'tourism=hotel', 'historic=castle', 'natural=peak', ...",
        "input_schema": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "[south, west, north, east]"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "name": {"type": "string", "description": "optional part of the name"},
                "why": {"type": "string"},
            },
            "required": ["tags"],
        },
    },
    {
        "name": "view_satellite",
        "description": "Aerial view of a candidate spot, to compare layouts with the photo (pool and slide "
        "shapes, roofs, roads, coastline). zoom 17-19 for buildings, 14-16 for terrain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "zoom": {"type": "integer"},
                "why": {"type": "string"},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "street_photos",
        "description": "Up to 3 real ground-level photos taken near a candidate spot (different directions), to "
        "confirm it from the street.",
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "radius_m": {"type": "number"},
                "why": {"type": "string"},
            },
            "required": ["latitude", "longitude"],
        },
    },
]

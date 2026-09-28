"""The house the camera is looking at: OpenStreetMap buildings/addresses in the view direction (Overpass API)."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel

RADIUS_M = 80
HALF_FOV = 35.0


class Building(BaseModel):
    address: str
    latitude: float
    longitude: float
    distance_m: float
    in_view: bool  # False = nearest address, not necessarily the one in the photo


def _bearing(lat: float, lon: float, lat2: float, lon2: float) -> tuple[float, float]:
    dy = (lat2 - lat) * 111_320
    dx = (lon2 - lon) * 111_320 * math.cos(math.radians(lat))
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360, math.hypot(dx, dy)


def _address(tags: dict[str, str]) -> str:
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street") or tags.get("addr:place")) if x)
    town = " ".join(x for x in (tags.get("addr:postcode"), tags.get("addr:city")) if x)
    name = tags.get("name")
    return ", ".join(x for x in (name, street, town) if x)


async def facing_building(http: httpx.AsyncClient, url: str, lat: float, lon: float, heading: float) -> Building | None:
    q = f'[out:json][timeout:20];(nwr["addr:housenumber"](around:{RADIUS_M},{lat},{lon}););out center tags;'
    r = await http.post(url, data={"data": q})
    r.raise_for_status()
    best: tuple[tuple[int, float], Building] | None = None
    for el in r.json().get("elements", []):
        c = el.get("center") or el
        if "lat" not in c or not el.get("tags"):
            continue
        b, d = _bearing(lat, lon, c["lat"], c["lon"])
        off = abs((b - heading + 180) % 360 - 180)
        in_view = off <= HALF_FOV
        cand = Building(
            address=_address(el["tags"]), latitude=c["lat"], longitude=c["lon"], distance_m=round(d, 1), in_view=in_view
        )
        key = (0 if in_view else 1, d)
        if cand.address and (best is None or key < best[0]):
            best = (key, cand)
    return best[1] if best else None

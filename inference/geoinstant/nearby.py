"""Then & now: recent street-level photos near a spot (Mapillary) plus Street View / Mapillary links."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel

from .geo import haversine_km


class NearbyImage(BaseModel):
    id: str
    thumb_url: str
    captured_at: str | None
    latitude: float
    longitude: float
    compass_angle: float | None
    distance_m: float


class Nearby(BaseModel):
    images: list[NearbyImage]
    street_view_url: str
    mapillary_url: str
    satellite_url: str
    note: str = ""


def links(lat: float, lon: float, heading: float | None = None) -> tuple[str, str, str]:
    sv = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.6f},{lon:.6f}"
    if heading is not None:
        sv += f"&heading={heading:.0f}"
    mly = f"https://www.mapillary.com/app/?lat={lat:.6f}&lng={lon:.6f}&z=17"
    sat = f"https://www.google.com/maps/@{lat:.6f},{lon:.6f},250m/data=!3m1!1e3"
    return sv, mly, sat


async def nearby(lat: float, lon: float, radius_m: float, token: str, heading: float | None = None) -> Nearby:
    sv, mly, sat = links(lat, lon, heading)
    if not token:
        return Nearby(
            images=[],
            street_view_url=sv,
            mapillary_url=mly,
            satellite_url=sat,
            note="Set GEOINSTANT_MAPILLARY_TOKEN to show recent photos here.",
        )
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    params = {
        "access_token": token,
        "fields": "id,thumb_1024_url,captured_at,computed_geometry,compass_angle",
        "bbox": f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}",
        "limit": "60",
    }
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get("https://graph.mapillary.com/images", params=params)
    if r.status_code != 200:
        return Nearby(
            images=[], street_view_url=sv, mapillary_url=mly, satellite_url=sat, note="Street photo service unavailable."
        )
    out = []
    for d in r.json().get("data", []):
        geom = (d.get("computed_geometry") or {}).get("coordinates")
        if not geom or not d.get("thumb_1024_url"):
            continue
        ilon, ilat = float(geom[0]), float(geom[1])
        ts = d.get("captured_at")
        out.append(
            NearbyImage(
                id=str(d["id"]),
                thumb_url=d["thumb_1024_url"],
                captured_at=_iso(ts),
                latitude=ilat,
                longitude=ilon,
                compass_angle=d.get("compass_angle"),
                distance_m=round(float(haversine_km(lat, lon, ilat, ilon)) * 1000, 1),
            )
        )
    # Newest first among the closest ones.
    out.sort(key=lambda i: i.distance_m)
    out = sorted(out[:20], key=lambda i: i.captured_at or "", reverse=True)[:8]
    return Nearby(images=out, street_view_url=sv, mapillary_url=mly, satellite_url=sat)


def _iso(ms: object) -> str | None:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(float(ms) / 1000, UTC).date().isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

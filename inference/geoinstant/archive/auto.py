"""Hands-free search: from the investigation's lead to a verified spot, no map zooming needed.

lead (investigation, else the quick answer) → mountains? skyline match around it
                                            → outdoors? street match in rings around it
Only verified results pin the photo; everything else stays a lead.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from ..skyline.extract import detect
from ..skyline.service import SkylineService, margin_box
from .store import Row

INDOOR = {"home", "bar_restaurant", "other_indoor"}
SKYLINE_MIN_CONFIDENCE = 0.5  # in testing every search at or above this was right
NEAR_KM2 = 9.0  # street/neighbourhood-level lead: 3 x 3 km


def lead_point(row: Row, city_km2: float) -> tuple[float, float, float] | None:
    """(lat, lon, area to search in km²) or None when the lead is too vague (region or wider)."""
    rep = (row.investigation or {}).get("report") or {}
    if rep.get("latitude") is not None and rep.get("longitude") is not None:
        area = {"exact": NEAR_KM2, "street": NEAR_KM2, "neighborhood": NEAR_KM2, "city": city_km2}.get(rep.get("precision", ""))
        if area:
            return float(rep["latitude"]), float(rep["longitude"]), area
    r = row.result or {}
    if r.get("resolution") in ("street", "city") and float(r.get("confidence", 0)) >= 50:
        return float(r["latitude"]), float(r["longitude"]), city_km2
    return None


def scene_of(row: Row) -> str:
    return str(((row.result or {}).get("analysis") or {}).get("scene") or "other")


def skyline_around(svc: SkylineService, img: Image.Image, lat: float, lon: float, half_km: float) -> dict[str, Any]:
    r = svc.search(detect(img), margin_box((lat, lon, lat, lon), half_km))
    c = r.candidates[0] if r.status == "ok" and r.candidates else None
    return {
        "pinned": bool(c and r.confidence >= SKYLINE_MIN_CONFIDENCE),
        "status": r.status,
        "message": r.message,
        "confidence": round(r.confidence, 3),
        "latitude": c.lat if c else None,
        "longitude": c.lon if c else None,
        "azimuth": c.m.azimuth if c else None,
        "fov": c.m.fov if c else None,
    }

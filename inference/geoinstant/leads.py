"""Signed search leads for the public page.

The street search only runs where the investigation pointed, for that exact photo, and never when
people are the main subject (the public page shows those at city level only). The investigation
hands out a signed lead; the street search checks it against the uploaded bytes.
"""

from __future__ import annotations

import hashlib
import hmac

from pydantic import BaseModel

from .models.investigator import Report

NEAR_KM2 = 9.0  # street / neighbourhood lead: 3 x 3 km


class Lead(BaseModel):
    latitude: float
    longitude: float
    km2: float
    token: str


def _sign(secret: bytes, image: bytes, lat: float, lon: float, km2: float) -> str:
    msg = f"{hashlib.sha256(image).hexdigest()}|{lat:.6f}|{lon:.6f}|{km2:g}"
    return hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()


def issue(secret: bytes, image: bytes, report: Report | None, city_km2: float, people_policy: str) -> Lead | None:
    if report is None or report.latitude is None or report.longitude is None:
        return None
    if people_policy == "coarsen" and report.people_are_main_subject:
        return None
    km2 = {"exact": NEAR_KM2, "street": NEAR_KM2, "neighborhood": NEAR_KM2, "city": city_km2}.get(report.precision)
    if km2 is None:  # region or wider: too big to search street by street
        return None
    lat, lon = round(report.latitude, 6), round(report.longitude, 6)
    return Lead(latitude=lat, longitude=lon, km2=km2, token=_sign(secret, image, lat, lon, km2))


def valid(secret: bytes, image: bytes, lat: float, lon: float, km2: float, token: str) -> bool:
    return hmac.compare_digest(_sign(secret, image, lat, lon, km2), token)

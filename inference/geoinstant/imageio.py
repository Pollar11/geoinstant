"""Image decoding and EXIF/XMP GPS extraction."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any

from PIL import ExifTags, Image, ImageOps

try:  # HEIC/HEIF (iPhone default). Optional: without it HEIC uploads are rejected with 415.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on the install
    HEIC_SUPPORTED = False

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "MPO"}


class ImageError(ValueError):
    """Raised for inputs we refuse to process (with an HTTP status hint)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GpsFix:
    latitude: float
    longitude: float
    altitude_m: float | None
    source: str  # "exif" | "xmp"
    captured_at: str | None


@dataclass
class DecodedImage:
    image: Image.Image  # RGB, orientation applied, long edge ≤ work_size
    original_size: tuple[int, int]
    format: str
    sha256: str


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def open_image(data: bytes, max_pixels: int) -> Image.Image:
    if not data:
        raise ImageError("Empty upload")
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as e:
        raise ImageError("Image is too large", 413) from e
    except Exception as e:
        raise ImageError("Unsupported or corrupt image (JPEG, PNG, WebP, HEIC accepted)", 415) from e
    if img.format not in ALLOWED_FORMATS:
        raise ImageError(f"Unsupported image format {img.format}", 415)
    w, h = img.size
    if w * h > max_pixels:
        raise ImageError("Image is too large", 413)
    return img


def decode(data: bytes, work_size: int, max_pixels: int) -> DecodedImage:
    img = open_image(data, max_pixels)
    original = img.size
    fmt = img.format or "?"
    if fmt in ("JPEG", "MPO"):
        img.draft("RGB", (work_size, work_size))
    img = ImageOps.exif_transpose(img) or img
    img = img.convert("RGB")
    img.thumbnail((work_size, work_size), Image.Resampling.BILINEAR)
    return DecodedImage(image=img, original_size=original, format=fmt, sha256=content_hash(data))


# ---- GPS metadata ----
_GPS_IFD = 0x8825
_EXIF_IFD = 0x8769
_GPS = {v: k for k, v in ExifTags.GPSTAGS.items()}


def _to_float(x: Any) -> float:
    if isinstance(x, tuple) and len(x) == 2:  # legacy (num, den)
        return float(Fraction(int(x[0]), int(x[1]))) if x[1] else 0.0
    return float(x)


def _dms(values: Any, ref: Any) -> float | None:
    try:
        d, m, s = (_to_float(v) for v in values)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60.0 + s / 3600.0
    ref_s = ref.decode() if isinstance(ref, bytes) else str(ref or "")
    return -deg if ref_s.strip().upper() in ("S", "W") else deg


def _valid(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    # (0, 0) is what many cameras write when they had no fix.
    return not (abs(lat) < 1e-6 and abs(lon) < 1e-6)


def _exif_gps(img: Image.Image) -> GpsFix | None:
    try:
        exif = img.getexif()
        gps = exif.get_ifd(_GPS_IFD)
    except Exception:  # noqa: BLE001
        return None
    if not gps:
        return None
    lat = _dms(gps.get(_GPS["GPSLatitude"]), gps.get(_GPS["GPSLatitudeRef"])) if _GPS["GPSLatitude"] in gps else None
    lon = _dms(gps.get(_GPS["GPSLongitude"]), gps.get(_GPS["GPSLongitudeRef"])) if _GPS["GPSLongitude"] in gps else None
    if not _valid(lat, lon):
        return None
    alt = None
    if _GPS["GPSAltitude"] in gps:
        try:
            alt = _to_float(gps[_GPS["GPSAltitude"]])
            if gps.get(_GPS["GPSAltitudeRef"]) in (1, b"\x01"):
                alt = -alt
        except (TypeError, ValueError, ZeroDivisionError):
            alt = None
    captured = None
    try:
        raw = exif.get_ifd(_EXIF_IFD).get(0x9003) or exif.get(0x0132)
        if raw:
            captured = datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
    except (ValueError, TypeError):
        captured = None
    assert lat is not None and lon is not None
    return GpsFix(round(lat, 7), round(lon, 7), alt, "exif", captured)


# XMP: exif:GPSLatitude="37,46.494N", drone-dji:GpsLatitude="+37.774900", Iptc4xmpExt etc.
_XMP_LAT = re.compile(rb'(?i)GPSLatitude(?:="|>)\s*([^"<]+)')
_XMP_LON = re.compile(rb'(?i)GPSLongitude(?:="|>)\s*([^"<]+)')


def _xmp_coord(raw: bytes) -> float | None:
    s = raw.decode("ascii", "ignore").strip()
    ref = ""
    if s and s[-1].upper() in "NSEW":
        ref, s = s[-1].upper(), s[:-1]
    try:
        if "," in s:  # "deg,min.frac" or "deg,min,sec"
            parts = [float(p) for p in s.split(",")]
            val = parts[0] + parts[1] / 60 + (parts[2] / 3600 if len(parts) > 2 else 0)
        else:
            val = float(s)
    except ValueError:
        return None
    return -val if ref in ("S", "W") else val


def _xmp_gps(data: bytes) -> GpsFix | None:
    # XMP lives in the first few hundred KB (APP1 for JPEG, a chunk for PNG/WebP).
    head = data[: 512 * 1024]
    if b"<x:xmpmeta" not in head and b"xmpmeta" not in head:
        return None
    m_lat, m_lon = _XMP_LAT.search(head), _XMP_LON.search(head)
    if not (m_lat and m_lon):
        return None
    lat, lon = _xmp_coord(m_lat.group(1)), _xmp_coord(m_lon.group(1))
    if not _valid(lat, lon):
        return None
    assert lat is not None and lon is not None
    return GpsFix(round(lat, 7), round(lon, 7), None, "xmp", None)


def extract_gps(data: bytes, max_pixels: int) -> GpsFix | None:
    """Return the embedded GPS fix, if any. Reads headers only (no pixel decode)."""
    try:
        img = open_image(data, max_pixels)
    except ImageError:
        return None
    return _exif_gps(img) or _xmp_gps(data)

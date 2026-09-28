import io

import pytest
from PIL import Image

from geoinstant.imageio import ImageError, decode, extract_gps

from .conftest import gps_exif, jpeg_bytes, synthetic_photo

MAX_PIXELS = 60_000_000


def test_exif_gps_is_extracted_with_hemisphere_signs() -> None:
    data = jpeg_bytes(synthetic_photo(1), gps_exif(-33.9249, 18.4241))
    fix = extract_gps(data, MAX_PIXELS)
    assert fix is not None
    assert fix.source == "exif"
    assert fix.latitude == pytest.approx(-33.9249, abs=1e-4)
    assert fix.longitude == pytest.approx(18.4241, abs=1e-4)


def test_western_hemisphere() -> None:
    fix = extract_gps(jpeg_bytes(synthetic_photo(2), gps_exif(40.7128, -74.0060)), MAX_PIXELS)
    assert fix is not None and fix.longitude == pytest.approx(-74.0060, abs=1e-4)


def test_null_island_is_ignored() -> None:
    assert extract_gps(jpeg_bytes(synthetic_photo(3), gps_exif(0.0, 0.0)), MAX_PIXELS) is None


def test_no_metadata() -> None:
    assert extract_gps(jpeg_bytes(synthetic_photo(4)), MAX_PIXELS) is None


def test_xmp_gps() -> None:
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        b'exif:GPSLatitude="37,46.494N" exif:GPSLongitude="122,25.164W"/></rdf:RDF></x:xmpmeta>'
    )
    buf = io.BytesIO()
    synthetic_photo(5).save(buf, "JPEG", xmp=xmp)
    fix = extract_gps(buf.getvalue(), MAX_PIXELS)
    assert fix is not None and fix.source == "xmp"
    assert fix.latitude == pytest.approx(37.7749, abs=1e-3)
    assert fix.longitude == pytest.approx(-122.4194, abs=1e-3)


def test_decode_downscales_and_hashes() -> None:
    data = jpeg_bytes(synthetic_photo(6, (3000, 2000)))
    d = decode(data, 1024, MAX_PIXELS)
    assert max(d.image.size) <= 1024
    assert d.original_size == (3000, 2000)
    assert len(d.sha256) == 64


def test_garbage_is_rejected_with_415() -> None:
    with pytest.raises(ImageError) as e:
        decode(b"not an image at all", 1024, MAX_PIXELS)
    assert e.value.status == 415


def test_decompression_bomb_guard() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (4000, 4000)).save(buf, "PNG")
    with pytest.raises(ImageError) as e:
        decode(buf.getvalue(), 1024, max_pixels=1_000_000)
    assert e.value.status == 413

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from geoinstant.config import Settings
from geoinstant.main import create_app

from .conftest import gps_exif, jpeg_bytes, synthetic_photo


def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_locate_raw_body(settings: Settings) -> None:
    with client(settings) as c:
        r = c.post("/v1/locate", content=jpeg_bytes(synthetic_photo(3)), headers={"Content-Type": "image/jpeg"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stage"] == "final"
        assert -90 <= body["latitude"] <= 90
        assert 0 <= body["confidence"] <= 100
        assert body["place"]["display_name"]


def test_locate_multipart(settings: Settings) -> None:
    with client(settings) as c:
        data = jpeg_bytes(synthetic_photo(4), gps_exif(35.6762, 139.6503))
        r = c.post("/v1/locate", files={"image": ("p.jpg", data, "image/jpeg")})
        assert r.status_code == 200
        assert r.json()["place"]["country_code"] == "JP"


def test_stream_emits_ordered_events(settings: Settings) -> None:
    with client(settings) as c:
        r = c.post("/v1/locate/stream", content=jpeg_bytes(synthetic_photo(5)), headers={"Content-Type": "image/jpeg"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
        types = [e["type"] for e in events]
        assert types[0] == "stage" and types[-1] == "done"
        assert "result" in types
        assert types.index("result") < types.index("done")


def test_rejects_bad_images_and_oversize(settings: Settings) -> None:
    small = settings.model_copy(update={"max_upload_bytes": 1000})
    with client(small) as c:
        assert c.post("/v1/locate", content=b"x" * 5000, headers={"Content-Type": "image/jpeg"}).status_code == 413
    with client(settings) as c:
        assert c.post("/v1/locate", content=b"nope", headers={"Content-Type": "image/jpeg"}).status_code == 415
        assert c.post("/v1/locate", content=b"", headers={"Content-Type": "image/jpeg"}).status_code == 400


def test_rate_limit(settings: Settings) -> None:
    tight = settings.model_copy(update={"rate_limit_per_minute": 1, "rate_limit_burst": 2})
    with client(tight) as c:
        data = jpeg_bytes(synthetic_photo(6))
        codes = [c.post("/v1/locate", content=data, headers={"Content-Type": "image/jpeg"}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_api_key(settings: Settings) -> None:
    keyed = settings.model_copy(update={"api_keys": ["secret-key-123"]})
    with client(keyed) as c:
        data = jpeg_bytes(synthetic_photo(7))
        assert c.post("/v1/locate", content=data, headers={"Content-Type": "image/jpeg"}).status_code == 401
        ok = c.post("/v1/locate", content=data, headers={"Content-Type": "image/jpeg", "X-API-Key": "secret-key-123"})
        assert ok.status_code == 200


def test_feedback_keeps_embedding_but_no_pixels_without_consent(settings: Settings) -> None:
    with client(settings) as c:
        rid = c.post("/v1/locate", content=jpeg_bytes(synthetic_photo(8)), headers={"Content-Type": "image/jpeg"}).json()[
            "request_id"
        ]
        img_b64 = base64.b64encode(jpeg_bytes(synthetic_photo(8))).decode()
        bad = c.post("/v1/feedback", json={"request_id": rid, "latitude": 1, "longitude": 2, "image_base64": img_b64})
        assert bad.status_code == 400
        r = c.post("/v1/feedback", json={"request_id": rid, "latitude": 59.91, "longitude": 10.75, "was_correct": False})
        assert r.status_code == 200
        assert r.json() == {**r.json(), "accepted": True, "stored_image": False, "stored_embedding": True}
        files = list(Path(settings.feedback_dir).rglob("*"))
        assert not [f for f in files if f.suffix == ".jpg"]
        record = json.loads(next(f for f in files if f.name == "feedback.jsonl").read_text().splitlines()[0])
        assert record["latitude"] == 59.91 and record["embedding"] and record["image"] is None

        r2 = c.post(
            "/v1/feedback",
            json={"request_id": rid, "latitude": 59.91, "longitude": 10.75, "consent_store_image": True, "image_base64": img_b64},
        )
        assert r2.json()["stored_image"] is True


def test_healthz(settings: Settings) -> None:
    with client(settings) as c:
        h = c.get("/healthz").json()
        assert h["status"] == "ok" and h["mode"] == "dev" and h["index_rows"] == 44


def test_reverse(settings: Settings) -> None:
    with client(settings) as c:
        r = c.get("/v1/reverse", params={"lat": 59.91, "lon": 10.75})
        assert r.status_code == 200 and r.json()["country_code"] == "NO"
        assert c.get("/v1/reverse", params={"lat": 123, "lon": 0}).status_code == 422


def test_proxy_key_limits_per_end_user(settings: Settings) -> None:
    proxied = settings.model_copy(update={"proxy_api_keys": ["proxy-key-abc"], "rate_limit_per_minute": 1, "rate_limit_burst": 1})
    with client(proxied) as c:
        h = {"X-API-Key": "proxy-key-abc"}
        assert c.get("/v1/reverse", params={"lat": 1, "lon": 1}, headers={**h, "X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert c.get("/v1/reverse", params={"lat": 1, "lon": 1}, headers={**h, "X-Forwarded-For": "1.1.1.1"}).status_code == 429
        # A different end user behind the same proxy has their own bucket.
        assert c.get("/v1/reverse", params={"lat": 1, "lon": 1}, headers={**h, "X-Forwarded-For": "2.2.2.2"}).status_code == 200
        assert c.get("/v1/reverse", params={"lat": 1, "lon": 1}).status_code == 401


def test_skyline_endpoint(settings: Settings, tmp_path: Path) -> None:
    from PIL import Image

    from geoinstant.skyline.horizon import panorama

    from .test_skyline import DEM, TRUE, photo_profile, write_tile

    write_tile(settings.artifacts_dir / "dem")
    s = settings.model_copy(update={"skyline_max_km": 25.0})
    prof = photo_profile(panorama(DEM, *TRUE, max_km=25), az=130.0, fov=46.0)
    trace = [[float(x), float(y)] for x, y in zip(prof.x[::16], prof.y[::16], strict=True)]
    buf = __import__("io").BytesIO()
    Image.new("RGB", (600, 400), (120, 140, 160)).save(buf, "JPEG")
    with client(s) as c:
        assert c.get("/v1/skyline/coverage").json()["on_demand"] is True
        r = c.post(
            "/v1/skyline",
            files={"image": ("m.jpg", buf.getvalue(), "image/jpeg")},
            data={"trace": json.dumps(trace), "bbox": json.dumps([46.70, 7.60, 46.86, 7.82])},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok" and body["traced"] is True
        best = body["candidates"][0]
        assert abs(best["latitude"] - TRUE[0]) < 0.01 and abs(best["longitude"] - TRUE[1]) < 0.015
        bad = c.post("/v1/skyline", files={"image": ("m.jpg", buf.getvalue(), "image/jpeg")}, data={"bbox": "[1,2]"})
        assert bad.status_code == 422


def test_investigate_streams_steps_and_report(settings: Settings) -> None:
    from geoinstant.models.investigator import Investigator

    from .test_investigator import REPORT, FakeClient, block

    app = create_app(settings)
    with TestClient(app) as c:
        client = FakeClient(
            [[block("text", text="Looking at the sign."), block("tool_use", id="t", name="report_location", input=REPORT)]]
        )
        app.state.engine.investigator = Investigator(client, "claude-opus-5", "medium", "https://example.invalid", "test")
        r = c.post(
            "/v1/investigate",
            files={"image": ("p.jpg", jpeg_bytes(synthetic_photo(9)), "image/jpeg")},
            data={"context": "Greece"},
        )
        assert r.status_code == 200
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
        assert events[0]["type"] == "step" and events[-1]["type"] == "report"
        assert events[-1]["investigation"]["report"]["place_name"].startswith("Taverna Nikos")


def test_nearby_without_token_gives_links(settings: Settings) -> None:
    with client(settings) as c:
        r = c.get("/v1/nearby", params={"lat": 36.46, "lon": 25.37, "heading": 90}).json()
        assert r["images"] == [] and "heading=90" in r["street_view_url"] and "mapillary" in r["mapillary_url"]


def test_single_proxy_key_is_accepted() -> None:
    from geoinstant.config import Settings

    s = Settings(proxy_api_key="k1", proxy_api_keys=["k0"])
    assert s.proxy_api_keys == ["k0", "k1"]

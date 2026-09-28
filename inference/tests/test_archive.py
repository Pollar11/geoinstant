import time
from pathlib import Path

from fastapi.testclient import TestClient

from geoinstant.archive.service import resolve
from geoinstant.archive.store import Row
from geoinstant.config import Settings
from geoinstant.main import create_app

from .conftest import gps_exif, jpeg_bytes, synthetic_photo

TOKEN = {"X-Archive-Token": "t0ken-for-tests"}


def row(pid: str, group: str | None = None, result: dict | None = None, user: tuple[float, float] | None = None) -> Row:
    return Row(
        pid,
        f"{pid}.jpg",
        "",
        "done",
        None,
        None,
        result,
        group,
        user[0] if user else None,
        user[1] if user else None,
        None,
        None,
        1,
        1,
    )


def res(lat: float, lon: float, resolution: str, conf: float) -> dict:
    return {"latitude": lat, "longitude": lon, "resolution": resolution, "confidence": conf, "place": {"display_name": "X"}}


def test_group_inheritance_rules() -> None:
    rows = [
        row("a", "g", res(46.0, 7.0, "street", 60)),
        row("b", "g", res(10.0, 10.0, "continent", 20)),
        row("c", "g"),
        row("d", None, res(1.0, 1.0, "country", 40)),
    ]
    locs = resolve(rows)
    assert locs["a"].source == "photo"
    assert locs["b"].source == "group" and locs["b"].via == "a" and locs["b"].latitude == 46.0
    assert locs["c"].source == "group"
    assert locs["d"] is None  # country-level is a lead, not a location
    # your pin beats everything, for the whole group
    rows[2] = row("c", "g", user=(40.0, -3.0))
    locs = resolve(rows)
    assert locs["c"].source == "you"
    assert locs["a"].source == "group" and locs["a"].latitude == 40.0


def settings_for(settings: Settings, tmp: Path) -> Settings:
    return settings.model_copy(update={"archive_token": TOKEN["X-Archive-Token"], "archive_dir": tmp / "archive"})


def wait_done(c: TestClient, n: int) -> list[dict]:
    for _ in range(200):
        photos = c.get("/v1/archive/photos", headers=TOKEN).json()
        if len(photos) == n and all(p["status"] in ("done", "error") for p in photos):
            return photos
        time.sleep(0.05)
    raise AssertionError("archive did not finish")


def test_album_flow(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(settings, tmp_path))) as c:
        assert c.get("/v1/archive/photos").status_code == 401
        files = [
            ("files", ("paris.jpg", jpeg_bytes(synthetic_photo(501), gps_exif(48.8566, 2.3522)), "image/jpeg")),
            ("files", ("kitchen.jpg", jpeg_bytes(synthetic_photo(502)), "image/jpeg")),
            ("files", ("beach.jpg", jpeg_bytes(synthetic_photo(503)), "image/jpeg")),
            ("files", ("broken.jpg", b"nope", "image/jpeg")),
        ]
        up = c.post("/v1/archive/photos", files=files, headers=TOKEN).json()
        assert len(up["added"]) == 3 and len(up["skipped"]) == 1
        # GPS read by the browser, sent alongside a metadata-free JPEG.
        c.post(
            "/v1/archive/photos",
            files=[("files", ("rome.jpg", jpeg_bytes(synthetic_photo(504)), "image/jpeg"))],
            data={"gps": '[{"lat": 41.9028, "lon": 12.4964, "taken": "1975-07-01T10:00:00"}]'},
            headers=TOKEN,
        )
        photos = {p["filename"]: p for p in wait_done(c, 4)}
        assert photos["rome.jpg"]["location"]["resolution"] == "exact"
        paris, kitchen, beach = photos["paris.jpg"], photos["kitchen.jpg"], photos["beach.jpg"]
        assert paris["location"]["source"] == "photo" and paris["location"]["resolution"] == "exact"
        assert kitchen["location"] is None or kitchen["location"]["resolution"] != "exact"

        # Same trip: the kitchen photo inherits Paris.
        g = c.post(
            "/v1/archive/groups", json={"name": "Paris trip", "photo_ids": [paris["id"], kitchen["id"]]}, headers=TOKEN
        ).json()
        assert set(g["photo_ids"]) == {paris["id"], kitchen["id"]}
        k = c.get(f"/v1/archive/photos/{kitchen['id']}", headers=TOKEN).json()
        assert k["location"]["source"] == "group" and k["group_name"] == "Paris trip"
        assert abs(k["location"]["latitude"] - 48.8566) < 1e-3

        # Your own pin + note.
        b = c.patch(
            f"/v1/archive/photos/{beach['id']}",
            json={"user_lat": 43.55, "user_lon": 7.02, "user_label": "Cannes", "note": "Grandma 1972"},
            headers=TOKEN,
        ).json()
        assert b["location"]["source"] == "you" and b["note"] == "Grandma 1972"
        b = c.patch(f"/v1/archive/photos/{beach['id']}", json={"clear_location": True}, headers=TOKEN).json()
        assert b["location"] is None or b["location"]["source"] != "you"

        img = c.get(f"/v1/archive/photos/{beach['id']}/image?size=thumb", headers=TOKEN)
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

        assert c.delete(f"/v1/archive/groups/{g['id']}", headers=TOKEN).status_code == 204
        assert c.get(f"/v1/archive/photos/{kitchen['id']}", headers=TOKEN).json()["group_id"] is None
        assert c.delete(f"/v1/archive/photos/{beach['id']}", headers=TOKEN).status_code == 204
        assert len(c.get("/v1/archive/photos", headers=TOKEN).json()) == 3


def test_archive_disabled_without_token(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        assert c.get("/v1/archive/photos", headers=TOKEN).status_code == 404


def test_coarse_answers_are_leads_not_locations() -> None:
    from geoinstant.archive.service import lead_of, own_location

    r = row("x", None, res(37.0, 25.0, "region", 70))
    assert own_location(r) is None
    assert lead_of(r) == "X · region"
    assert resolve([r, row("y", "g"), row("z", "g", res(1.0, 1.0, "city", 90))])["y"] is None  # no sharing of coarse guesses

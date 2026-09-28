import io
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from geoinstant.config import Settings
from geoinstant.main import create_app
from geoinstant.models.embedder import HashEmbedder
from geoinstant.streetmatch import mapillary
from geoinstant.streetmatch.mapillary import MapillaryClient
from geoinstant.streetmatch.service import StreetMatchService

from .conftest import jpeg_bytes
from .test_archive import TOKEN, settings_for

BBOX = (48.850, 2.340, 48.860, 2.352)  # ~1 km²


def street(seed: int, size: tuple[int, int] = (1024, 768)) -> Image.Image:
    """A made-up facade: windows, doors and signs at random places - lots of corners, like a real street."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", size, tuple(int(v) for v in rng.integers(90, 200, 3)))
    d = ImageDraw.Draw(img)
    for _ in range(160):
        x, y = int(rng.integers(0, size[0] - 40)), int(rng.integers(0, size[1] - 40))
        w, h = int(rng.integers(10, 70)), int(rng.integers(10, 90))
        d.rectangle((x, y, x + w, y + h), fill=tuple(int(v) for v in rng.integers(0, 255, 3)), outline=(0, 0, 0))
    return img


def old_print(img: Image.Image) -> Image.Image:
    """The same view as a faded, tilted black-and-white family photo."""
    w, h = img.size
    warped = img.transform(
        (w, h), Image.Transform.PERSPECTIVE, (1.06, 0.04, -40, 0.02, 1.05, -30, 0.00004, 0.00002, 1.0), Image.Resampling.BILINEAR
    )
    g = ImageOps.autocontrast(warped.convert("L"), cutoff=5).point(lambda v: 60 + v * 0.6)
    return g.filter(ImageFilter.GaussianBlur(0.8)).crop((60, 50, w - 80, h - 60)).convert("RGB")


# 30 street photos; #7 and #8 are two frames of the same spot, 10 m apart.
SCENES = {f"img{i}": street(1000 + i) for i in range(30)}
SCENES["img8"] = SCENES["img7"].crop((30, 10, 1024, 768)).resize((1024, 768))
POS = {f"img{i}": (48.851 + i * 0.0003, 2.341 + i * 0.0003) for i in range(30)}
POS["img8"] = (POS["img7"][0] + 0.00009, POS["img7"][1])


class FakeMapillary:
    def __init__(self) -> None:
        self.graph_calls = 0
        self.fetched: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "graph.mapillary.com":
            self.graph_calls += 1
            w, s, e, n = map(float, req.url.params["bbox"].split(","))
            data = [
                {
                    "id": k,
                    "computed_geometry": {"coordinates": [lon, lat]},
                    "compass_angle": 90,
                    "captured_at": 1_700_000_000_000,
                    "thumb_256_url": f"https://img.test/{k}/256",
                    "thumb_1024_url": f"https://img.test/{k}/1024",
                }
                for k, (lat, lon) in POS.items()
                if s <= lat < n and w <= lon < e
            ]
            return httpx.Response(200, json={"data": data})
        k, size = req.url.path.strip("/").split("/")
        self.fetched.append(req.url.path)
        img = SCENES[k] if size == "1024" else SCENES[k].resize((256, 192))
        return httpx.Response(200, content=jpeg_bytes(img))


def service(tmp_path: Path, fake: FakeMapillary) -> StreetMatchService:
    client = MapillaryClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    return StreetMatchService(tmp_path / "sm", HashEmbedder(), client)


async def test_finds_the_exact_street_photo(tmp_path: Path) -> None:
    fake = FakeMapillary()
    svc = service(tmp_path, fake)
    r = await svc.search(old_print(SCENES["img7"]), BBOX)
    assert r.searched == 30
    assert r.verified, r.candidates
    assert r.best and r.best.image_id in ("img7", "img8")  # the neighbouring frame doesn't block it
    assert r.best.captured_at == "2023-11-14" and r.best.heading == 90

    # Second search of the same area only re-lists; thumbnails come from the cache.
    thumbs = sum(p.endswith("/256") for p in fake.fetched)
    await svc.search(old_print(SCENES["img3"]), BBOX)
    assert sum(p.endswith("/256") for p in fake.fetched) == thumbs


async def test_unknown_place_is_not_verified(tmp_path: Path) -> None:
    r = await service(tmp_path, FakeMapillary()).search(old_print(street(9999)), BBOX)
    assert not r.verified and r.best and r.best.inliers < 15


async def test_limits(tmp_path: Path) -> None:
    svc = service(tmp_path, FakeMapillary())
    with pytest.raises(ValueError, match="Zoom in"):
        await svc.search(street(1), (48.0, 2.0, 48.2, 2.3))
    with pytest.raises(ValueError, match="No street-level photos"):
        await svc.search(street(1), (10.0, 10.0, 10.01, 10.01))


async def test_full_tiles_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mapillary, "LIMIT", 5)
    fake = FakeMapillary()
    client = MapillaryClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    imgs = await client.list_images((48.850, 2.340, 48.854, 2.344))
    assert {i.id for i in imgs} == {k for k, (lat, lon) in POS.items() if lat < 48.854 and lon < 2.344}
    assert fake.graph_calls > 1


def test_album_street_match_pins_the_photo(settings: Settings, tmp_path: Path) -> None:
    s = settings_for(settings, tmp_path).model_copy(update={"mapillary_token": "t"})
    with TestClient(create_app(s)) as c:
        fake = FakeMapillary()
        c.app.state.streetmatch.client = MapillaryClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))  # type: ignore[attr-defined]
        buf = io.BytesIO()
        old_print(SCENES["img12"]).save(buf, "JPEG", quality=90)
        pid = c.post(
            "/v1/archive/photos", files=[("files", ("grandpa.jpg", buf.getvalue(), "image/jpeg"))], headers=TOKEN
        ).json()["added"][0]

        assert c.post(f"/v1/archive/photos/{pid}/streetmatch", json={"bbox": [48, 2, 49, 3]}, headers=TOKEN).status_code == 422
        job = c.post(f"/v1/archive/photos/{pid}/streetmatch", json={"bbox": list(BBOX)}, headers=TOKEN).json()
        for _ in range(400):
            job = c.get(f"/v1/archive/streetmatch/{job['id']}", headers=TOKEN).json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert job["status"] == "done" and job["result"]["verified"], job

        d = c.get(f"/v1/archive/photos/{pid}", headers=TOKEN).json()
        assert d["streetmatch"]["best"]["image_id"] == "img12"
        assert d["location"]["resolution"] == "exact" and d["location"]["label"].startswith("Matched street photo")
        assert abs(d["location"]["latitude"] - POS["img12"][0]) < 1e-6

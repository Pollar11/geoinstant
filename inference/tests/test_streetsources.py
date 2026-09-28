from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import httpx
import numpy as np
from PIL import Image

from geoinstant.models.embedder import HashEmbedder
from geoinstant.streetmatch.address import facing_building
from geoinstant.streetmatch.mapillary import MapillaryClient
from geoinstant.streetmatch.panoramax import PanoramaxClient
from geoinstant.streetmatch.service import StreetMatchService
from geoinstant.streetmatch.sources import MultiSource
from geoinstant.streetmatch.verify import LightGlueMatcher

from .conftest import jpeg_bytes
from .test_streetmatch import BBOX, POS, SCENES, FakeMapillary, old_print

OVERPASS = "https://overpass.test/api/interpreter"


def fake_panoramax(req: httpx.Request) -> httpx.Response:
    if req.url.host == "pnx.test":
        k, size = req.url.path.strip("/").split("/")
        img = SCENES[k] if size == "sd" else SCENES[k].resize((256, 192))
        return httpx.Response(200, content=jpeg_bytes(img))
    page = int(req.url.params.get("page", "1"))
    keys = sorted(POS)[(page - 1) * 20 : page * 20]
    feats = [
        {
            "id": k,
            "geometry": {"type": "Point", "coordinates": [POS[k][1], POS[k][0]]},
            "properties": {"datetime": "2024-05-02T10:00:00Z", "view:azimuth": 180},
            "assets": {"thumb": {"href": f"https://pnx.test/{k}/thumb"}, "sd": {"href": f"https://pnx.test/{k}/sd"}},
        }
        for k in keys
    ]
    links = [{"rel": "next", "href": "https://api.test/api/search?page=2"}] if page == 1 else []
    return httpx.Response(200, json={"type": "FeatureCollection", "features": feats, "links": links})


def fake_overpass(req: httpx.Request) -> httpx.Response:
    lat, lon = POS["img12"]
    return httpx.Response(
        200,
        json={
            "elements": [
                # Right behind the camera (it faces south, 180°): closer, but not in the photo.
                {"type": "node", "lat": lat + 0.0001, "lon": lon, "tags": {"addr:housenumber": "3", "addr:street": "Rue Behind"}},
                {
                    "type": "way",
                    "center": {"lat": lat - 0.0002, "lon": lon},
                    "tags": {
                        "addr:housenumber": "14",
                        "addr:street": "Rue Soufflot",
                        "addr:postcode": "75005",
                        "addr:city": "Paris",
                    },
                },
            ]
        },
    )


def pnx() -> PanoramaxClient:
    return PanoramaxClient("https://api.test/api", http=httpx.AsyncClient(transport=httpx.MockTransport(fake_panoramax)))


async def test_panoramax_listing_follows_pages() -> None:
    imgs = await pnx().list_images(BBOX)
    assert len(imgs) == 30 and all(i.id.startswith("pnx:") for i in imgs)
    assert imgs[0].source == "Panoramax" and imgs[0].heading == 180 and imgs[0].captured_at == "2024-05-02"


async def test_panoramax_alone_finds_the_house_and_its_address(tmp_path: Path) -> None:
    svc = StreetMatchService(tmp_path / "sm", HashEmbedder(), MultiSource([pnx()]), overpass_url=OVERPASS)
    svc._http = httpx.AsyncClient(transport=httpx.MockTransport(fake_overpass))
    r = await svc.search(old_print(SCENES["img12"]), BBOX)
    assert r.verified and r.best and r.best.image_id == "pnx:img12" and r.best.source == "Panoramax"
    assert r.building and r.building.in_view
    assert r.building.address == "14 Rue Soufflot, 75005 Paris"


async def test_sources_combine_and_one_failing_is_ok(tmp_path: Path) -> None:
    def down(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    broken = MapillaryClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    mly = MapillaryClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(FakeMapillary())))
    assert len(await MultiSource([broken, pnx()]).list_images(BBOX)) == 30
    both = MultiSource([mly, pnx()])
    imgs = await both.list_images(BBOX)
    assert len(imgs) == 60
    assert await both.fetch(next(i.full_url for i in imgs if i.source == "Panoramax"))  # routed to Panoramax


async def test_address_falls_back_to_nearest_when_nothing_in_view() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake_overpass))
    lat, lon = POS["img12"]
    b = await facing_building(http, OVERPASS, lat, lon, heading=90)  # facing east: neither is in view
    assert b and not b.in_view and b.address.startswith("3 Rue Behind")


class FakeLightGlue:
    """Stands in for the ONNX session: returns matches that follow one homography, plus noise."""

    def __init__(self, same_place: bool) -> None:
        self.same = same_place

    def get_inputs(self) -> list[NS]:
        return [NS(name="images", shape=[2, 1, 480, 640])]

    def run(self, _: object, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert feeds["images"].shape == (2, 1, 480, 640)
        rng = np.random.default_rng(0)
        a = rng.uniform(0, 600, (200, 2)).astype(np.float32)
        H = np.array([[1.05, 0.02, 12], [0.01, 0.98, -7], [1e-5, 2e-5, 1]], dtype=np.float32)
        b = cv2.perspectiveTransform(a[None], H)[0] if self.same else rng.uniform(0, 600, (200, 2)).astype(np.float32)
        idx = np.arange(200)
        matches = np.stack([np.zeros(200, int), idx, idx], axis=1)
        return [np.stack([a, b]), matches, np.full(200, 0.9, np.float32)]


def test_lightglue_matcher() -> None:
    img = Image.new("RGB", (800, 600))
    for same, expect_high in ((True, True), (False, False)):
        m = LightGlueMatcher(Path("unused"), [], session=FakeLightGlue(same))
        n = m.inliers(m.prepare(img), m.prepare(img))
        assert (n >= 150) if expect_high else (n < 60), n


def test_verified_match_labels_the_house() -> None:
    from geoinstant.archive.service import own_location

    from .test_archive import row

    r = row("p")
    best = {"latitude": 48.85, "longitude": 2.34, "captured_at": "2024-05-02"}
    r.streetmatch = {"verified": True, "best": best, "building": {"address": "14 Rue Soufflot", "in_view": True}}
    loc = own_location(r)
    assert loc and loc.resolution == "exact" and loc.label == "Facing 14 Rue Soufflot"
    r.streetmatch = {"verified": False, "best": best}
    assert own_location(r) is None

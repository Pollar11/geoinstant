from typing import Any

import httpx
import pytest
from PIL import Image

from geoinstant.models import research as research_mod
from geoinstant.models.investigator import Investigation, Investigator, Step
from geoinstant.models.research import Research, overpass_query
from geoinstant.nearby import Nearby, NearbyImage

from .conftest import jpeg_bytes, synthetic_photo
from .test_investigator import REPORT, FakeClient, block

SLIDES = {
    "elements": [
        {
            "type": "way",
            "center": {"lat": 28.2951, "lon": -81.6243},
            "tags": {"attraction": "water_slide", "name": "Resort A slide"},
        },
        {"type": "node", "lat": 28.3312, "lon": -81.5401, "tags": {"leisure": "water_park", "name": "Resort B water park"}},
    ]
}


def handler(req: httpx.Request) -> httpx.Response:
    if req.url.host == "overpass.test":
        q = req.content.decode()
        assert "attraction" in q and "water_slide" in q
        return httpx.Response(200, json=SLIDES)
    return httpx.Response(200, content=jpeg_bytes(synthetic_photo(4)), headers={"content-type": "image/jpeg"})


async def public(url: str) -> None:
    return None


def research(**kw: Any) -> Research:
    args = {
        "overpass_url": "https://overpass.test/api/interpreter",
        "mapillary_token": "t",
        "satellite_template": "https://sat.test/{lon},{lat},{zoom}.jpg",
    } | kw
    return Research(**args, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolve=public)


def test_query_and_input_checks() -> None:
    q = overpass_query((28.0, -81.8, 28.5, -81.3), ["attraction=water_slide", "leisure=water_park"], None)
    assert 'nwr["attraction"="water_slide"](28.00000,-81.80000,28.50000,-81.30000);' in q
    assert 'nwr["leisure"="water_park"]' in q and q.endswith("out center tags 60;")


async def test_find_places_rejects_bad_input() -> None:
    rs = research()
    with pytest.raises(ValueError, match="tags"):
        await rs.find_places((28.0, -81.8, 28.5, -81.3), ['x"];out;'])
    with pytest.raises(ValueError, match="too large"):
        await rs.find_places((20.0, -90.0, 35.0, -75.0), ["leisure=water_park"])
    found = await rs.find_places((28.0, -81.8, 28.5, -81.3), ["attraction=water_slide"])
    assert [f["name"] for f in found] == ["Resort A slide", "Resort B water park"]


def test_tools_offered_only_when_configured() -> None:
    assert {t["name"] for t in research().tools()} == {"find_places", "street_photos", "view_satellite"}
    assert {t["name"] for t in research(mapillary_token="", satellite_template="").tools()} == {"find_places"}


async def test_candidate_research_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_nearby(lat: float, lon: float, radius_m: float, token: str, heading: float | None = None) -> Nearby:
        ims = [
            NearbyImage(
                id=str(i),
                thumb_url=f"https://img.test/{i}.jpg",
                captured_at="2024-05-01",
                latitude=lat,
                longitude=lon,
                compass_angle=h,
                distance_m=10.0 + i,
            )
            for i, h in enumerate([0, 10, 120, 240, 250])
        ]
        return Nearby(images=ims, street_view_url="", mapillary_url="", satellite_url="")

    monkeypatch.setattr(research_mod, "nearby", fake_nearby)
    client = FakeClient(
        [
            [
                block(
                    "tool_use",
                    id="f",
                    name="find_places",
                    input={"area": "Kissimmee, Florida", "tags": ["attraction=water_slide"]},
                )
            ],
            [block("tool_use", id="s", name="view_satellite", input={"latitude": 28.2951, "longitude": -81.6243, "zoom": 18})],
            [block("tool_use", id="p", name="street_photos", input={"latitude": 28.2951, "longitude": -81.6243})],
            [block("tool_use", id="r", name="report_location", input=REPORT)],
        ]
    )
    inv = Investigator(client, "claude-opus-5", "medium", "https://example.invalid", "test", research=research())

    async def fake_nominatim(path: str, params: dict[str, Any]) -> Any:
        return [{"boundingbox": ["28.1", "28.4", "-81.7", "-81.3"]}]

    inv._nominatim = fake_nominatim  # type: ignore[method-assign]
    events = [ev async for ev in inv.run(Image.new("RGB", (800, 600)))]
    steps = [e for e in events if isinstance(e, Step)]
    assert [s.kind for s in steps] == ["geocode", "view", "view"]
    assert "2 candidates" in steps[0].text and "Satellite view" in steps[1].text
    assert "(3)" in steps[2].text  # three photos, facing different directions
    names = {t.get("name") for t in client.calls[0]["tools"]}
    assert {"find_places", "view_satellite", "street_photos"} <= names
    listed = client.calls[1]["messages"][-1]["content"][0]["content"][0]["text"]
    assert "Resort A slide" in listed
    street = client.calls[3]["messages"][-1]["content"][0]["content"]
    assert [c["type"] for c in street] == ["text", "image"] * 3
    assert isinstance(events[-1], Investigation) and events[-1].report is not None

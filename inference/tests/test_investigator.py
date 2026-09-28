from types import SimpleNamespace as NS
from typing import TYPE_CHECKING, Any

from PIL import Image

from geoinstant.models.investigator import Investigation, Investigator, Step

if TYPE_CHECKING:
    from geoinstant.models.reverse_image import ReverseImage


def block(type_: str, **kw: Any) -> NS:
    return NS(type=type_, **kw)


class FakeClient:
    """Plays back scripted assistant turns and records what it was sent."""

    def __init__(self, turns: list[list[NS]]) -> None:
        self.turns = turns
        self.calls: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(create=self.create))

    async def create(self, **kw: Any) -> NS:
        self.calls.append({**kw, "messages": list(kw["messages"])})  # snapshot: the loop mutates the list
        content = self.turns.pop(0) if self.turns else [block("text", text="Done.")]
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop, model=kw["model"])


REPORT = {
    "latitude": 36.4618,
    "longitude": 25.3753,
    "precision": "street",
    "confidence": 0.8,
    "place_name": "Taverna Nikos, Oia, Santorini",
    "address": "Main St, Oia 847 02",
    "summary": "Sign reads Taverna Nikos; web search places it in Oia.",
    "evidence_chain": [{"clue": "Sign 'Taverna Nikos'", "conclusion": "a taverna in Oia"}],
    "people_are_main_subject": False,
}


async def run(client: FakeClient) -> list[Step | Investigation]:
    inv = Investigator(client, "claude-opus-5", "medium", "https://example.invalid", "test")

    async def fake_nominatim(path: str, params: dict[str, Any]) -> Any:
        return [{"display_name": "Taverna Nikos, Oia", "lat": "36.4618", "lon": "25.3753", "type": "restaurant"}]

    inv._nominatim = fake_nominatim  # type: ignore[method-assign]
    return [ev async for ev in inv.run(Image.new("RGB", (800, 600), (200, 200, 220)), context="Greece, 1970s")]


async def test_full_investigation() -> None:
    client = FakeClient(
        [
            [
                block("text", text="There's a sign above the door."),
                block(
                    "tool_use", id="t1", name="zoom", input={"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.2, "why": "Read the sign"}
                ),
            ],
            [
                block("server_tool_use", id="s1", name="web_search", input={"query": "Taverna Nikos Oia"}),
                block("text", text="It's a taverna in Oia."),
                block("tool_use", id="t2", name="geocode", input={"query": "Taverna Nikos, Oia"}),
            ],
            [block("tool_use", id="t3", name="report_location", input=REPORT)],
        ]
    )
    events = await run(client)
    steps = [e for e in events if isinstance(e, Step)]
    assert [s.kind for s in steps] == ["note", "zoom", "search", "note", "geocode"]
    assert steps[1].box == (0.1, 0.1, 0.3, 0.2)
    final = events[-1]
    assert isinstance(final, Investigation) and final.report is not None
    assert final.report.precision == "street" and final.report.place_name.startswith("Taverna Nikos")

    # The request carried the photo, the family's context, the tools and the web search server tool.
    first = client.calls[0]
    assert first["messages"][0]["content"][0]["type"] == "image"
    assert "Greece, 1970s" in first["messages"][0]["content"][1]["text"]
    assert {t.get("name") for t in first["tools"]} >= {"zoom", "geocode", "reverse_geocode", "report_location", "web_search"}
    assert first["fallbacks"] == "default"
    # Zoom result went back as an image; geocode as text.
    zoom_result = client.calls[1]["messages"][-1]["content"][0]
    assert zoom_result["tool_use_id"] == "t1" and zoom_result["content"][0]["type"] == "image"
    geo_result = client.calls[2]["messages"][-1]["content"][0]
    assert "36.4618" in geo_result["content"][0]["text"]


async def test_nudges_once_then_gives_up() -> None:
    client = FakeClient([[block("text", text="Hard to say.")], [block("text", text="Still unsure.")]])
    events = await run(client)
    final = events[-1]
    assert isinstance(final, Investigation) and final.report is None
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][-1]["content"].startswith("Call report_location")


async def test_api_error_is_a_step() -> None:
    class Broken(FakeClient):
        async def create(self, **kw: Any) -> NS:
            raise RuntimeError("boom")

    events = await run(Broken([]))
    assert isinstance(events[0], Step) and events[0].kind == "error"
    assert isinstance(events[-1], Investigation) and events[-1].report is None


def test_coarsened_for_people() -> None:
    from geoinstant.models.investigator import Report

    r = Report.model_validate({**REPORT, "people_are_main_subject": True}).coarsened()
    assert r.precision == "city" and r.address == "" and r.latitude == 36.5 and r.evidence_chain == []
    assert Report.model_validate(REPORT).coarsened().precision == "street"


def fake_reverse() -> "ReverseImage":
    import httpx

    from geoinstant.models.reverse_image import ReverseImage

    from .conftest import jpeg_bytes, synthetic_photo

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "vision.googleapis.com":
            assert req.url.params["key"] == "k" and "WEB_DETECTION" in req.content.decode()
            web = {
                "bestGuessLabels": [{"label": "cafe restroom"}],
                "webEntities": [{"description": "Café Luna Zürich", "score": 0.8}],
                "pagesWithMatchingImages": [{"url": "https://reviews.test/cafe-luna", "pageTitle": "Café Luna - photos"}],
                "visuallySimilarImages": [{"url": "https://img.test/luna-restroom.jpg"}],
            }
            return httpx.Response(200, json={"responses": [{"webDetection": web}]})
        return httpx.Response(200, content=jpeg_bytes(synthetic_photo(3)), headers={"content-type": "image/jpeg"})

    async def ok(url: str) -> None:
        return None

    return ReverseImage("k", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolve=ok)


async def test_interior_found_by_reverse_image_search() -> None:
    client = FakeClient(
        [
            [
                block(
                    "tool_use",
                    id="r1",
                    name="reverse_image_search",
                    input={"why": "whole photo", "people_are_main_subject": False},
                )
            ],
            [
                block(
                    "tool_use",
                    id="v1",
                    name="view_image",
                    input={"url": "https://img.test/luna-restroom.jpg", "why": "same lamp?"},
                )
            ],
            [block("tool_use", id="t3", name="report_location", input=REPORT)],
        ]
    )
    inv = Investigator(client, "claude-opus-5", "medium", "https://example.invalid", "test", reverse=fake_reverse())
    events = [ev async for ev in inv.run(Image.new("RGB", (800, 600)), people_policy="coarsen")]
    steps = [e for e in events if isinstance(e, Step)]
    assert [s.kind for s in steps] == ["image_search", "view"]
    assert "(2 leads)" in steps[0].text and "img.test" in steps[1].text
    names = {t.get("name") for t in client.calls[0]["tools"]}
    assert {"reverse_image_search", "view_image", "web_fetch", "web_search"} <= names
    found = client.calls[1]["messages"][-1]["content"][0]["content"][0]["text"]
    assert "Café Luna Zürich" in found and "https://reviews.test/cafe-luna" in found
    viewed = client.calls[2]["messages"][-1]["content"][0]["content"][0]
    assert viewed["type"] == "image"


async def test_reverse_search_respects_the_people_rule() -> None:
    call = {"why": "whole photo", "people_are_main_subject": True}
    for policy, allowed in (("coarsen", False), ("off", True)):
        client = FakeClient([[block("tool_use", id="r1", name="reverse_image_search", input=call)]])
        inv = Investigator(client, "claude-opus-5", "medium", "https://example.invalid", "test", reverse=fake_reverse())
        events = [ev async for ev in inv.run(Image.new("RGB", (400, 300)), people_policy=policy)]
        first = next(e for e in events if isinstance(e, Step))
        assert (first.kind == "image_search") is allowed


async def test_no_vision_key_no_reverse_tools() -> None:
    client = FakeClient([[block("tool_use", id="t", name="report_location", input=REPORT)]])
    await run(client)
    names = {t.get("name") for t in client.calls[0]["tools"]}
    assert "reverse_image_search" not in names and "web_fetch" in names


async def test_view_image_only_fetches_public_hosts() -> None:
    import pytest

    from geoinstant.models.reverse_image import _resolve_public

    for url in (
        "http://127.0.0.1/x.jpg",
        "http://169.254.169.254/latest",
        "http://10.0.0.5/a.png",
        "file:///etc/passwd",
        "ftp://x.test/a",
    ):
        with pytest.raises(ValueError):
            await _resolve_public(url)

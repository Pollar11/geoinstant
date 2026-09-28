from types import SimpleNamespace as NS
from typing import Any

from PIL import Image

from geoinstant.models.investigator import Investigation, Investigator, Step


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

"""The VLM stage, with the Claude client replaced by a stub (no network in tests)."""

from PIL import Image

from geoinstant.config import Settings
from geoinstant.geo import haversine_km
from geoinstant.models.vlm import VlmAnswer, VlmClue, VlmResult
from geoinstant.pipeline import Engine
from geoinstant.schemas import ResultEvent, StageEvent

from .conftest import jpeg_bytes, synthetic_photo


class StubVlm:
    name = "stub"

    def __init__(self, answer: VlmAnswer) -> None:
        self.answer = answer

    async def analyze(self, image: Image.Image) -> VlmResult:
        return VlmResult(answer=self.answer, model="stub-model")


TOKYO = VlmAnswer(
    country_code="JP",
    region="Tokyo",
    city="Tokyo",
    latitude=35.68,
    longitude=139.76,
    confidence=0.8,
    radius_km=15,
    clues=[VlmClue(clue="Kana on a shop sign", implies="Japan")],
    visible_text=["ラーメン"],
)


async def run(settings: Settings, answer: VlmAnswer, mode: str) -> list:
    engine = Engine(settings.model_copy(update={"vlm_mode": mode}))
    engine.vlm = StubVlm(answer)  # type: ignore[assignment]
    return [ev async for ev in engine.locate(jpeg_bytes(synthetic_photo(321)))]


async def test_enrich_streams_final_then_refined(settings: Settings) -> None:
    events = await run(settings, TOKYO, "enrich")
    kinds = [e.type for e in events if isinstance(e, ResultEvent)]
    assert kinds[-2:] == ["result", "refined"]
    refined = next(e.result for e in events if isinstance(e, ResultEvent) and e.type == "refined")
    assert refined.place.country_code == "JP"
    assert haversine_km(refined.latitude, refined.longitude, 35.68, 139.76) < 50
    assert {ev.source for ev in refined.evidence} >= {"vlm", "text"}  # visible_text fed the text rules
    assert any(isinstance(e, StageEvent) and e.stage == "vlm" and e.status == "done" for e in events)


async def test_blocking_mode_has_single_answer(settings: Settings) -> None:
    events = await run(settings, TOKYO, "blocking")
    results = [e for e in events if isinstance(e, ResultEvent) and e.type != "partial"]
    assert [e.type for e in results] == ["result"]
    assert results[0].result.place.country_code == "JP"


async def test_people_policy_coarsens(settings: Settings) -> None:
    portrait = TOKYO.model_copy(update={"people_are_main_subject": True})
    events = await run(settings, portrait, "blocking")
    r = next(e.result for e in events if isinstance(e, ResultEvent) and e.type == "result")
    assert r.privacy.coarsened
    assert r.uncertainty_radius_m >= 10_000
    assert r.resolution not in ("street", "exact")
    assert round(r.latitude, 1) == r.latitude

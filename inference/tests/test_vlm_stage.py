"""The VLM stage, with the Claude client replaced by a stub (no network in tests)."""

from PIL import Image

from geoinstant.config import Settings
from geoinstant.geo import haversine_km
from geoinstant.models.vlm import VlmAnswer, VlmCandidate, VlmClue, VlmResult
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
    scene="urban",
    era="1980s",
    clues=[VlmClue(category="text", clue="Kana on a shop sign", implies="Japan", strength="strong")],
    candidates=[
        VlmCandidate(
            country_code="JP", region="Tokyo", city="Tokyo", latitude=35.68, longitude=139.76, probability=0.7, radius_km=15
        ),
        VlmCandidate(
            country_code="JP", region="Osaka", city="Osaka", latitude=34.69, longitude=135.5, probability=0.15, radius_km=20
        ),
    ],
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
    assert refined.analysis is not None and refined.analysis.era == "1980s"
    assert refined.analysis.clues[0].strength == "strong" and len(refined.analysis.guesses) == 2


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


async def test_policy_override_off(settings: Settings) -> None:
    portrait = TOKYO.model_copy(update={"people_are_main_subject": True})
    engine = Engine(settings.model_copy(update={"vlm_mode": "blocking"}))
    engine.vlm = StubVlm(portrait)  # type: ignore[assignment]
    events = [ev async for ev in engine.locate(jpeg_bytes(synthetic_photo(322)), people_policy="off")]
    r = next(e.result for e in events if isinstance(e, ResultEvent) and e.type == "result")
    assert not r.privacy.coarsened

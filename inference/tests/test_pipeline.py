from geoinstant.config import Settings
from geoinstant.geo import haversine_km
from geoinstant.pipeline import Engine
from geoinstant.schemas import DoneEvent, ErrorEvent, ResultEvent, StageEvent

from .conftest import INDEXED, gps_exif, jpeg_bytes, synthetic_photo


async def collect(engine: Engine, data: bytes) -> list:
    return [ev async for ev in engine.locate(data)]


async def test_exif_short_circuits(settings: Settings) -> None:
    engine = Engine(settings)
    events = await collect(engine, jpeg_bytes(synthetic_photo(99), gps_exif(48.8566, 2.3522)))
    results = [e for e in events if isinstance(e, ResultEvent)]
    assert len(results) == 1
    r = results[0].result
    assert r.source == "exif" and r.resolution == "exact"
    assert r.place.country_code == "FR"
    assert not any(isinstance(e, StageEvent) and e.stage == "embedding" for e in events)
    assert isinstance(events[-1], DoneEvent)


async def test_retrieval_recovers_a_reencoded_indexed_photo(settings: Settings) -> None:
    """A resized, re-compressed copy of an indexed photo must come back to its coordinates."""
    engine = Engine(settings)
    name, lat, lon = INDEXED[1]  # Lisbon
    query = synthetic_photo(1).resize((500, 375))
    events = await collect(engine, jpeg_bytes(query, quality=70))
    final = next(e.result for e in events if isinstance(e, ResultEvent) and e.type == "result")
    assert final.source == "visual"
    assert final.mode == "dev"
    assert haversine_km(final.latitude, final.longitude, lat, lon) < 50, name
    assert final.place.country_code == "PT"
    assert any(ev.source == "retrieval" for ev in final.evidence)
    assert "total" in final.timings_ms


async def test_result_cache_hit(settings: Settings) -> None:
    engine = Engine(settings)
    data = jpeg_bytes(synthetic_photo(2))
    first = [e async for e in engine.locate(data)]
    second = [e async for e in engine.locate(data)]
    r1 = next(e.result for e in first if isinstance(e, ResultEvent))
    r2 = next(e.result for e in second if isinstance(e, ResultEvent))
    assert r2.cached and not r1.cached
    assert r2.request_id != r1.request_id
    assert (r2.latitude, r2.longitude) == (r1.latitude, r1.longitude)


async def test_bad_input_yields_error_event(settings: Settings) -> None:
    engine = Engine(settings)
    events = await collect(engine, b"\x00\x01garbage")
    assert isinstance(events[-1], ErrorEvent) and events[-1].status == 415

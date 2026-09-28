"""Async pipeline: metadata → decode → parallel stages → fusion, streamed as events."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Literal, TypeVar

import numpy as np
from numpy.typing import NDArray

from .cells import CellTree
from .config import Settings
from .evidence.base import Evidence, observation_evidence
from .evidence.classifier import CellClassifier
from .evidence.cues import CueKnowledgeBase
from .evidence.retrieval import VectorIndex, retrieval_evidence
from .evidence.text_cues import analyse_text
from .evidence.vlm import vlm_evidence
from .fusion import Fused, fuse
from .gazetteer import Gazetteer, load_countries
from .imageio import GpsFix, ImageError, decode, extract_gps
from .models.detector import NullDetector, load_detector
from .models.embedder import HashEmbedder, load_embedder
from .models.investigator import load_investigator
from .models.ocr import NullOcr, load_ocr
from .models.vlm import VlmResult, load_vlm
from .runtime import TtlLru, coarsen, people_are_main_subject
from .schemas import (
    Analysis,
    Clue,
    DoneEvent,
    ErrorEvent,
    Event,
    EvidenceItem,
    Guess,
    HierarchyNode,
    LocateResult,
    Place,
    Privacy,
    ResultEvent,
    StageEvent,
)

log = logging.getLogger(__name__)
R = TypeVar("R")
T = TypeVar("T")


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 1)


class Engine:
    """Loads every model/index once and serves requests concurrently."""

    def __init__(self, settings: Settings) -> None:
        s = self.settings = settings
        countries_path = s.artifact(s.countries_csv)
        countries = load_countries(countries_path) if countries_path.exists() else load_countries()
        self.gazetteer = Gazetteer(s.artifact(s.gazetteer_csv), countries)
        cells_path = s.artifact(s.cells)
        self.tree = CellTree.load(cells_path, self.gazetteer) if cells_path.exists() else CellTree.from_gazetteer(self.gazetteer)
        self.embedder = load_embedder(s.artifact(s.image_encoder), s.image_encoder_size, s.onnx_providers, s.onnx_threads)
        self.classifier = CellClassifier.load(s.artifact(s.cell_prototypes), self.tree)
        self.index = VectorIndex.load(s.artifact(s.index), s.artifact(s.faiss_index), self.tree)
        self.detector = load_detector(s.artifact(s.detector), s.artifact(s.detector_classes), s.onnx_providers)
        self.ocr = load_ocr()
        self.vlm = load_vlm(s.vlm_mode, s.vlm_model, s.vlm_effort, s.anthropic_api_key)
        self.investigator = load_investigator(
            s.investigator_mode,
            s.investigator_model,
            s.investigator_effort,
            s.anthropic_api_key,
            s.geocode_url,
            s.geocode_user_agent,
        )
        self.cues = CueKnowledgeBase(s.cue_priors, countries)
        self.results: TtlLru[LocateResult] = TtlLru(s.result_cache_size, s.result_cache_ttl_s)
        self.embeddings: TtlLru[NDArray[np.float32]] = TtlLru(s.result_cache_size, s.result_cache_ttl_s)
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="geoinstant")

    # ---- introspection ----------------------------------------------------------------------
    @property
    def models(self) -> dict[str, str]:
        return {
            "embedder": self.embedder.name,
            "classifier": f"{len(self.tree)} cells" if self.classifier else "none",
            "retrieval": f"{len(self.index)} photos" if self.index else "none",
            "detector": self.detector.name,
            "ocr": self.ocr.name,
            "vlm": self.vlm.name if self.vlm else "none",
            "gazetteer": self.gazetteer.source,
        }

    @property
    def mode(self) -> str:
        real_embedder = not isinstance(self.embedder, HashEmbedder)
        if real_embedder and self.classifier and self.index and not isinstance(self.detector, NullDetector):
            return "production"
        if real_embedder and (self.classifier or self.index):
            return "partial"
        return "dev"

    async def _run(self, fn: Callable[..., R], *args: Any) -> R:
        return await asyncio.get_running_loop().run_in_executor(self.pool, partial(fn, *args))

    # ---- the pipeline -----------------------------------------------------------------------
    async def locate(self, data: bytes, vlm_mode: str | None = None, people_policy: str | None = None) -> AsyncIterator[Event]:
        s = self.settings
        vlm_mode = vlm_mode or s.vlm_mode
        policy = people_policy or s.people_precision_policy
        t0 = time.perf_counter()
        rid = uuid.uuid4().hex
        timings: dict[str, float] = {}

        # A. Metadata GPS: the only "instant" path.
        t = time.perf_counter()
        gps = await self._run(extract_gps, data, s.max_pixels)
        timings["metadata"] = _ms(t)
        if gps is not None:
            yield StageEvent(stage="metadata", status="done", ms=timings["metadata"], detail=f"{gps.source.upper()} GPS found")
            timings["total"] = _ms(t0)
            yield ResultEvent(type="result", result=self.gps_result(rid, gps, timings))
            yield DoneEvent(request_id=rid, total_ms=_ms(t0))
            return
        yield StageEvent(stage="metadata", status="skipped", ms=timings["metadata"], detail="No GPS metadata")

        # Decode once, at working resolution.
        t = time.perf_counter()
        try:
            decoded = await self._run(decode, data, s.work_size, s.max_pixels)
        except ImageError as e:
            yield ErrorEvent(status=e.status, message=str(e))
            return
        timings["decode"] = _ms(t)
        yield StageEvent(stage="decode", status="done", ms=timings["decode"])
        img = decoded.image

        cache_key = f"{decoded.sha256}:{vlm_mode}:{policy}"
        cached = self.results.get(cache_key)
        if cached is not None:
            timings["total"] = _ms(t0)
            hit = cached.model_copy(update={"request_id": rid, "cached": True, "timings_ms": timings})
            emb_cached = self.embeddings.get(cached.request_id)
            if emb_cached is not None:
                self.embeddings.set(rid, emb_cached)
            yield ResultEvent(type="result", result=hit)
            yield DoneEvent(request_id=rid, total_ms=_ms(t0))
            return

        deadline = t0 + s.fast_deadline_ms / 1000.0

        def remaining() -> float:
            return max(0.0, deadline - time.perf_counter())

        # Launch everything that only needs pixels.
        started = time.perf_counter()
        emb_task = asyncio.ensure_future(self._run(self.embedder.embed, img))
        det_task = asyncio.ensure_future(self._run(self.detector.detect, img))
        ocr_task = asyncio.ensure_future(self._run(self.ocr.read, img))
        vlm_task: asyncio.Future[VlmResult | None] | None = None
        if self.vlm is not None and vlm_mode != "off":
            vlm_task = asyncio.ensure_future(self.vlm.analyze(img))
            yield StageEvent(stage="vlm", status="running")

        evidences: list[Evidence] = []

        # C. Embedding → classifier + retrieval.
        emb: NDArray[np.float32] | None = None
        try:
            emb = await asyncio.wait_for(asyncio.shield(emb_task), timeout=remaining())
            timings["embedding"] = _ms(started)
            yield StageEvent(stage="embedding", status="done", ms=timings["embedding"], detail=self.embedder.name)
        except TimeoutError:
            yield StageEvent(stage="embedding", status="timeout")
        if emb is not None:
            self.embeddings.set(rid, emb)
            t = time.perf_counter()
            if self.classifier:
                ev = self.classifier.evidence(emb, self.tree, s.weight_classifier)
                if ev:
                    evidences.append(ev)
            if self.index:
                ev = retrieval_evidence(self.index, self.tree, emb, s.retrieval_k, s.retrieval_temperature, s.weight_retrieval)
                if ev:
                    evidences.append(ev)
            timings["retrieval"] = _ms(t)
            yield StageEvent(
                stage="retrieval", status="done" if self.index or self.classifier else "skipped", ms=timings["retrieval"]
            )
            slow_stages_pending = not (det_task.done() and ocr_task.done()) or vlm_mode == "blocking"
            has_slow_stages = (
                not (isinstance(self.detector, NullDetector) and isinstance(self.ocr, NullOcr)) or vlm_mode == "blocking"
            )
            if evidences and slow_stages_pending and has_slow_stages:
                # Progressive result: coarse answer while detectors finish.
                fused = self._fuse(evidences)
                yield ResultEvent(type="partial", result=self._visual_result(rid, "partial", fused, dict(timings), False, policy))

        # B. Cue detector + OCR, bounded by the fast deadline.
        stage_ev, detections = await self._bounded(
            "detection", det_task, isinstance(self.detector, NullDetector), started, remaining(), timings
        )
        yield stage_ev
        stage_ev, lines = await self._bounded("ocr", ocr_task, isinstance(self.ocr, NullOcr), started, remaining(), timings)
        yield stage_ev

        for obs in self.cues.from_detections(detections):
            evidences.append(observation_evidence(obs, self.tree, "cue", s.weight_cue))
        text_obs = analyse_text(lines)
        for obs in text_obs:
            evidences.append(observation_evidence(obs, self.tree, "text", s.weight_text))
        people = people_are_main_subject(detections)

        vlm_result: VlmResult | None = None
        if vlm_task is not None and vlm_mode == "blocking":
            vlm_result = await self._await_vlm(vlm_task, t0)
            yield self._vlm_stage(vlm_result, t0, timings)
            evidences.extend(self._vlm_evidences(vlm_result, text_obs))
            people = people or bool(vlm_result and vlm_result.answer.people_are_main_subject)

        t = time.perf_counter()
        fused = self._fuse(evidences)
        timings["fusion"] = _ms(t)
        timings["total"] = _ms(t0)
        final = self._visual_result(rid, "final", fused, dict(timings), people, policy, vlm_result)
        yield ResultEvent(type="result", result=final)
        self.results.set(cache_key, final)

        if vlm_task is not None and vlm_mode == "enrich":
            vlm_result = await self._await_vlm(vlm_task, t0)
            yield self._vlm_stage(vlm_result, t0, timings)
            extra = self._vlm_evidences(vlm_result, text_obs)
            if extra:
                people = people or bool(vlm_result and vlm_result.answer.people_are_main_subject)
                fused = self._fuse(evidences + extra)
                timings["total_refined"] = _ms(t0)
                refined = self._visual_result(rid, "refined", fused, dict(timings), people, policy, vlm_result)
                self.results.set(cache_key, refined)
                yield ResultEvent(type="refined", result=refined)

        yield DoneEvent(request_id=rid, total_ms=_ms(t0))

    # ---- helpers -------------------------------------------------------------------------------
    @staticmethod
    async def _bounded(
        name: Literal["detection", "ocr"],
        task: asyncio.Future[list[T]],
        skip: bool,
        started: float,
        budget_s: float,
        timings: dict[str, float],
    ) -> tuple[StageEvent, list[T]]:
        """Await an optional stage until the deadline; a late or failing stage yields nothing."""
        if skip:
            return StageEvent(stage=name, status="skipped", detail="model not installed"), []
        try:
            out = await asyncio.wait_for(asyncio.shield(task), timeout=budget_s)
        except TimeoutError:
            return StageEvent(stage=name, status="timeout"), []
        except Exception:
            log.exception("%s failed", name)
            return StageEvent(stage=name, status="error"), []
        timings[name] = _ms(started)
        return StageEvent(stage=name, status="done", ms=timings[name], detail=f"{len(out)} found"), out

    async def _await_vlm(self, task: asyncio.Future[VlmResult | None], t0: float) -> VlmResult | None:
        budget = max(0.0, t0 + self.settings.vlm_deadline_ms / 1000.0 - time.perf_counter())
        try:
            return await asyncio.wait_for(task, timeout=budget)
        except TimeoutError:
            return None
        except Exception:
            log.exception("VLM failed")
            return None

    @staticmethod
    def _vlm_stage(result: VlmResult | None, t0: float, timings: dict[str, float]) -> StageEvent:
        if result is None:
            return StageEvent(stage="vlm", status="timeout", detail="no answer in time")
        timings["vlm"] = _ms(t0)
        return StageEvent(stage="vlm", status="done", ms=timings["vlm"], detail=result.model)

    def _vlm_evidences(self, result: VlmResult | None, ocr_obs: list[Any]) -> list[Evidence]:
        if result is None:
            return []
        out = []
        ev = vlm_evidence(result, self.tree, self.settings.weight_vlm)
        if ev:
            out.append(ev)
        # Text the VLM read but OCR missed (or OCR is not installed) → the same text rules.
        seen = {o.key for o in ocr_obs}
        for obs in analyse_text(result.answer.visible_text):
            if obs.key not in seen:
                out.append(observation_evidence(obs, self.tree, "text", self.settings.weight_text * 0.7))
        return out

    def _fuse(self, evidences: list[Evidence]) -> Fused:
        s = self.settings
        return fuse(self.tree, self.gazetteer, evidences, s.resolution_min_mass, s.calibration_temperature)

    def _visual_result(
        self,
        rid: str,
        stage: str,
        f: Fused,
        timings: dict[str, float],
        people: bool,
        policy: str,
        vlm: VlmResult | None = None,
    ) -> LocateResult:
        lat, lon, radius, resolution, place = f.latitude, f.longitude, f.radius_km, f.resolution, f.place
        privacy = Privacy()
        if people and policy == "coarsen":
            lat, lon, radius = coarsen(lat, lon, radius, self.settings.coarsen_radius_km)
            if resolution in ("street", "city"):
                resolution = "city"
            privacy = Privacy(coarsened=True, reason="People are the main subject; precision limited to city level.")
        return LocateResult(
            request_id=rid,
            stage=stage,
            source="visual",
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            uncertainty_radius_m=round(radius * 1000.0, 1),
            confidence=f.confidence,
            resolution=resolution,
            place=place,
            hierarchy=f.hierarchy,
            candidates=f.candidates,
            evidence=f.evidence,
            regions=f.regions,
            explanation=f.explanation,
            timings_ms=timings,
            mode=self.mode,
            models=self.models,
            privacy=privacy,
            analysis=_analysis(vlm),
        )

    def gps_result(self, rid: str, gps: GpsFix, timings: dict[str, float]) -> LocateResult:
        pl = self.gazetteer.reverse(gps.latitude, gps.longitude)
        place = Place(
            name=pl.name,
            admin1=pl.admin1,
            country=pl.country,
            country_code=pl.country_code,
            continent=pl.continent,
            display_name=pl.display_name,
            distance_km=round(pl.distance_km, 2),
        )
        tag = gps.source.upper()
        return LocateResult(
            request_id=rid,
            stage="final",
            source=gps.source,
            latitude=gps.latitude,
            longitude=gps.longitude,
            uncertainty_radius_m=15.0,  # typical phone GPS accuracy; EXIF rarely carries HPositioningError
            confidence=99.0,
            resolution="exact",
            place=place,
            hierarchy=[
                HierarchyNode(level="continent", name=place.continent, code="", probability=0.99),
                HierarchyNode(level="country", name=place.country, code=place.country_code, probability=0.99),
            ],
            evidence=[
                EvidenceItem(
                    source="exif",
                    label=f"{tag} GPS coordinates embedded in the file",
                    detail="Metadata can be edited; the image content was not checked against it.",
                    likelihood_ratio=1e6,
                    direction="supports",
                )
            ],
            explanation=f"The file carries {tag} GPS coordinates: {place.display_name}.",
            captured_at=gps.captured_at,
            timings_ms=timings,
            mode=self.mode,
            models=self.models,
        )


def _analysis(vlm: VlmResult | None) -> Analysis | None:
    if vlm is None:
        return None
    a = vlm.answer
    return Analysis(
        scene=a.scene,
        era=a.era,
        model=vlm.model,
        clues=[Clue(category=c.category, clue=c.clue, implies=c.implies, strength=c.strength) for c in a.clues],
        guesses=[
            Guess(
                label=c.label,
                country_code=c.country_code,
                latitude=c.latitude,
                longitude=c.longitude,
                probability=c.probability,
                radius_km=c.radius_km,
            )
            for c in a.candidates
        ],
    )

"""HTTP API: /v1/locate, /v1/locate/stream, /v1/skyline, /v1/reverse, /v1/feedback, /healthz."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser

from . import leads
from .archive.api import router as archive_router
from .archive.service import ArchiveService
from .archive.store import ArchiveStore
from .config import Settings, get_settings
from .geocode import Nominatim
from .imageio import ImageError, decode
from .models.embedder import Embedder, OnnxImageEncoder
from .models.investigator import Investigation
from .nearby import Nearby, nearby
from .pipeline import Engine
from .runtime import FeedbackStore, TokenBucket
from .schemas import (
    ErrorEvent,
    FeedbackRequest,
    FeedbackResponse,
    Health,
    LocateResult,
    Place,
    ResultEvent,
    SkylineCandidate,
    SkylineCoverage,
    SkylineResult,
)
from .skyline.extract import detect, from_trace
from .skyline.service import SkylineService
from .streetmatch.mapillary import MapillaryClient
from .streetmatch.panoramax import PanoramaxClient
from .streetmatch.service import Job, StreetMatchService
from .streetmatch.sources import MultiSource, StreetSource
from .streetmatch.verify import load_matcher

log = logging.getLogger("geoinstant")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.engine = Engine(settings)
    app.state.limiter = TokenBucket(settings.rate_limit_per_minute, settings.rate_limit_burst)
    app.state.feedback = FeedbackStore(settings.feedback_dir)
    app.state.skyline = SkylineService(
        settings.artifact(settings.skyline_dir),
        settings.artifact(settings.dem_dir),
        settings.dem_tile_url,
        settings.skyline_max_area_km2,
        settings.skyline_max_km,
    )
    app.state.streetmatch = street_service(settings, app.state.engine.embedder)
    app.state.lead_secret = os.urandom(32)  # leads are only valid for this process's lifetime
    app.state.archive = None
    if settings.archive_token:
        app.state.archive = ArchiveService(
            ArchiveStore(settings.archive_dir),
            app.state.engine,
            settings.archive_people_policy,
            settings.archive_concurrency,
            settings.archive_investigate,
            app.state.streetmatch if settings.archive_auto_search else None,
            app.state.skyline if settings.archive_auto_search else None,
            settings.auto_street_km2,
            settings.auto_skyline_km,
            Nominatim(settings.geocode_url, settings.geocode_user_agent) if settings.geocode_url else None,
        )
        app.state.archive.start()
    log.info("GeoInstant ready (mode=%s) %s", app.state.engine.mode, app.state.engine.models)
    yield
    if app.state.archive:
        await app.state.archive.stop()
    app.state.engine.pool.shutdown(wait=False, cancel_futures=True)


def street_service(settings: Settings, fallback: Embedder) -> StreetMatchService:
    vpr = settings.artifact(settings.vpr_encoder)
    embedder: Embedder = fallback
    if vpr.exists():
        try:
            # MegaLoc bakes its own normalisation into the graph (scripts/export_vpr.py).
            embedder = OnnxImageEncoder(
                vpr, settings.vpr_encoder_size, settings.onnx_providers, settings.onnx_threads, crop=False
            )
        except Exception:
            log.exception("Failed to load %s; using the main image encoder", vpr)
    sources: list[StreetSource] = []
    if settings.mapillary_token:
        sources.append(MapillaryClient(settings.mapillary_token))
    if settings.panoramax_url:
        sources.append(PanoramaxClient(settings.panoramax_url))
    return StreetMatchService(
        settings.artifact(settings.streetmatch_dir),
        embedder,
        MultiSource(sources) if sources else None,
        settings.streetmatch_max_area_km2,
        matcher=load_matcher(settings.artifact(settings.matcher), settings.onnx_providers),
        overpass_url=settings.overpass_url,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Keep multipart uploads in RAM (Starlette spools anything over 1 MB to a temp file).
    MultiPartParser.spool_max_size = settings.max_upload_bytes

    app = FastAPI(title="GeoInstant", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["POST", "GET", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key", "X-Archive-Token"],
    )

    def matches(supplied: str, keys: list[str]) -> bool:
        return bool(supplied) and any(hmac.compare_digest(supplied, k) for k in keys)

    def forwarded_ip(request: Request, hops: int) -> str | None:
        # Only the last `hops` entries were appended by proxies we run; earlier ones are client-supplied.
        fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        return fwd[-min(hops, len(fwd))] if fwd and hops > 0 else None

    def client_id(request: Request) -> str:
        key = request.headers.get("x-api-key", "")
        if matches(key, settings.proxy_api_keys):
            return f"ip:{forwarded_ip(request, 1) or 'unknown'}"
        if key:
            return f"key:{key[:12]}"
        ip = forwarded_ip(request, settings.trusted_proxy_hops)
        return f"ip:{ip or (request.client.host if request.client else 'unknown')}"

    def check(request: Request, cost: float) -> None:
        if settings.api_keys or settings.proxy_api_keys:
            supplied = request.headers.get("x-api-key", "")
            if not matches(supplied, settings.api_keys + settings.proxy_api_keys):
                raise HTTPException(401, "Missing or invalid API key")
        wait = request.app.state.limiter.take(client_id(request), cost)
        if wait > 0:
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(int(wait) + 1)})

    def guard(request: Request) -> None:
        check(request, 1.0)

    async def read_image(request: Request) -> bytes:
        declared = int(request.headers.get("content-length") or 0)
        if declared > settings.max_upload_bytes:
            raise HTTPException(413, "Image is too large")
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=4)
            f = form.get("image")
            if not isinstance(f, UploadFile):
                raise HTTPException(400, "Expected a multipart field named 'image'")
            data = await f.read()
            await f.close()
        else:
            buf = bytearray()
            async for chunk in request.stream():
                buf.extend(chunk)
                if len(buf) > settings.max_upload_bytes:
                    raise HTTPException(413, "Image is too large")
            data = bytes(buf)
        if not data:
            raise HTTPException(400, "Empty upload")
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "Image is too large")
        return data

    app.include_router(archive_router(settings))

    VlmParam = Query(None, pattern="^(off|enrich|blocking)$", description="Override the VLM mode for this request")

    @app.post("/v1/locate", response_model=LocateResult, dependencies=[Depends(guard)])
    async def locate(request: Request, vlm: str | None = VlmParam) -> LocateResult | JSONResponse:
        data = await read_image(request)
        engine: Engine = request.app.state.engine
        best: LocateResult | None = None
        async for ev in engine.locate(data, vlm):
            if isinstance(ev, ErrorEvent):
                return JSONResponse({"detail": ev.message}, status_code=ev.status)
            if isinstance(ev, ResultEvent) and ev.type in ("result", "refined"):
                best = ev.result
        if best is None:  # pragma: no cover - the pipeline always yields a result or an error
            raise HTTPException(500, "No result")
        return best

    @app.post("/v1/locate/stream", dependencies=[Depends(guard)])
    async def locate_stream(request: Request, vlm: str | None = VlmParam) -> StreamingResponse:
        data = await read_image(request)
        engine: Engine = request.app.state.engine

        async def events() -> AsyncIterator[bytes]:
            async for ev in engine.locate(data, vlm):
                yield f"event: {ev.type}\ndata: {json.dumps(ev.model_dump(mode='json'), separators=(',', ':'))}\n\n".encode()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/feedback", response_model=FeedbackResponse, dependencies=[Depends(guard)])
    async def feedback(body: FeedbackRequest, request: Request) -> FeedbackResponse:
        if body.image_base64 and not body.consent_store_image:
            raise HTTPException(400, "image_base64 is only accepted with consent_store_image=true")
        if body.image_base64 and len(body.image_base64) > settings.max_upload_bytes * 4 // 3 + 4:
            raise HTTPException(413, "Image is too large")
        engine: Engine = request.app.state.engine
        store: FeedbackStore = request.app.state.feedback
        return store.save(body, engine.embeddings.get(body.request_id), engine.embedder.name)

    @app.get("/v1/reverse", response_model=Place, dependencies=[Depends(guard)])
    async def reverse(
        request: Request,
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
    ) -> Place:
        """Name a coordinate. Lets clients that read EXIF GPS on-device skip uploading the photo."""
        return place_of(request.app.state.engine, lat, lon)

    def place_of(engine: Engine, lat: float, lon: float) -> Place:
        pl = engine.gazetteer.reverse(lat, lon)
        return Place(
            name=pl.name,
            admin1=pl.admin1,
            country=pl.country,
            country_code=pl.country_code,
            continent=pl.continent,
            display_name=pl.display_name,
            distance_km=round(pl.distance_km, 2),
        )

    def parse_floats(raw: object, n: int | None, what: str) -> list[float] | None:
        if raw is None or raw == "":
            return None
        try:
            v = json.loads(str(raw))
            flat = [float(x) for x in (v if n is None else v[:n])]
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"Invalid {what}") from e
        if n is not None and len(flat) != n:
            raise HTTPException(422, f"Invalid {what}")
        return flat

    @app.post("/v1/skyline", response_model=SkylineResult)
    async def skyline(request: Request) -> SkylineResult:
        """Match the mountain skyline. Multipart: image, optional trace=[[x,y],...], bbox=[s,w,n,e]."""
        check(request, 3.0)  # skyline searches are expensive
        form = await request.form(max_files=1, max_fields=4)
        f = form.get("image")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Expected a multipart field named 'image'")
        data = await f.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "Image is too large")
        bbox_v = parse_floats(form.get("bbox"), 4, "bbox")
        trace_raw = form.get("trace")
        trace: list[tuple[float, float]] | None = None
        if trace_raw:
            try:
                trace = [(float(p[0]), float(p[1])) for p in json.loads(str(trace_raw))]
            except (ValueError, TypeError, IndexError) as e:
                raise HTTPException(422, "Invalid trace") from e
            if len(trace) < 3:
                raise HTTPException(422, "Trace needs at least 3 points")
        engine: Engine = request.app.state.engine
        svc: SkylineService = request.app.state.skyline

        def work() -> SkylineResult:
            try:
                img = decode(data, 1024, settings.max_pixels).image
            except ImageError as e:
                raise HTTPException(e.status, str(e)) from e
            aspect = img.width / img.height
            prof = from_trace(trace, aspect) if trace else detect(img)
            bbox = (bbox_v[0], bbox_v[1], bbox_v[2], bbox_v[3]) if bbox_v else None
            r = svc.search(prof, bbox)
            step = max(1, len(prof.x) // 80)
            return SkylineResult(
                status=r.status,
                message=r.message,
                confidence=round(100 * r.confidence, 1),
                profile=[
                    (round(float(x), 4), round(float(y), 4), round(float(w), 2))
                    for x, y, w in zip(prof.x[::step], prof.y[::step], prof.w[::step], strict=True)
                ],
                traced=prof.traced,
                relief_deg=round(r.relief, 2),
                search_area=r.bbox,
                viewpoints=r.viewpoints,
                spacing_km=round(r.spacing_km, 3),
                candidates=[
                    SkylineCandidate(
                        latitude=round(c.lat, 6),
                        longitude=round(c.lon, 6),
                        elevation_m=round(c.elev, 1),
                        azimuth_deg=round(c.m.azimuth, 1),
                        fov_deg=c.m.fov,
                        fit_error=round(c.m.rmse, 4),
                        match=round(c.m.score, 3),
                        place=place_of(engine, c.lat, c.lon),
                    )
                    for c in r.candidates
                ],
                heat=r.heat,
                timings_ms=r.timings,
            )

        return await asyncio.get_running_loop().run_in_executor(engine.pool, work)

    @app.post("/v1/investigate")
    async def investigate(request: Request) -> StreamingResponse:
        """Deep investigation (Claude + zoom + web search + map lookup), streamed as SSE steps."""
        check(request, 5.0)
        engine: Engine = request.app.state.engine
        if engine.investigator is None:
            raise HTTPException(503, "Investigator is off (set ANTHROPIC_API_KEY)")
        form = await request.form(max_files=1, max_fields=3)
        f = form.get("image")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Expected a multipart field named 'image'")
        data = await f.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "Image is too large")
        try:
            img = (await asyncio.to_thread(decode, data, 2048, settings.max_pixels)).image
        except ImageError as e:
            raise HTTPException(e.status, str(e)) from e
        context = str(form.get("context") or "")[:1000]
        inv = engine.investigator

        async def events() -> AsyncIterator[bytes]:
            async for ev in inv.run(img, context):
                if isinstance(ev, Investigation):
                    secret: bytes = request.app.state.lead_secret
                    lead = leads.issue(secret, data, ev.report, settings.auto_street_km2, settings.people_precision_policy)
                    if ev.report and settings.people_precision_policy == "coarsen":
                        ev = ev.model_copy(update={"report": ev.report.coarsened()})
                    payload = {
                        "type": "report",
                        "investigation": ev.model_dump(mode="json"),
                        "lead": lead.model_dump() if lead else None,  # where the street search may run
                    }
                else:
                    payload = {"type": "step", "step": ev.model_dump(mode="json")}
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @app.post("/v1/streetmatch", response_model=Job, status_code=202)
    async def street_search(request: Request) -> Job:
        """Exact spot for one photo: street photos around the investigation's lead. Multipart: image + the signed lead."""
        # Heavy, but each search needs a lead from its own (rate-limited) investigation; locate 1 + investigate 5
        # + this must fit one visitor's burst (10).
        check(request, 2.0)
        sm: StreetMatchService = request.app.state.streetmatch
        if not sm.enabled:
            raise HTTPException(503, "No street photo source is configured")
        form = await request.form(max_files=1, max_fields=6)
        f = form.get("image")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Expected a multipart field named 'image'")
        data = await f.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "Image is too large")
        try:
            lat, lon, km2 = (float(str(form.get(k))) for k in ("latitude", "longitude", "km2"))
        except (TypeError, ValueError) as e:
            raise HTTPException(422, "Expected latitude, longitude and km2") from e
        if not leads.valid(request.app.state.lead_secret, data, lat, lon, km2, str(form.get("token") or "")):
            raise HTTPException(403, "Search the spot this photo's investigation pointed to")
        try:
            img = (await asyncio.to_thread(decode, data, 2048, settings.max_pixels)).image
        except ImageError as e:
            raise HTTPException(e.status, str(e)) from e
        return sm.start_around(img, lat, lon, km2)

    @app.get("/v1/streetmatch/{job_id}", response_model=Job)
    async def street_search_job(job_id: str, request: Request) -> Job:
        check(request, 0.1)  # polled every couple of seconds
        sm: StreetMatchService = request.app.state.streetmatch
        job = sm.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such search")
        return job

    @app.get("/v1/nearby", response_model=Nearby, dependencies=[Depends(guard)])
    async def nearby_photos(
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
        radius_m: float = Query(300, ge=20, le=2000),
        heading: float | None = Query(None, ge=0, le=360),
    ) -> Nearby:
        """Then & now: recent street-level photos around a spot."""
        return await nearby(lat, lon, radius_m, settings.mapillary_token, heading)

    @app.get("/v1/skyline/coverage", response_model=SkylineCoverage)
    async def skyline_coverage(request: Request) -> SkylineCoverage:
        svc: SkylineService = request.app.state.skyline
        return SkylineCoverage(regions=svc.coverage, on_demand=svc.dem_available, max_area_km2=svc.max_area)

    @app.get("/healthz", response_model=Health)
    async def healthz(request: Request) -> Health:
        engine: Engine = request.app.state.engine
        return Health(
            status="ok",
            mode=engine.mode,
            models=engine.models,
            cells=len(engine.tree),
            index_rows=len(engine.index) if engine.index else 0,
        )

    return app


app = create_app()

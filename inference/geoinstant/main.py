"""HTTP API: /v1/locate, /v1/locate/stream, /v1/reverse, /v1/feedback, /healthz."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser

from .config import Settings, get_settings
from .pipeline import Engine
from .runtime import FeedbackStore, TokenBucket
from .schemas import ErrorEvent, FeedbackRequest, FeedbackResponse, Health, LocateResult, Place, ResultEvent

log = logging.getLogger("geoinstant")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.engine = Engine(settings)
    app.state.limiter = TokenBucket(settings.rate_limit_per_minute, settings.rate_limit_burst)
    app.state.feedback = FeedbackStore(settings.feedback_dir)
    log.info("GeoInstant ready (mode=%s) %s", app.state.engine.mode, app.state.engine.models)
    yield
    app.state.engine.pool.shutdown(wait=False, cancel_futures=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Keep multipart uploads in RAM (Starlette spools anything over 1 MB to a temp file).
    MultiPartParser.spool_max_size = settings.max_upload_bytes

    app = FastAPI(title="GeoInstant", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["POST", "GET"],
        allow_headers=["Content-Type", "X-API-Key"],
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

    def guard(request: Request) -> None:
        if settings.api_keys or settings.proxy_api_keys:
            supplied = request.headers.get("x-api-key", "")
            if not matches(supplied, settings.api_keys + settings.proxy_api_keys):
                raise HTTPException(401, "Missing or invalid API key")
        wait = request.app.state.limiter.take(client_id(request))
        if wait > 0:
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(int(wait) + 1)})

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
        engine: Engine = request.app.state.engine
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

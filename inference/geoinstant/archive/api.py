"""Archive HTTP routes under /v1/archive (require X-Archive-Token)."""

# No `from __future__ import annotations`: FastAPI must resolve the local `Svc` alias at runtime.

import asyncio
import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from ..config import Settings
from ..imageio import GpsFix, ImageError, content_hash, extract_gps
from .service import ArchiveService, Group, PhotoDetail, PhotoSummary


class PhotoPatch(BaseModel):
    user_lat: float | None = Field(default=None, ge=-90, le=90)
    user_lon: float | None = Field(default=None, ge=-180, le=180)
    user_label: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)
    group_id: str | None = None
    clear_location: bool = False


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    photo_ids: list[str] = Field(min_length=1, max_length=5000)


class GroupPatch(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _client_fix(items: list[object], i: int) -> GpsFix | None:
    g = items[i] if i < len(items) else None
    if not isinstance(g, dict):
        return None
    try:
        lat, lon = float(g["lat"]), float(g["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        return None
    taken = g.get("taken")
    return GpsFix(round(lat, 7), round(lon, 7), None, "exif", str(taken)[:40] if taken else None)


class UploadResult(BaseModel):
    added: list[str]
    skipped: list[str]


def router(settings: Settings) -> APIRouter:
    def auth(request: Request) -> ArchiveService:
        svc: ArchiveService | None = getattr(request.app.state, "archive", None)
        token = settings.archive_token
        if svc is None or not token:
            raise HTTPException(404, "Archive is not enabled")
        if not hmac.compare_digest(request.headers.get("x-archive-token", ""), token):
            raise HTTPException(401, "Not signed in")
        return svc

    Svc = Annotated[ArchiveService, Depends(auth)]
    r = APIRouter(prefix="/v1/archive")

    @r.post("/photos", response_model=UploadResult)
    async def upload(request: Request, svc: Svc) -> UploadResult:
        form = await request.form(max_files=settings.archive_max_files_per_upload, max_fields=4)
        # Optional GPS read on the device (browsers strip EXIF when they shrink photos): [null | {lat, lon, taken}].
        try:
            client_gps = json.loads(str(form.get("gps") or "[]"))
        except ValueError:
            client_gps = []
        added, skipped = [], []
        for i, f in enumerate(form.getlist("files")):
            if not isinstance(f, UploadFile):
                continue
            name = f.filename or "photo"
            data = await f.read()
            await f.close()
            if len(data) > settings.max_upload_bytes:
                skipped.append(f"{name}: too large")
                continue
            try:
                gps = await asyncio.to_thread(extract_gps, data, settings.max_pixels) or _client_fix(client_gps, i)
                pid = await asyncio.to_thread(svc.store.add, name, data, gps, content_hash(data), settings.max_pixels)
            except ImageError as e:
                skipped.append(f"{name}: {e}")
                continue
            added.append(pid)
        svc.notify()
        return UploadResult(added=added, skipped=skipped)

    @r.get("/photos", response_model=list[PhotoSummary])
    async def photos(svc: Svc) -> list[PhotoSummary]:
        return await asyncio.to_thread(svc.summaries)

    @r.get("/photos/{pid}", response_model=PhotoDetail)
    async def photo(pid: str, svc: Svc) -> PhotoDetail:
        d = await asyncio.to_thread(svc.detail, pid)
        if d is None:
            raise HTTPException(404, "No such photo")
        return d

    @r.get("/photos/{pid}/image")
    async def image(pid: str, svc: Svc, size: str = "full") -> FileResponse:
        path = svc.store.image_path(pid if pid.isalnum() else "_", "thumb" if size == "thumb" else "full")
        if not path.exists():
            raise HTTPException(404, "No such photo")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @r.patch("/photos/{pid}", response_model=PhotoDetail)
    async def patch(pid: str, body: PhotoPatch, svc: Svc) -> PhotoDetail:
        if svc.store.get(pid) is None:
            raise HTTPException(404, "No such photo")
        fields = body.model_dump(exclude_unset=True)
        if fields.pop("clear_location", False):
            fields.update(user_lat=None, user_lon=None, user_label=None)
        if "group_id" in fields and fields["group_id"] and fields["group_id"] not in svc.store.groups():
            raise HTTPException(422, "No such group")
        await asyncio.to_thread(svc.store.update, pid, fields)
        d = await asyncio.to_thread(svc.detail, pid)
        assert d is not None
        return d

    @r.delete("/photos/{pid}", status_code=204)
    async def delete(pid: str, svc: Svc) -> None:
        await asyncio.to_thread(svc.store.delete, pid)

    @r.post("/photos/{pid}/reanalyze", status_code=202)
    async def reanalyze(pid: str, svc: Svc) -> None:
        await asyncio.to_thread(svc.store.requeue, pid)
        svc.notify()

    @r.get("/groups", response_model=list[Group])
    async def groups(svc: Svc) -> list[Group]:
        return await asyncio.to_thread(svc.groups)

    @r.post("/groups", response_model=Group)
    async def create_group(body: GroupCreate, svc: Svc) -> Group:
        gid = await asyncio.to_thread(svc.store.create_group, body.name, body.photo_ids)
        return next(g for g in await asyncio.to_thread(svc.groups) if g.id == gid)

    @r.patch("/groups/{gid}", status_code=204)
    async def rename_group(gid: str, body: GroupPatch, svc: Svc) -> None:
        await asyncio.to_thread(svc.store.rename_group, gid, body.name)

    @r.delete("/groups/{gid}", status_code=204)
    async def delete_group(gid: str, svc: Svc) -> None:
        await asyncio.to_thread(svc.store.delete_group, gid)

    return r

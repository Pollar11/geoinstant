"""Archive logic: background analysis queue and effective locations (own, group, or yours)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel

from ..imageio import GpsFix
from ..models.investigator import Investigation
from ..pipeline import Engine
from ..schemas import ErrorEvent, LocateResult, ResultEvent
from .store import ArchiveStore, Row

log = logging.getLogger(__name__)

RES_RANK = {"exact": 6, "street": 5, "city": 4, "region": 3, "country": 2, "continent": 1, "world": 0}
# A photo's own answer is good enough to share with its group at region level or better.
SHAREABLE = {"exact", "street", "city", "region"}


class Location(BaseModel):
    latitude: float
    longitude: float
    label: str
    source: Literal["you", "photo", "group"]
    confidence: float  # 0..100
    resolution: str
    via: str | None = None  # photo id the group location came from


class PhotoSummary(BaseModel):
    id: str
    filename: str
    added_at: str
    status: str
    error: str | None
    width: int
    height: int
    group_id: str | None
    group_name: str | None
    scene: str | None
    era: str | None
    location: Location | None


class PhotoDetail(PhotoSummary):
    note: str | None
    result: LocateResult | None
    investigation: Investigation | None = None


class Group(BaseModel):
    id: str
    name: str
    photo_ids: list[str]
    location: Location | None


INVESTIGATION_RES = {
    "exact": "exact",
    "street": "street",
    "neighborhood": "street",
    "city": "city",
    "region": "region",
    "country": "country",
}


def own_location(row: Row) -> Location | None:
    if row.user_lat is not None and row.user_lon is not None:
        return Location(
            latitude=row.user_lat,
            longitude=row.user_lon,
            label=row.user_label or "Set by you",
            source="you",
            confidence=100,
            resolution="exact",
        )
    options: list[Location] = []
    r = row.result
    if r and r.get("resolution") != "world":
        options.append(
            Location(
                latitude=r["latitude"],
                longitude=r["longitude"],
                label=r["place"]["display_name"],
                source="photo",
                confidence=r["confidence"],
                resolution=r["resolution"],
            )
        )
    rep = (row.investigation or {}).get("report") or {}
    if rep.get("latitude") is not None and rep.get("longitude") is not None and rep.get("precision") in INVESTIGATION_RES:
        options.append(
            Location(
                latitude=rep["latitude"],
                longitude=rep["longitude"],
                label=rep.get("place_name") or "Investigated location",
                source="photo",
                confidence=round(100 * float(rep.get("confidence", 0)), 1),
                resolution=INVESTIGATION_RES[rep["precision"]],
            )
        )
    return max(options, key=_strength, default=None)


def _strength(loc: Location) -> tuple[int, float]:
    return (RES_RANK.get(loc.resolution, 0) + (10 if loc.source == "you" else 0), loc.confidence)


def resolve(rows: list[Row]) -> dict[str, Location | None]:
    """Each photo's best location: yours > its own confident answer > its group's best > its own weak answer."""
    own = {r.id: own_location(r) for r in rows}
    best_in_group: dict[str, tuple[Location, str]] = {}
    for r in rows:
        loc = own[r.id]
        if r.group_id and loc and (loc.source == "you" or loc.resolution in SHAREABLE):
            cur = best_in_group.get(r.group_id)
            if cur is None or _strength(loc) > _strength(cur[0]):
                best_in_group[r.group_id] = (loc, r.id)
    out: dict[str, Location | None] = {}
    for r in rows:
        loc = own[r.id]
        g = best_in_group.get(r.group_id) if r.group_id else None
        if g and g[1] != r.id and (loc is None or _strength(g[0]) > _strength(loc)):
            out[r.id] = g[0].model_copy(update={"source": "group", "via": g[1]})
        else:
            out[r.id] = loc
    return out


class ArchiveService:
    def __init__(
        self, store: ArchiveStore, engine: Engine, people_policy: str, concurrency: int, investigate: bool = True
    ) -> None:
        self.store = store
        self.investigate = investigate
        self.engine = engine
        self.people_policy = people_policy
        self.concurrency = max(1, concurrency)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(self.concurrency)]
        self._wake.set()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def notify(self) -> None:
        self._wake.set()

    async def _worker(self, n: int) -> None:
        while True:
            row = await asyncio.to_thread(self.store.next_queued)
            if row is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                result = await self.analyze(row)
                await asyncio.to_thread(self.store.set_result, row.id, result.model_dump(mode="json"))
                inv = self.engine.investigator
                # Investigate only when the reasoning model is reachable (it produced the clue board).
                if self.investigate and inv is not None and result.analysis is not None:
                    img = await asyncio.to_thread(self._load_image, row.id)
                    async for ev in inv.run(img, row.note or ""):
                        if isinstance(ev, Investigation):
                            await asyncio.to_thread(self.store.set_investigation, row.id, ev.model_dump(mode="json"))
            except Exception as e:
                log.exception("archive analysis failed for %s", row.id)
                await asyncio.to_thread(self.store.set_result, row.id, None, str(e)[:300] or "Analysis failed")

    def _load_image(self, pid: str) -> Image.Image:
        with Image.open(self.store.image_path(pid, "full")) as im:
            return im.convert("RGB")

    async def analyze(self, row: Row) -> LocateResult:
        if row.gps:
            fix = GpsFix(**row.gps)
            return self.engine.gps_result(uuid.uuid4().hex, fix, {"total": 0.0})
        data = await asyncio.to_thread(self.store.image_bytes, row.id)
        best: LocateResult | None = None
        async for ev in self.engine.locate(data, vlm_mode="blocking", people_policy=self.people_policy):
            if isinstance(ev, ErrorEvent):
                raise RuntimeError(ev.message)
            if isinstance(ev, ResultEvent) and ev.type in ("result", "refined"):
                best = ev.result
        if best is None:
            raise RuntimeError("No result")
        return best

    # ---- views ------------------------------------------------------------------------------
    def summaries(self) -> list[PhotoSummary]:
        rows = self.store.all()
        locs = resolve(rows)
        names = self.store.groups()
        return [self._summary(r, locs[r.id], names) for r in rows]

    def detail(self, pid: str) -> PhotoDetail | None:
        row = self.store.get(pid)
        if row is None:
            return None
        rows = [r for r in self.store.all() if r.group_id == row.group_id] if row.group_id else [row]
        loc = resolve(rows)[row.id]
        s = self._summary(row, loc, self.store.groups())
        return PhotoDetail(
            **s.model_dump(),
            note=row.note,
            result=LocateResult.model_validate(row.result) if row.result else None,
            investigation=Investigation.model_validate(row.investigation) if row.investigation else None,
        )

    def groups(self) -> list[Group]:
        rows = self.store.all()
        locs = resolve(rows)
        out = []
        for gid, name in self.store.groups().items():
            members = [r for r in rows if r.group_id == gid]
            best = max((locs[r.id] for r in members if locs[r.id]), key=lambda loc: _strength(loc), default=None)  # type: ignore[arg-type]
            out.append(Group(id=gid, name=name, photo_ids=[r.id for r in members], location=best))
        return out

    @staticmethod
    def _summary(r: Row, loc: Location | None, names: dict[str, str]) -> PhotoSummary:
        analysis: dict[str, Any] = (r.result or {}).get("analysis") or {}
        return PhotoSummary(
            id=r.id,
            filename=r.filename,
            added_at=r.added_at,
            status=r.status,
            error=r.error,
            width=r.width,
            height=r.height,
            group_id=r.group_id,
            group_name=names.get(r.group_id) if r.group_id else None,
            scene=analysis.get("scene"),
            era=analysis.get("era") or None,
            location=loc,
        )

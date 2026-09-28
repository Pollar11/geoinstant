"""Archive logic: background analysis queue and effective locations (own, group, or yours)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from datetime import datetime
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel

from ..geocode import Nominatim
from ..imageio import GpsFix
from ..models.investigator import Investigation
from ..pipeline import Engine
from ..schemas import ErrorEvent, LocateResult, ResultEvent
from ..skyline.service import SkylineService
from ..streetmatch.service import Job, StreetMatchService, StreetResult
from .auto import INDOOR, NEAR_KM2, lead_point, scene_of, skyline_around
from .store import ArchiveStore, Row

log = logging.getLogger(__name__)

RES_RANK = {"exact": 6, "street": 5, "city": 4, "region": 3, "country": 2, "continent": 1, "world": 0}
# A photo's own answer is good enough to share with its group at region level or better.
# Only a street/building-level answer counts as a location; anything coarser is shown as a lead.
PINNED = {"exact", "street"}
SHAREABLE = PINNED


SAME_DAY_H = 12.0  # photos this close in time share a lead
NEAR_H = 3.0  # this close: probably the same neighbourhood


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
    lead: str | None = None
    searching: str | None = None  # automatic street/skyline search in progress
    taken_at: str | None = None  # camera clock


class PhotoDetail(PhotoSummary):
    note: str | None
    result: LocateResult | None
    investigation: Investigation | None = None
    streetmatch: StreetResult | None = None
    skyline: dict[str, Any] | None = None


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


def _inside(lat: float, lon: float, bbox: list[float]) -> bool:
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


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
    sm = row.streetmatch or {}
    if sm.get("verified") and sm.get("best"):
        b = sm["best"]
        house = sm.get("building") or {}
        options.append(
            Location(
                latitude=b["latitude"],
                longitude=b["longitude"],
                label=(f"Facing {house['address']}" if house.get("in_view") else "")
                or f"Matched street photo{' from ' + b['captured_at'] if b.get('captured_at') else ''}",
                source="photo",
                confidence=95,
                resolution="exact",
            )
        )
    sky = row.skyline or {}
    if sky.get("pinned") and sky.get("latitude") is not None:
        options.append(
            Location(
                latitude=sky["latitude"],
                longitude=sky["longitude"],
                label="Mountain skyline match",
                source="photo",
                confidence=round(100 * float(sky.get("confidence", 0)), 1),
                resolution="street",
            )
        )
    return max((o for o in options if o.resolution in PINNED), key=_strength, default=None)


def lead_of(row: Row) -> str | None:
    """Best coarse answer (e.g. 'Cyclades, Greece · region'), shown when there is no exact spot."""
    rep = (row.investigation or {}).get("report") or {}
    if rep.get("place_name") and rep.get("precision") not in (None, "unknown", "exact", "street"):
        return f"{rep['place_name']} · {rep['precision']}"
    r = row.result
    if r and r.get("resolution") not in (None, "world", "exact", "street"):
        return f"{r['place']['display_name']} · {r['resolution']}"
    return None


def hours_apart(a: str, b: str) -> float:
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 3600
    except ValueError:
        return float("inf")


def same_day(row: Row, rows: list[Row], own: dict[str, Location | None]) -> tuple[Row, Location, float] | None:
    """The pinned photo taken closest in time to this one (within 12 h): where the family was that day."""
    if not row.taken_at:
        return None
    best: tuple[Row, Location, float] | None = None
    for r in rows:
        loc = own.get(r.id)
        if r.id == row.id or not r.taken_at or loc is None:
            continue
        h = hours_apart(row.taken_at, r.taken_at)
        if h <= SAME_DAY_H and (best is None or h < best[2]):
            best = (r, loc, h)
    return best


def same_day_text(near: tuple[Row, Location, float]) -> str:
    r, loc, h = near
    gap = "within the hour" if h < 1 else f"{h:.0f} h apart"
    return f"Same day as {r.filename} at {loc.label} ({gap})"


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
        self,
        store: ArchiveStore,
        engine: Engine,
        people_policy: str,
        concurrency: int,
        investigate: bool = True,
        streetmatch: StreetMatchService | None = None,
        skyline: SkylineService | None = None,
        auto_street_km2: float = 25.0,
        auto_skyline_km: float = 20.0,
        geocoder: Nominatim | None = None,
    ) -> None:
        self.geocoder = geocoder
        self.store = store
        self.investigate = investigate
        self.streetmatch = streetmatch
        self.skyline = skyline
        self.auto_street_km2 = auto_street_km2
        self.auto_skyline_km = auto_skyline_km
        self.searching: dict[str, Job | str] = {}
        self._background: set[asyncio.Task[None]] = set()
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
                    img = await asyncio.to_thread(self.load_image, row.id)
                    async for ev in inv.run(img, row.note or ""):
                        if isinstance(ev, Investigation):
                            await asyncio.to_thread(self.store.set_investigation, row.id, ev.model_dump(mode="json"))
            except Exception as e:
                log.exception("archive analysis failed for %s", row.id)
                await asyncio.to_thread(self.store.set_result, row.id, None, str(e)[:300] or "Analysis failed")
                continue
            try:
                await self.auto_locate(row.id)
                done = await asyncio.to_thread(self.store.get, row.id)
                if done and own_location(done):
                    await self.wake_same_day(row.id)
            except Exception:  # the analysis stands; the automatic search is a bonus
                log.exception("automatic search failed for %s", row.id)

    async def auto_locate(self, pid: str) -> None:
        """No exact spot yet: search around the lead (skyline for mountains, street photos outdoors)."""
        row = await asyncio.to_thread(self.store.get, pid)
        if row is None or own_location(row) is not None or pid in self.searching:
            return
        lead = lead_point(row, self.auto_street_km2)
        rows = await asyncio.to_thread(self.store.all)
        near = same_day(row, rows, {r.id: own_location(r) for r in rows})
        if near:
            # Where the family was that day beats a town-level guess; within 3 h, beats everything.
            _, loc, h = near
            if lead is None or lead[2] > NEAR_KM2 or h <= NEAR_H:
                lead = (loc.latitude, loc.longitude, NEAR_KM2 if h <= NEAR_H else self.auto_street_km2)
        if lead is None:
            return
        lat, lon, km2 = lead
        prev = row.streetmatch or {}
        if prev.get("bbox") and _inside(lat, lon, prev["bbox"]):
            return  # already searched around here
        scene = scene_of(row)
        img = await asyncio.to_thread(self.load_image, pid)
        if scene == "mountain" and self.skyline is not None:
            self.searching[pid] = "Matching the mountain skyline around the lead…"
            try:
                sky = await asyncio.to_thread(skyline_around, self.skyline, img, lat, lon, self.auto_skyline_km)
                await asyncio.to_thread(self.store.set_skyline, pid, sky)
            except Exception:
                log.exception("automatic skyline search failed for %s", pid)
                sky = {}
            finally:
                self.searching.pop(pid, None)
            if sky.get("pinned"):
                return
        if scene in INDOOR or self.streetmatch is None or not self.streetmatch.enabled:
            return

        async def save(job: Job) -> None:
            self.searching.pop(pid, None)
            if job.result:
                await asyncio.to_thread(self.store.set_streetmatch, pid, job.result.model_dump(mode="json"))
                if job.result.verified:
                    await self.wake_same_day(pid)

        self.searching[pid] = self.streetmatch.start_around(img, lat, lon, km2, pid, save)

    async def wake_same_day(self, pid: str) -> None:
        """A photo was just pinned: search around it for the unpinned photos taken the same day."""
        row = await asyncio.to_thread(self.store.get, pid)
        if row is None or not row.taken_at:
            return
        for r in await asyncio.to_thread(self.store.all):
            if r.id != pid and r.taken_at and hours_apart(r.taken_at, row.taken_at) <= SAME_DAY_H and own_location(r) is None:
                await self.auto_locate(r.id)

    def spawn(self, coro: Coroutine[None, None, None]) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def searching_message(self, pid: str) -> str | None:
        s = self.searching.get(pid)
        return s if isinstance(s, str) or s is None else s.message or "Searching street photos around the lead…"

    def load_image(self, pid: str) -> Image.Image:
        with Image.open(self.store.image_path(pid, "full")) as im:
            return im.convert("RGB")

    async def analyze(self, row: Row) -> LocateResult:
        if row.gps:
            fix = GpsFix(**row.gps)
            result = self.engine.gps_result(uuid.uuid4().hex, fix, {"total": 0.0})
            # The offline place list only knows cities; an exact spot deserves its street address.
            addr = await self.geocoder.address(fix.latitude, fix.longitude) if self.geocoder else None
            if addr:
                result = result.model_copy(update={"place": result.place.model_copy(update={"display_name": addr})})
            return result
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
        own = {r.id: own_location(r) for r in rows}
        return [self._summary(r, locs[r.id], names, same_day(r, rows, own)) for r in rows]

    def detail(self, pid: str) -> PhotoDetail | None:
        row = self.store.get(pid)
        if row is None:
            return None
        every = self.store.all()
        rows = [r for r in every if r.group_id == row.group_id] if row.group_id else [row]
        loc = resolve(rows)[row.id]
        near = same_day(row, every, {r.id: own_location(r) for r in every})
        s = self._summary(row, loc, self.store.groups(), near)
        return PhotoDetail(
            **s.model_dump(),
            note=row.note,
            result=LocateResult.model_validate(row.result) if row.result else None,
            investigation=Investigation.model_validate(row.investigation) if row.investigation else None,
            streetmatch=StreetResult.model_validate(row.streetmatch) if row.streetmatch else None,
            skyline=row.skyline,
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

    def _summary(
        self, r: Row, loc: Location | None, names: dict[str, str], near: tuple[Row, Location, float] | None = None
    ) -> PhotoSummary:
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
            lead=None if loc else (same_day_text(near) if near else lead_of(r)),
            searching=None if loc else self.searching_message(r.id),
            taken_at=r.taken_at,
        )

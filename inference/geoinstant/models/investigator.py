"""Investigator: Claude as a GeoGuessr-pro detective with tools (zoom, web search, map lookup).

Loop: look at the photo → zoom into details → search the web for names/text it reads →
look up addresses on the map → report_location with the full reasoning chain.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from .reverse_image import ReverseImage

log = logging.getLogger(__name__)

SYSTEM = """You are a world-class photo geolocation investigator - think GeoGuessr champion plus OSINT \
researcher. Find where this photo was taken, as precisely as the evidence allows.

Method:
1. Survey the whole photo. List every clue: text, signs, shop/brand names, menus, prices, currency, \
plates, architecture, interiors (sockets, switches, radiators, tiles, windows), vegetation, terrain, \
coastline, sun/shadows, vehicles, era.
2. Use `zoom` on anything small or blurry that might be readable or diagnostic.
3. Use `web_search` to research what you read (business names, slogans, phone numbers, landmarks, \
products and where they were sold) and to verify candidate places.
4. Use `geocode` to get coordinates for a named place or address, and `reverse_geocode` to check a spot.
5. Interiors and scenes without readable names (when `reverse_image_search` is available): search the whole \
photo, then distinctive details (a lamp, tiles, wallpaper, a mural, a bar counter). Cafés, hotels, bars and \
restaurants have interior photos on maps, review and booking sites. Open promising pages with `web_fetch` and \
look at candidate photos with `view_image`; claim a venue only when several distinctive details match.
6. Narrate briefly between steps (one or two sentences), like a pro explaining a round.
7. Finish by calling `report_location` exactly once.

Rules:
- Never identify people, and never use faces or bodies as evidence. Do not run `reverse_image_search` \
when people are the main subject.
- Be honest about precision. Indoors with nothing identifying, report country or region, not a \
made-up address. Only claim "exact" or "street" when a specific place is identified and verified.
- confidence = probability the true spot is within the stated precision."""

PRECISION = ["exact", "street", "neighborhood", "city", "region", "country", "unknown"]

REPORT_TOOL: dict[str, Any] = {
    "name": "report_location",
    "description": "Submit the final answer. Call exactly once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "latitude",
            "longitude",
            "precision",
            "confidence",
            "place_name",
            "address",
            "summary",
            "evidence_chain",
            "people_are_main_subject",
        ],
        "properties": {
            "latitude": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "longitude": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "precision": {"type": "string", "enum": PRECISION},
            "confidence": {"type": "number", "description": "0..1"},
            "place_name": {"type": "string", "description": "e.g. 'Taverna Nikos, Oia, Santorini, Greece'"},
            "address": {"type": "string", "description": "street address if known, else ''"},
            "summary": {"type": "string", "description": "2-4 sentences: how you found it"},
            "people_are_main_subject": {"type": "boolean"},
            "evidence_chain": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["clue", "conclusion"],
                    "properties": {"clue": {"type": "string"}, "conclusion": {"type": "string"}},
                },
            },
        },
    },
}

CLIENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "zoom",
        "description": "Zoom into a region of the photo. Coordinates are fractions of width/height (0..1).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x0": {"type": "number"},
                "y0": {"type": "number"},
                "x1": {"type": "number"},
                "y1": {"type": "number"},
                "why": {"type": "string"},
            },
            "required": ["x0", "y0", "x1", "y1"],
        },
    },
    {
        "name": "geocode",
        "description": "Find coordinates for a place name or address (OpenStreetMap). Returns up to 5 matches.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "reverse_geocode",
        "description": "What is at these coordinates (address, place name)?",
        "input_schema": {
            "type": "object",
            "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}},
            "required": ["latitude", "longitude"],
        },
    },
    REPORT_TOOL,
]

REVERSE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "reverse_image_search",
        "description": "Find web pages and photos that match the photo (or a region of it): best-guess labels, "
        "named entities, pages with matching images, visually similar images, landmarks with coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x0": {"type": "number", "description": "optional region, fractions of width/height; omit for whole photo"},
                "y0": {"type": "number"},
                "x1": {"type": "number"},
                "y1": {"type": "number"},
                "why": {"type": "string"},
                "people_are_main_subject": {"type": "boolean"},
            },
            "required": ["why", "people_are_main_subject"],
        },
    },
    {
        "name": "view_image",
        "description": "Look at a candidate photo from a URL (e.g. from reverse_image_search or a fetched page) "
        "to compare it with the photo being investigated.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "why": {"type": "string"}},
            "required": ["url"],
        },
    },
]


class Evidence(BaseModel):
    clue: str
    conclusion: str


class Report(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    precision: Literal["exact", "street", "neighborhood", "city", "region", "country", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0, le=1)
    place_name: str = ""
    address: str = ""
    summary: str = ""
    evidence_chain: list[Evidence] = []
    people_are_main_subject: bool = False

    def coarsened(self) -> Report:
        """City level at most: for public use when people are the main subject."""
        if not self.people_are_main_subject or self.precision in ("city", "region", "country", "unknown"):
            return self
        return self.model_copy(
            update={
                "latitude": round(self.latitude, 1) if self.latitude is not None else None,
                "longitude": round(self.longitude, 1) if self.longitude is not None else None,
                "precision": "city",
                "address": "",
                "place_name": ", ".join(self.place_name.split(", ")[-2:]),
                "summary": "People are the main subject, so the location is shown at city level only.",
                "evidence_chain": [],
            }
        )


class Step(BaseModel):
    kind: Literal["note", "zoom", "search", "geocode", "reverse", "image_search", "view", "error"]
    text: str
    box: tuple[float, float, float, float] | None = None


class Investigation(BaseModel):
    report: Report | None
    steps: list[Step]
    model: str
    seconds: float


def _jpeg_b64(img: Image.Image, max_side: int) -> str:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


class Investigator:
    def __init__(
        self,
        client: Any,
        model: str,
        effort: str,
        geocode_url: str,
        user_agent: str,
        max_steps: int = 18,
        max_searches: int = 6,
        budget_s: float = 180.0,
        reverse: ReverseImage | None = None,
    ) -> None:
        self.reverse = reverse
        self.client = client
        self.model = model
        self.effort = effort
        self.geocode_url = geocode_url.rstrip("/")
        self.user_agent = user_agent
        self.max_steps = max_steps
        self.max_searches = max_searches
        self.budget_s = budget_s
        self._geo_lock = asyncio.Lock()  # Nominatim policy: at most 1 request/second

    # ---- tools ------------------------------------------------------------------------------
    async def _nominatim(self, path: str, params: dict[str, Any]) -> Any:
        async with self._geo_lock:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": self.user_agent}) as http:
                r = await http.get(f"{self.geocode_url}/{path}", params={**params, "format": "jsonv2"})
            await asyncio.sleep(1.0)
        r.raise_for_status()
        return r.json()

    async def _run_tool(
        self, name: str, args: dict[str, Any], img: Image.Image, people_policy: str = "coarsen"
    ) -> tuple[list[dict[str, Any]], Step]:
        if name == "zoom":
            x0, y0, x1, y1 = (float(min(max(args.get(k, d), 0.0), 1.0)) for k, d in (("x0", 0), ("y0", 0), ("x1", 1), ("y1", 1)))
            if x1 - x0 < 0.01 or y1 - y0 < 0.01:
                return [{"type": "text", "text": "Region too small."}], Step(kind="error", text="Zoom region too small")
            w, h = img.size
            crop = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
            if max(crop.size) < 768:  # upscale small crops so text is legible
                s = 768 / max(crop.size)
                crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))), Image.Resampling.LANCZOS)
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(crop, 1024)}}]
            return content, Step(kind="zoom", text=str(args.get("why") or "Zoomed in"), box=(x0, y0, x1, y1))
        if name == "geocode":
            q = str(args.get("query", ""))[:200]
            rows = await self._nominatim("search", {"q": q, "limit": 5})
            out = [
                {"name": r.get("display_name"), "lat": float(r["lat"]), "lon": float(r["lon"]), "type": r.get("type")}
                for r in rows
            ]
            return [{"type": "text", "text": json.dumps(out) if out else "No matches."}], Step(
                kind="geocode", text=f"Map lookup: {q}"
            )
        if name == "reverse_geocode":
            lat, lon = float(args["latitude"]), float(args["longitude"])
            r = await self._nominatim("reverse", {"lat": lat, "lon": lon})
            txt = r.get("display_name", "Nothing found") if isinstance(r, dict) else "Nothing found"
            return [{"type": "text", "text": txt}], Step(kind="reverse", text=f"Checked {lat:.4f}, {lon:.4f}")
        if name == "reverse_image_search" and self.reverse is not None:
            if args.get("people_are_main_subject") and people_policy == "coarsen":
                return [{"type": "text", "text": "Not available when people are the main subject."}], Step(
                    kind="error", text="Reverse image search skipped: people are the main subject"
                )
            region = img
            if all(k in args for k in ("x0", "y0", "x1", "y1")):
                x0, y0, x1, y1 = (float(min(max(args[k], 0.0), 1.0)) for k in ("x0", "y0", "x1", "y1"))
                if x1 - x0 >= 0.05 and y1 - y0 >= 0.05:
                    w, h = img.size
                    region = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
            found = await self.reverse.search(region)
            n = len(found["pages"]) + len(found["similar_images"]) + len(found["landmarks"])
            return [{"type": "text", "text": json.dumps(found, ensure_ascii=False)}], Step(
                kind="image_search", text=f"Reverse image search: {args.get('why') or 'whole photo'} ({n} leads)"
            )
        if name == "view_image" and self.reverse is not None:
            url = str(args.get("url", ""))[:2000]
            other = await self.reverse.fetch_image(url)
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(other, 1024)}}
            ]
            return content, Step(
                kind="view", text=f"Compared with {urlparse(url).hostname or 'a photo'}: {args.get('why') or ''}".strip()
            )
        return [{"type": "text", "text": f"Unknown tool {name}"}], Step(kind="error", text=f"Unknown tool {name}")

    # ---- loop -------------------------------------------------------------------------------
    async def run(
        self, img: Image.Image, context: str = "", people_policy: str = "coarsen"
    ) -> AsyncIterator[Step | Investigation]:
        t0 = time.perf_counter()
        steps: list[Step] = []
        intro = "Where was this photo taken? Investigate and report."
        if context.strip():
            intro += f"\n\nWhat the family knows (may be wrong): {context.strip()[:1000]}"
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(img, 1568)}},
                    {"type": "text", "text": intro},
                ],
            }
        ]
        tools = [
            *CLIENT_TOOLS,
            *(REVERSE_TOOLS if self.reverse else []),
            {"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4},
        ]
        report: Report | None = None
        nudged = False
        model = self.model

        def emit(s: Step) -> Step:
            steps.append(s)
            return s

        for _ in range(self.max_steps):
            if time.perf_counter() - t0 > self.budget_s:
                yield emit(Step(kind="error", text="Time budget reached"))
                break
            try:
                resp = await self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    system=SYSTEM,
                    tools=tools,
                    messages=messages,
                )
            except Exception as e:  # noqa: BLE001 - surface API problems as a step, keep the page alive
                log.warning("investigator API error: %s", e)
                yield emit(Step(kind="error", text="The reasoning service is unavailable right now."))
                break
            model = getattr(resp, "model", model)
            if resp.stop_reason == "refusal":
                yield emit(Step(kind="error", text="The reasoning model declined this photo."))
                break
            messages.append({"role": "assistant", "content": resp.content})

            results: list[dict[str, Any]] = []
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    yield emit(Step(kind="note", text=b.text.strip()[:600]))
                elif b.type == "server_tool_use" and b.name == "web_search":
                    yield emit(Step(kind="search", text=f"Web search: {(b.input or {}).get('query', '')}"))
                elif b.type == "server_tool_use" and b.name == "web_fetch":
                    yield emit(Step(kind="search", text=f"Opened: {(b.input or {}).get('url', '')[:200]}"))
                elif b.type == "tool_use":
                    if b.name == "report_location":
                        try:
                            report = Report.model_validate(b.input)
                        except ValidationError:
                            report = None
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": "Received."})
                        continue
                    try:
                        content, step = await self._run_tool(b.name, dict(b.input or {}), img, people_policy)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": content})
                    except Exception as e:  # noqa: BLE001
                        step = Step(kind="error", text=f"{b.name} failed")
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}", "is_error": True})
                    yield emit(step)

            if report is not None:
                break
            if results:
                messages.append({"role": "user", "content": results})
                continue
            if resp.stop_reason == "pause_turn":
                continue
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Call report_location now with your best answer."})

        if (
            report
            and report.latitude is not None
            and report.longitude is not None
            and not (-90 <= report.latitude <= 90 and -180 <= report.longitude <= 180)
        ):
            report.latitude = report.longitude = None
        yield Investigation(report=report, steps=steps, model=model, seconds=round(time.perf_counter() - t0, 1))


def load_investigator(
    mode: str, model: str, effort: str, api_key: str | None, geocode_url: str, user_agent: str, vision_key: str = ""
) -> Investigator | None:
    if mode == "off":
        return None
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
    except Exception:  # noqa: BLE001
        log.warning("Investigator disabled: could not create the Anthropic client")
        return None
    return Investigator(client, model, effort, geocode_url, user_agent, reverse=ReverseImage(vision_key) if vision_key else None)

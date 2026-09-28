"""Optional Claude reasoning stage (structured JSON output, off the critical path)."""

from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from PIL import Image
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam

Effort = Literal["low", "medium", "high"]

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert photo geolocator (GeoGuessr-champion level). You see one photo, often an \
old family snapshot, and work out where it was taken from what is visible.

Work through every clue that applies, like a pro narrating a round:
- Text: language, script, spelling, shop and brand names, menus, prices, currency, phone formats, \
licence plates, street and road signs, newspapers, calendars, posters.
- Indoors (homes, bars, restaurants): power sockets and plugs, light switches, radiators and \
heaters, window and door styles, tiles and floors, furniture and decor, appliances, bottle and \
packaging labels, beer taps, ashtrays, crockery, TV and electronics.
- Beaches and coasts: sand and rock colour, water colour, waves, beach umbrellas and loungers, \
lifeguard towers, flags, palm and tree species, coastline shape, islands or mountains on the horizon.
- Rural and streets: crops, fences, barns, roof shapes and materials, soil colour, trees, \
utility poles, road markings, bollards, vehicles, driving side, architecture.
- Mountains and landscape: ridge shapes, rock type, snow line, vegetation zone.
- Light: sun height and shadow direction (hemisphere, latitude, season).
- Era: clothing, hairstyles, cars, photo print style. Estimate the decade.

Rules:
- Only use what is visible. Never identify people or use faces as evidence.
- Give up to 3 candidate places with calibrated probabilities (they may sum to less than 1). \
Each radius_km should cover the place with ~70% certainty: a generic beach deserves thousands of km.
- Leave latitude/longitude null for a candidate you can only place at country level or coarser.
- strength: how specific each clue is (strong = points to one country or region; weak = generic)."""

USER_PROMPT = "Where was this photo taken? Answer with the JSON schema."

SCENES = ["home", "bar_restaurant", "other_indoor", "beach_coast", "mountain", "rural", "urban", "other"]
CATEGORIES = ["text", "architecture", "interior", "infrastructure", "vehicles", "nature", "terrain", "light", "era", "other"]

_NUM_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scene", "era", "clues", "candidates", "visible_text", "people_are_main_subject"],
    "properties": {
        "scene": {"type": "string", "enum": SCENES},
        "era": {"type": "string", "description": "e.g. '1970s', or '' if unclear"},
        "clues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "clue", "implies", "strength"],
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "clue": {"type": "string"},
                    "implies": {"type": "string"},
                    "strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
                },
            },
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["country_code", "region", "city", "latitude", "longitude", "probability", "radius_km"],
                "properties": {
                    "country_code": {"type": "string", "description": "ISO 3166-1 alpha-2, or '' if unknown"},
                    "region": {"type": "string"},
                    "city": {"type": "string"},
                    "latitude": _NUM_OR_NULL,
                    "longitude": _NUM_OR_NULL,
                    "probability": {"type": "number", "description": "0..1"},
                    "radius_km": {"type": "number"},
                },
            },
        },
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "people_are_main_subject": {"type": "boolean"},
    },
}


class VlmClue(BaseModel):
    category: str = "other"
    clue: str
    implies: str
    strength: Literal["strong", "medium", "weak"] = "medium"


class VlmCandidate(BaseModel):
    country_code: str = ""
    region: str = ""
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    probability: float = 0.0
    radius_km: float = 1000.0

    @property
    def label(self) -> str:
        return ", ".join(p for p in (self.city, self.region, self.country_code) if p) or "unknown"


class VlmAnswer(BaseModel):
    scene: str = "other"
    era: str = ""
    clues: list[VlmClue] = []
    candidates: list[VlmCandidate] = []
    visible_text: list[str] = []
    people_are_main_subject: bool = False


@dataclass
class VlmResult:
    answer: VlmAnswer
    model: str
    notes: list[str] = field(default_factory=list)


def encode_jpeg(image: Image.Image, max_side: int = 1024, quality: int = 85) -> str:
    img = image.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


class ClaudeVlm:
    def __init__(self, model: str, effort: Effort, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        # With no explicit key the SDK resolves ANTHROPIC_API_KEY / auth token / CLI profile.
        self.client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        self.model = model
        self.effort = effort
        self.name = f"claude:{model}"
        self.disabled = False

    async def analyze(self, image: Image.Image) -> VlmResult | None:
        import anthropic

        if self.disabled:
            return None
        messages: list[BetaMessageParam] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": encode_jpeg(image)},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ]
        output_config: BetaOutputConfigParam = {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        }
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                # A policy decline is re-run server-side on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config=output_config,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
        except anthropic.AuthenticationError:
            log.warning("VLM disabled: Anthropic credentials were rejected")
            self.disabled = True
            return None
        except anthropic.RateLimitError:
            log.warning("VLM rate limited; skipping")
            return None
        except anthropic.APIStatusError as e:
            log.warning("VLM API error %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("VLM unreachable; skipping")
            return None

        if response.stop_reason == "refusal":
            log.info("VLM declined the request")
            return None
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            answer = VlmAnswer.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError):
            log.warning("VLM returned unparseable output (stop_reason=%s)", response.stop_reason)
            return None
        for c in answer.candidates:
            c.probability = min(max(c.probability, 0.0), 1.0)
            c.radius_km = min(max(c.radius_km, 0.05), 5000.0)
        answer.candidates = sorted(answer.candidates, key=lambda c: -c.probability)[:3]
        return VlmResult(answer=answer, model=response.model)


def load_vlm(mode: str, model: str, effort: Effort, api_key: str | None) -> ClaudeVlm | None:
    if mode == "off":
        return None
    try:
        return ClaudeVlm(model, effort, api_key)
    except Exception:  # noqa: BLE001 - missing credentials must not stop the service booting
        log.warning("VLM disabled: could not create the Anthropic client (no credentials?)")
        return None

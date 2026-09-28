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

SYSTEM_PROMPT = """You are the reasoning stage of an image geolocation system. You see one photo and \
estimate where it was taken from the environment only: vegetation, terrain, soil, sky and sun \
angle, architecture, road markings, signs and their script/language, licence-plate formats, \
utility poles, bollards, vehicles, shop names and any other visible text.

Rules:
- Report only what is visible. List each clue you relied on and what it implies.
- Be calibrated: confidence is your probability that the true location lies within radius_km \
of your coordinates. A generic beach or forest deserves a large radius and low confidence.
- Leave latitude/longitude null if you cannot do better than a continent.
- Never identify people or use faces as evidence. Set people_are_main_subject to true when \
people are the main subject of the photo."""

USER_PROMPT = "Where was this photo taken? Answer with the JSON schema."

RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "country_code",
        "region",
        "city",
        "latitude",
        "longitude",
        "confidence",
        "radius_km",
        "clues",
        "visible_text",
        "people_are_main_subject",
    ],
    "properties": {
        "country_code": {"type": "string", "description": "ISO 3166-1 alpha-2, or empty if unknown"},
        "region": {"type": "string"},
        "city": {"type": "string"},
        "latitude": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "longitude": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "confidence": {"type": "number", "description": "0..1"},
        "radius_km": {"type": "number"},
        "clues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["clue", "implies"],
                "properties": {"clue": {"type": "string"}, "implies": {"type": "string"}},
            },
        },
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "people_are_main_subject": {"type": "boolean"},
    },
}


class VlmClue(BaseModel):
    clue: str
    implies: str


class VlmAnswer(BaseModel):
    country_code: str = ""
    region: str = ""
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    confidence: float = 0.0
    radius_km: float = 1000.0
    clues: list[VlmClue] = []
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
        answer.confidence = min(max(answer.confidence, 0.0), 1.0)
        answer.radius_km = min(max(answer.radius_km, 0.05), 5000.0)
        return VlmResult(answer=answer, model=response.model)


def load_vlm(mode: str, model: str, effort: Effort, api_key: str | None) -> ClaudeVlm | None:
    if mode == "off":
        return None
    try:
        return ClaudeVlm(model, effort, api_key)
    except Exception:  # noqa: BLE001 - missing credentials must not stop the service booting
        log.warning("VLM disabled: could not create the Anthropic client (no credentials?)")
        return None

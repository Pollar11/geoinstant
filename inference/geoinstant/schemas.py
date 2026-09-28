"""Public API contract (mirrored by web/lib/api-types.ts - keep them in sync)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Resolution = Literal["exact", "street", "city", "region", "country", "continent", "world"]
ResultStage = Literal["partial", "final", "refined"]


class Place(BaseModel):
    name: str
    admin1: str
    country: str
    country_code: str
    continent: str
    display_name: str
    distance_km: float = Field(description="Distance from the prediction to the named place")


class HierarchyNode(BaseModel):
    level: Literal["continent", "country", "region", "city"]
    name: str
    code: str
    probability: float = Field(ge=0, le=1)


class Candidate(BaseModel):
    latitude: float
    longitude: float
    name: str
    country_code: str
    probability: float


class EvidenceItem(BaseModel):
    source: Literal["exif", "classifier", "retrieval", "cue", "text", "vlm"]
    label: str
    detail: str = ""
    likelihood_ratio: float = Field(
        description="How many times more likely this evidence makes the answer vs. the rest of the world"
    )
    direction: Literal["supports", "contradicts", "neutral"]


class RegionBox(BaseModel):
    box: tuple[float, float, float, float] = Field(description="x0, y0, x1, y1 normalised to [0, 1]")
    label: str
    score: float
    source: str


class Privacy(BaseModel):
    coarsened: bool = False
    reason: str | None = None
    stored: bool = False


class Clue(BaseModel):
    category: str
    clue: str
    implies: str
    strength: Literal["strong", "medium", "weak"]


class Guess(BaseModel):
    label: str
    country_code: str
    latitude: float | None
    longitude: float | None
    probability: float
    radius_km: float


class Analysis(BaseModel):
    """The visual-reasoning clue board (present when the VLM stage answered)."""

    scene: str
    era: str
    model: str
    clues: list[Clue] = []
    guesses: list[Guess] = []


class LocateResult(BaseModel):
    request_id: str
    stage: ResultStage
    source: Literal["exif", "xmp", "visual"]
    latitude: float
    longitude: float
    uncertainty_radius_m: float
    confidence: float = Field(ge=0, le=100, description="Calibrated probability (%) that the answer is right at `resolution`")
    resolution: Resolution
    place: Place
    hierarchy: list[HierarchyNode] = []
    candidates: list[Candidate] = []
    evidence: list[EvidenceItem] = []
    regions: list[RegionBox] = []
    explanation: str
    captured_at: str | None = None
    timings_ms: dict[str, float] = {}
    mode: Literal["production", "partial", "dev"]
    models: dict[str, str] = {}
    privacy: Privacy = Privacy()
    cached: bool = False
    analysis: Analysis | None = None


# ---- Streaming events (text/event-stream, one JSON object per `data:` line) ------------------
class StageEvent(BaseModel):
    type: Literal["stage"] = "stage"
    stage: Literal["upload", "metadata", "decode", "embedding", "retrieval", "detection", "ocr", "fusion", "vlm"]
    status: Literal["running", "done", "skipped", "timeout", "error"]
    ms: float | None = None
    detail: str = ""


class ResultEvent(BaseModel):
    type: Literal["partial", "result", "refined"]
    result: LocateResult


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    request_id: str
    total_ms: float


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    status: int
    message: str


Event = StageEvent | ResultEvent | DoneEvent | ErrorEvent


# ---- Feedback ----------------------------------------------------------------------------------
class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    place_name: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=1000)
    was_correct: bool | None = None
    consent_store_image: bool = False
    image_base64: str | None = Field(default=None, description="Only accepted with consent_store_image=true")


class FeedbackResponse(BaseModel):
    accepted: bool
    feedback_id: str
    stored_image: bool
    stored_embedding: bool


class Health(BaseModel):
    status: Literal["ok"]
    mode: Literal["production", "partial", "dev"]
    models: dict[str, str]
    cells: int
    index_rows: int


# ---- Skyline (mountain) matching ---------------------------------------------------------------
class SkylineCandidate(BaseModel):
    latitude: float
    longitude: float
    elevation_m: float
    azimuth_deg: float = Field(description="Direction the camera faced, degrees from north")
    fov_deg: float = Field(description="Estimated horizontal field of view")
    fit_error: float = Field(description="Skyline mismatch relative to its own spread (0 = perfect)")
    match: float = Field(ge=0, le=1, description="Fit relative to the best candidate")
    place: Place


class SkylineResult(BaseModel):
    status: Literal["ok", "too_flat", "no_area", "area_too_large", "no_data"]
    message: str = ""
    confidence: float = Field(ge=0, le=100, description="How clearly the best candidate beats the others")
    profile: list[tuple[float, float, float]] = Field(description="Skyline used: (x, y, weight), normalised image coords")
    traced: bool
    relief_deg: float
    search_area: tuple[float, float, float, float] | None = Field(description="south, west, north, east")
    viewpoints: int = 0
    spacing_km: float = 0.0
    candidates: list[SkylineCandidate] = []
    heat: list[tuple[float, float, float]] = Field(default=[], description="(lat, lon, score) of searched viewpoints")
    timings_ms: dict[str, float] = {}


class SkylineCoverage(BaseModel):
    regions: list[tuple[float, float, float, float]]
    on_demand: bool
    max_area_km2: float

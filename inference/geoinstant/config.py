"""Runtime configuration (environment variables prefixed with ``GEOINSTANT_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEOINSTANT_", env_file=".env", extra="ignore")

    # --- Artifacts -------------------------------------------------------------------------
    # Missing artifacts are skipped; with none the service runs in dev mode.
    artifacts_dir: Path = Path("./artifacts")
    image_encoder: str = "image_encoder.onnx"  # SigLIP / GeoCLIP-style vision tower, L2-normalised output
    image_encoder_size: int = 224
    cell_prototypes: str = "cell_prototypes.npz"  # leaf-cell classifier weights or zero-shot text prototypes
    cells: str = "cells.npz"  # hierarchical geocell tree (falls back to the bundled seed gazetteer)
    index: str = "index.npz"  # geo-tagged image embeddings (numpy) ...
    faiss_index: str = "index.faiss"  # ... or a FAISS index with the same row order
    detector: str = "cue_detector.onnx"  # YOLO-style detector fine-tuned on location cues
    detector_classes: str = "cue_detector.classes.json"
    cue_priors: Path = DATA_DIR / "cue_priors.json"
    gazetteer_csv: str = "cities.csv"  # GeoNames cities1000 export; bundled seed used when absent
    countries_csv: str = "countries.csv"  # GeoNames countryInfo export; bundled subset used when absent

    onnx_providers: list[str] = Field(
        default_factory=lambda: ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    onnx_threads: int = 0  # 0 = let ONNX Runtime decide

    # --- Pipeline --------------------------------------------------------------------------
    max_upload_bytes: int = 25 * 1024 * 1024
    max_pixels: int = 60_000_000  # decompression-bomb guard
    work_size: int = 1024  # long edge used for detection / OCR / VLM
    fast_deadline_ms: int = 750  # budget for the main ("final") result, server side
    retrieval_k: int = 64
    retrieval_temperature: float = 0.05
    resolution_min_mass: float = 0.30  # a hierarchy level is reported only if its node holds this much mass
    calibration_temperature: float = 1.0  # fitted by scripts/evaluate.py --calibrate
    # Per-source weights of the product-of-experts fusion (exponents on each likelihood).
    weight_classifier: float = 1.0
    weight_retrieval: float = 1.0
    weight_cue: float = 1.0
    weight_text: float = 1.0
    weight_vlm: float = 0.7

    # --- Optional VLM reasoning (Claude) ---------------------------------------------------
    vlm_mode: Literal["off", "enrich", "blocking"] = "enrich"
    vlm_model: str = "claude-opus-5"
    vlm_effort: Literal["low", "medium", "high"] = "low"
    vlm_deadline_ms: int = 12_000
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # --- Investigator (Claude agent with zoom, web search and map lookup) --------------------
    investigator_mode: Literal["on", "off"] = "on"
    investigator_model: str = "claude-opus-5"
    investigator_effort: Literal["low", "medium", "high"] = "medium"
    geocode_url: str = "https://nominatim.openstreetmap.org"  # OSM Nominatim (max 1 request/s)
    geocode_user_agent: str = "GeoInstant/1.0 (private family photo archive)"
    mapillary_token: str = ""  # free client token from mapillary.com/dashboard/developers → recent street photos

    # --- Privacy / abuse -------------------------------------------------------------------
    # City-level precision at most when people are the main subject.
    people_precision_policy: Literal["coarsen", "off"] = "coarsen"
    coarsen_radius_km: float = 10.0
    api_keys: list[str] = Field(default_factory=list)  # empty = open (put the web proxy in front)
    # Proxy keys (the web app): rate-limited per end user via X-Forwarded-For.
    proxy_api_keys: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = 20
    rate_limit_burst: int = 10
    trusted_proxy_hops: int = 1  # how many X-Forwarded-For hops to trust
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3300"])

    # --- Skyline (mountain) matching ----------------------------------------------------
    skyline_dir: str = "skyline"  # prebuilt region indexes (scripts/build_skyline_index.py) + on-demand cache
    dem_dir: str = "dem"  # SRTM .hgt tiles
    dem_tile_url: str = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{ns}/{name}.hgt.gz"  # "" = offline
    skyline_max_area_km2: float = 5000.0  # on-demand search area limit
    skyline_max_km: float = 40.0  # how far to render terrain

    # --- Private photo archive (album) ------------------------------------------------------
    archive_dir: Path = Path("./archive")
    archive_token: str = ""  # shared secret with the web app; empty = archive disabled
    archive_people_policy: Literal["coarsen", "off"] = "off"  # your own family photos: full precision
    archive_concurrency: int = 3
    archive_investigate: bool = True  # run the investigator on every album photo (uses web search)
    archive_max_files_per_upload: int = 100

    # --- Feedback / continuous learning ----------------------------------------------------
    feedback_dir: Path = Path("./feedback")
    result_cache_size: int = 2048
    result_cache_ttl_s: int = 900

    def artifact(self, name: str) -> Path:
        return self.artifacts_dir / name


@lru_cache
def get_settings() -> Settings:
    return Settings()

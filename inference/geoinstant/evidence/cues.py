"""Detected cues → likelihood ratios from data/cue_priors.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..gazetteer import Country
from ..models.detector import Detection
from .base import Observation

NON_LOCATION_CLASSES = {"person", "face"}


class CueKnowledgeBase:
    def __init__(self, path: Path, countries: dict[str, Country]) -> None:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self.entries: dict[str, dict[str, Any]] = raw.get("cues", {})
        self.countries = countries

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def observation(self, key: str, score: float, box: tuple[float, float, float, float] | None = None) -> Observation | None:
        e = self.entries.get(key)
        if e is None:
            return None
        countries: dict[str, float] = dict(e.get("countries", {}))
        if "drives_on" in e:  # derived from countries.csv so the list stays in one place
            countries.update({iso: float(e["lr"]) for iso, c in self.countries.items() if c.drives_on == e["drives_on"]})
        band = e.get("lat_band")
        return Observation(
            key=key,
            label=str(e.get("label", key)),
            score=score,
            countries=countries,
            regions=tuple((r["lat"], r["lon"], r["sigma_km"], r["peak"]) for r in e.get("regions", [])),
            lat_band=(band["min"], band["max"], bool(band.get("abs", False)), band["lr"]) if band else None,
            other=float(e.get("other", 0.2)),
            box=box,
        )

    def from_detections(self, detections: list[Detection]) -> list[Observation]:
        """One observation per cue key (the strongest detection), ignoring non-location classes."""
        best: dict[str, Detection] = {}
        for d in detections:
            if d.key in NON_LOCATION_CLASSES or d.key not in self.entries:
                continue
            if d.key not in best or d.score > best[d.key].score:
                best[d.key] = d
        out = []
        for d in best.values():
            obs = self.observation(d.key, d.score, d.box)
            if obs is not None:
                out.append(obs)
        return out

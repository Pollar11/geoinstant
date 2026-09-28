"""Scene-text OCR via RapidOCR (optional)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    box: tuple[float, float, float, float]  # normalised x0, y0, x1, y1


class Ocr(Protocol):
    name: str

    def read(self, image: Image.Image) -> list[TextLine]: ...


class NullOcr:
    name = "none"

    def read(self, image: Image.Image) -> list[TextLine]:
        return []


class RapidOcr:
    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR()
        self.name = "rapidocr"

    def read(self, image: Image.Image) -> list[TextLine]:
        W, H = image.size
        result, _ = self.engine(np.asarray(image))
        lines: list[TextLine] = []
        for quad, text, score in result or []:
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            lines.append(TextLine(str(text), float(score), (min(xs) / W, min(ys) / H, max(xs) / W, max(ys) / H)))
        return lines


def load_ocr() -> Ocr:
    try:
        return RapidOcr()
    except ImportError:
        log.warning("rapidocr_onnxruntime not installed; scene-text cues come from the VLM only")
    except Exception:
        log.exception("Failed to initialise OCR")
    return NullOcr()

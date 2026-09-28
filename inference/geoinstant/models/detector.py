"""YOLO-layout ONNX detector for location cues ([1, 4+nc, N] output)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    key: str  # cue key, e.g. "joshua_tree", "bollard_fr", "person"
    score: float
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in [0, 1]

    @property
    def area(self) -> float:
        return max(0.0, self.box[2] - self.box[0]) * max(0.0, self.box[3] - self.box[1])


class Detector(Protocol):
    name: str

    def detect(self, image: Image.Image) -> list[Detection]: ...


class NullDetector:
    name = "none"

    def detect(self, image: Image.Image) -> list[Detection]:
        return []


def nms(boxes: NDArray[np.float32], scores: NDArray[np.float32], iou: float) -> list[int]:
    order = np.argsort(-scores)
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / (area_i + area_r - inter + 1e-9) < iou]
    return keep


def decode_yolo(
    output: NDArray[np.float32],
    classes: list[str],
    scale: float,
    pad: tuple[float, float],
    orig: tuple[int, int],
    conf: float = 0.25,
    iou: float = 0.5,
) -> list[Detection]:
    """Decode a ``[1, 4+nc, N]`` YOLO head (cx, cy, w, h in letterboxed input pixels)."""
    pred = output[0].T  # (N, 4+nc)
    cls_scores = pred[:, 4:]
    cls_id = cls_scores.argmax(axis=1)
    score = cls_scores[np.arange(len(pred)), cls_id]
    m = score >= conf
    if not m.any():
        return []
    pred, cls_id, score = pred[m], cls_id[m], score[m]
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    x0 = (cx - w / 2 - pad[0]) / scale
    y0 = (cy - h / 2 - pad[1]) / scale
    x1 = (cx + w / 2 - pad[0]) / scale
    y1 = (cy + h / 2 - pad[1]) / scale
    boxes = np.stack([x0, y0, x1, y1], axis=1)
    out: list[Detection] = []
    for c in np.unique(cls_id):  # class-wise NMS
        idx = np.where(cls_id == c)[0]
        for k in nms(boxes[idx], score[idx], iou):
            b = boxes[idx[k]]
            W, H = orig
            box = (
                float(np.clip(b[0] / W, 0, 1)),
                float(np.clip(b[1] / H, 0, 1)),
                float(np.clip(b[2] / W, 0, 1)),
                float(np.clip(b[3] / H, 0, 1)),
            )
            key = classes[int(c)] if int(c) < len(classes) else f"class_{int(c)}"
            out.append(Detection(key, float(score[idx[k]]), box))
    return sorted(out, key=lambda d: -d.score)


class OnnxYoloDetector:
    def __init__(self, path: Path, classes: list[str], providers: list[str], size: int = 640) -> None:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
        self.session = ort.InferenceSession(str(path), providers=[p for p in providers if p in available])
        self.input_name = self.session.get_inputs()[0].name
        self.classes = classes
        self.size = size
        self.name = f"onnx:{path.name}"

    def detect(self, image: Image.Image) -> list[Detection]:
        W, H = image.size
        s = self.size / max(W, H)
        nw, nh = round(W * s), round(H * s)
        canvas = Image.new("RGB", (self.size, self.size), (114, 114, 114))
        px, py = (self.size - nw) // 2, (self.size - nh) // 2
        canvas.paste(image.resize((nw, nh), Image.Resampling.BILINEAR), (px, py))
        x = (np.asarray(canvas, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self.session.run(None, {self.input_name: x})[0]
        return decode_yolo(np.asarray(out, dtype=np.float32), self.classes, s, (px, py), (W, H))


def load_detector(path: Path, classes_path: Path, providers: list[str]) -> Detector:
    if not path.exists():
        log.warning("No cue detector at %s; visual cue detection disabled", path)
        return NullDetector()
    try:
        classes = json.loads(classes_path.read_text()) if classes_path.exists() else []
        return OnnxYoloDetector(path, classes, providers)
    except Exception:
        log.exception("Failed to load detector %s", path)
        return NullDetector()

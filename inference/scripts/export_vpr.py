"""Export MegaLoc (DINOv2 transformer + attention aggregation, trained for place recognition) to ONNX.

    pip install torch onnx && python scripts/export_vpr.py   # → artifacts/vpr_encoder.onnx
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

# The service normalises inputs the CLIP way; undo that and apply ImageNet's inside the graph.
CLIP_MEAN, CLIP_STD = (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)
IMNET_MEAN, IMNET_STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


class Wrapped(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model
        t = lambda v: torch.tensor(v).view(1, 3, 1, 1)  # noqa: E731
        self.register_buffer("cm", t(CLIP_MEAN))
        self.register_buffer("cs", t(CLIP_STD))
        self.register_buffer("im", t(IMNET_MEAN))
        self.register_buffer("is_", t(IMNET_STD))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = ((x * self.cs + self.cm) - self.im) / self.is_
        e = self.model(x)
        return e / e.norm(dim=-1, keepdim=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=322, help="multiple of 14 (DINOv2 patch size)")
    ap.add_argument("--out", type=Path, default=Path("artifacts/vpr_encoder.onnx"))
    args = ap.parse_args()
    model = Wrapped(torch.hub.load("gmberton/MegaLoc", "get_trained_model").eval())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            model,
            torch.randn(1, 3, args.size, args.size),
            str(args.out),
            input_names=["pixel_values"],
            output_names=["embedding"],
            dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
            opset_version=17,
        )
    print(f"wrote {args.out} (set GEOINSTANT_VPR_ENCODER_SIZE={args.size})")


if __name__ == "__main__":
    main()

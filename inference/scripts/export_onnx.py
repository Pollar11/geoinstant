"""Export CLIP/SigLIP image + text encoders to ONNX (FP16)."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch


class VisionTower(torch.nn.Module):
    def __init__(self, encode) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.encode = encode

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        e = self.encode(pixels)
        return e / e.norm(dim=-1, keepdim=True)


def load(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    if args.hf:
        from transformers import CLIPModel, CLIPTokenizer

        m = CLIPModel.from_pretrained(args.hf).eval()
        return m.get_image_features, m.get_text_features, CLIPTokenizer.from_pretrained(args.hf)
    import open_clip

    m, _, _ = open_clip.create_model_and_transforms(args.model, pretrained=args.pretrained)
    m.eval()
    return m.encode_image, m.encode_text, open_clip.get_tokenizer(args.model)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="ViT-L-14")
    ap.add_argument("--pretrained", default="openai")
    ap.add_argument("--hf", help="Hugging Face CLIP checkpoint instead of open_clip")
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--out", type=Path, default=Path("artifacts/image_encoder.onnx"))
    ap.add_argument("--text-out", type=Path, default=Path("artifacts/text_encoder.onnx"))
    ap.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()

    encode_image, encode_text, tokenizer = load(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            VisionTower(encode_image),
            torch.randn(1, 3, args.size, args.size),
            str(args.out),
            input_names=["pixel_values"],
            output_names=["embedding"],
            dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
            opset_version=17,
        )
        if args.hf:
            tokens = tokenizer(["a photo"], return_tensors="pt", padding="max_length")["input_ids"]
        else:
            tokens = tokenizer(["a photo"])
        torch.onnx.export(
            VisionTower(encode_text),
            tokens,
            str(args.text_out),
            input_names=["input_ids"],
            output_names=["embedding"],
            dynamic_axes={"input_ids": {0: "batch"}, "embedding": {0: "batch"}},
            opset_version=17,
        )
    if args.fp16:
        import onnx
        from onnxconverter_common import float16

        for path in (args.out, args.text_out):
            m = onnx.load(str(path))
            onnx.save(float16.convert_float_to_float16(m, keep_io_types=True), str(path))
    print(f"wrote {args.out} and {args.text_out}")


if __name__ == "__main__":
    main()

"""Zero-shot cell prototypes from a CLIP text encoder."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from geoinstant.cells import CellTree
from geoinstant.config import get_settings
from geoinstant.gazetteer import Gazetteer

PROMPTS = [
    "A street view photo taken in {name}, {admin1}, {country}.",
    "A photo of the landscape near {name}, {country}.",
    "A photo taken in {admin1}, {country}.",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-encoder", type=Path, default=Path("artifacts/text_encoder.onnx"))
    ap.add_argument("--tokenizer", default="ViT-L-14", help="open_clip model name for the tokenizer")
    ap.add_argument("--logit-scale", type=float, default=100.0)
    ap.add_argument("--out", type=Path, default=Path("artifacts/cell_prototypes.npz"))
    args = ap.parse_args()

    import onnxruntime as ort
    import open_clip

    s = get_settings()
    gaz = Gazetteer(s.artifact(s.gazetteer_csv))
    cells = s.artifact(s.cells)
    tree = CellTree.load(cells, gaz) if cells.exists() else CellTree.from_gazetteer(gaz)
    tok = open_clip.get_tokenizer(args.tokenizer)
    sess = ort.InferenceSession(str(args.text_encoder), providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    country = tree.levels["country"]

    protos = []
    for i in range(len(tree)):
        cname = country.labels[country.parent[i]]
        texts = [p.format(name=tree.name[i], admin1=tree.admin1[i] or cname, country=cname) for p in PROMPTS]
        e = sess.run(None, {sess.get_inputs()[0].name: tok(texts).numpy()})[0]
        v = e.mean(0)
        protos.append(v / np.linalg.norm(v))
    np.savez(args.out, prototypes=np.stack(protos).astype(np.float16), logit_scale=np.float32(args.logit_scale))
    print(f"{len(protos)} prototypes → {args.out}")


if __name__ == "__main__":
    main()

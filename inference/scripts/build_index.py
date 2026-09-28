"""Build the retrieval index: python scripts/build_index.py --folder photos/ | --manifest train.csv [--faiss]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np

from geoinstant.cells import CellTree
from geoinstant.config import get_settings
from geoinstant.gazetteer import Gazetteer
from geoinstant.indexing import embed_images, from_folder, from_manifest, write_index
from geoinstant.models.embedder import load_embedder


def write_faiss(emb: np.ndarray, path: Path) -> None:
    import faiss  # type: ignore[import-not-found]

    n, d = emb.shape
    nlist = int(min(65536, max(64, 4 * np.sqrt(n))))
    quantizer = faiss.IndexFlatIP(d)
    index = faiss.IndexIVFPQ(quantizer, d, nlist, 64, 8, faiss.METRIC_INNER_PRODUCT)
    sample = emb[np.random.default_rng(0).choice(n, size=min(n, 256 * nlist), replace=False)]
    index.train(sample.astype(np.float32))
    index.add(emb.astype(np.float32))
    index.nprobe = 32
    faiss.write_index(index, str(path))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--folder", type=Path)
    src.add_argument("--manifest", type=Path)
    ap.add_argument("--out", type=Path, default=Path("artifacts/index.npz"))
    ap.add_argument("--faiss", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    gaz = Gazetteer(s.artifact(s.gazetteer_csv))
    cells = s.artifact(s.cells)
    tree = CellTree.load(cells, gaz) if cells.exists() else CellTree.from_gazetteer(gaz)
    embedder = load_embedder(s.artifact(s.image_encoder), s.image_encoder_size, s.onnx_providers, s.onnx_threads)
    items = from_folder(args.folder) if args.folder else from_manifest(args.manifest)
    emb, lat, lon = embed_images(items, embedder, s.work_size)
    write_index(args.out, emb, lat, lon, tree)
    if args.faiss:
        write_faiss(emb, args.out.with_suffix(".faiss"))
    print(f"indexed {len(lat)} photos with {embedder.name} → {args.out}")


if __name__ == "__main__":
    main()

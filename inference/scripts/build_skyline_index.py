"""Prebuild a skyline region for instant search.

python scripts/build_skyline_index.py --name alps --bbox 45.8 5.9 47.9 10.5 --spacing 0.5
"""

from __future__ import annotations

import argparse
import time

from geoinstant.config import get_settings
from geoinstant.skyline.dem import TileStore, mosaic
from geoinstant.skyline.service import area_km2, build_index, margin_box


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("SOUTH", "WEST", "NORTH", "EAST"), required=True)
    ap.add_argument("--spacing", type=float, default=0.5, help="km between viewpoints")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    s = get_settings()
    bbox = tuple(args.bbox)
    store = TileStore(s.artifact(s.dem_dir), s.dem_tile_url or None)
    t = time.time()
    dem = mosaic(store, *margin_box(bbox, s.skyline_max_km + 2), step=2)  # type: ignore[arg-type]
    ix = build_index(dem, bbox, args.spacing, s.skyline_max_km, args.workers)  # type: ignore[arg-type]
    out = s.artifact(s.skyline_dir) / f"{args.name}.npz"
    ix.save(out)
    print(f"{len(ix.lat)} viewpoints over {area_km2(bbox):,.0f} km² in {time.time() - t:.0f}s → {out}")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()

"""Accuracy (1/25/200/750/2500 km), calibration and latency on a manifest (path,lat,lon)."""

from __future__ import annotations

import argparse
import asyncio
import json
from itertools import pairwise
from pathlib import Path

import numpy as np

from geoinstant.config import get_settings
from geoinstant.geo import haversine_km
from geoinstant.indexing import from_manifest
from geoinstant.pipeline import Engine
from geoinstant.schemas import LocateResult, ResultEvent

THRESHOLDS_KM = [1, 25, 200, 750, 2500]


async def run(manifest: Path, vlm: str, limit: int | None) -> None:
    engine = Engine(get_settings())
    errors, conf, hit, latency = [], [], [], []
    items = list(from_manifest(manifest))[:limit]
    for n, it in enumerate(items, 1):
        final: LocateResult | None = None
        async for ev in engine.locate(it.path.read_bytes(), vlm):
            if isinstance(ev, ResultEvent) and ev.type in ("result", "refined"):
                final = ev.result
        if final is None:
            continue
        err = float(haversine_km(final.latitude, final.longitude, it.lat, it.lon))
        errors.append(err)
        conf.append(final.confidence / 100.0)
        hit.append(err * 1000.0 <= final.uncertainty_radius_m)
        latency.append(final.timings_ms.get("total", 0.0))
        if n % 100 == 0:
            print(f"{n}/{len(items)}")

    e = np.array(errors)
    report: dict[str, object] = {
        "n": len(e),
        "median_error_km": round(float(np.median(e)), 1),
        **{f"acc@{t}km": round(float((e <= t).mean()), 4) for t in THRESHOLDS_KM},
        "latency_ms": {q: round(float(np.percentile(latency, p)), 1) for q, p in (("p50", 50), ("p95", 95), ("p99", 99))},
    }
    # Reliability: bucket by stated confidence, compare with how often the truth was inside the radius.
    c, h = np.array(conf), np.array(hit, dtype=float)
    bins = np.linspace(0, 1, 11)
    table, ece = [], 0.0
    for lo, hi in pairwise(bins):
        m = (c >= lo) & (c < hi if hi < 1 else c <= hi)
        if m.any():
            gap = abs(c[m].mean() - h[m].mean())
            ece += m.mean() * gap
            table.append(
                {
                    "bin": f"{lo:.1f}-{hi:.1f}",
                    "n": int(m.sum()),
                    "stated": round(float(c[m].mean()), 3),
                    "observed": round(float(h[m].mean()), 3),
                }
            )
    report["calibration"] = {"ece": round(float(ece), 4), "table": table}
    report["hint"] = "stated > observed → raise GEOINSTANT_CALIBRATION_TEMPERATURE; stated < observed → lower it"
    print(json.dumps(report, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--vlm", choices=["off", "enrich", "blocking"], default="off")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    asyncio.run(run(args.manifest, args.vlm, args.limit))


if __name__ == "__main__":
    main()

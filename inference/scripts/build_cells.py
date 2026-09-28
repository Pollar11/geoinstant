"""Build semantic geocells: python scripts/build_cells.py --coords coords.csv (lat,lon)"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from geoinstant.gazetteer import Gazetteer
from geoinstant.geo import haversine_km, spherical_mean, to_unit_vectors


def kmeans_sphere(lat: np.ndarray, lon: np.ndarray, k: int, iters: int = 25, seed: int = 0) -> np.ndarray:
    x = to_unit_vectors(lat, lon)
    rng = np.random.default_rng(seed)
    c = x[rng.choice(len(x), size=k, replace=False)]
    for _ in range(iters):
        assign = np.argmax(x @ c.T, axis=1)
        for j in range(k):
            m = assign == j
            if m.any():
                v = x[m].sum(0)
                c[j] = v / np.linalg.norm(v)
    return np.argmax(x @ c.T, axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coords", type=Path, required=True)
    ap.add_argument("--gazetteer", type=Path, default=None)
    ap.add_argument("--target", type=int, default=400, help="photos per cell")
    ap.add_argument("--out", type=Path, default=Path("artifacts/cells.npz"))
    args = ap.parse_args()

    gaz = Gazetteer(args.gazetteer)
    with args.coords.open() as f:
        rows = [(float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)]
    lat = np.array([r[0] for r in rows])
    lon = np.array([r[1] for r in rows])

    town = np.empty(len(lat), dtype=np.int64)
    for s in range(0, len(lat), 2048):
        d = haversine_km(lat[s : s + 2048, None], lon[s : s + 2048, None], gaz.lat[None], gaz.lon[None])
        town[s : s + 2048] = d.argmin(1)

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, t in enumerate(town):
        groups[(gaz.iso2[t], gaz.admin1[t])].append(i)

    out: dict[str, list] = {k: [] for k in ("lat", "lon", "iso2", "admin1", "name", "scale_km", "count")}
    for (iso2, admin1), idx_list in groups.items():
        idx = np.array(idx_list)
        k = max(1, math.ceil(len(idx) / args.target))
        labels = kmeans_sphere(lat[idx], lon[idx], k) if k > 1 else np.zeros(len(idx), dtype=np.int64)
        for j in np.unique(labels):
            m = idx[labels == j]
            c_lat, c_lon = spherical_mean(lat[m], lon[m], np.ones(len(m)))
            d = haversine_km(c_lat, c_lon, lat[m], lon[m])
            near = int(np.argmin(haversine_km(c_lat, c_lon, gaz.lat, gaz.lon)))
            out["lat"].append(c_lat)
            out["lon"].append(c_lon)
            out["iso2"].append(iso2)
            out["admin1"].append(admin1)
            out["name"].append(gaz.names[near])
            out["scale_km"].append(max(float(np.percentile(d, 68)), 1.0))
            out["count"].append(len(m))

    count = np.array(out["count"], dtype=np.float64)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        args.out,
        lat=np.array(out["lat"]),
        lon=np.array(out["lon"]),
        iso2=np.array(out["iso2"]),
        admin1=np.array(out["admin1"]),
        name=np.array(out["name"]),
        scale_km=np.array(out["scale_km"]),
        log_prior=np.log(count / count.sum()),
    )
    print(f"{len(count)} cells from {len(lat)} photos → {args.out}")


if __name__ == "__main__":
    main()

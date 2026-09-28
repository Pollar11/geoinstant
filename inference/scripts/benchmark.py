"""Latency benchmark: python scripts/benchmark.py --url http://localhost:8000"""

from __future__ import annotations

import argparse
import asyncio
import io
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image


def payloads(folder: Path | None, n: int) -> list[bytes]:
    rng = np.random.default_rng(0)
    base: list[Image.Image] = []
    if folder:
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        base = [Image.open(p).convert("RGB") for p in sorted(folder.iterdir()) if p.suffix.lower() in exts]
    out = []
    for i in range(n):
        if base:
            img = base[i % len(base)].copy()
            img.thumbnail((1024, 1024))
        else:
            img = Image.fromarray(rng.integers(0, 255, (768, 1024, 3), dtype=np.uint8))
        img.putpixel((0, 0), (i % 256, (i // 256) % 256, 7))  # defeat the result cache
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        out.append(buf.getvalue())
    return out


async def main_async(args: argparse.Namespace) -> None:
    bodies = payloads(args.images, args.requests)
    lat: list[float] = []
    server: list[float] = []
    statuses: dict[int, int] = {}
    sem = asyncio.Semaphore(args.concurrency)
    headers = {"Content-Type": "image/jpeg", **({"X-API-Key": args.key} if args.key else {})}
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        await client.post("/v1/locate?vlm=off", content=bodies[0], headers=headers)  # warm-up

        async def one(body: bytes) -> None:
            async with sem:
                t = time.perf_counter()
                r = await client.post("/v1/locate?vlm=off", content=body, headers=headers)
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
                if r.status_code == 200:
                    lat.append((time.perf_counter() - t) * 1000)
                    server.append(r.json()["timings_ms"].get("total", 0.0))

        t0 = time.perf_counter()
        await asyncio.gather(*(one(b) for b in bodies))
        wall = time.perf_counter() - t0
    a = np.array(lat)
    print(f"status codes: {statuses}  (raise GEOINSTANT_RATE_LIMIT_* on the server for load tests)")
    if not len(a):
        return
    print(f"ok={len(a)} concurrency={args.concurrency} throughput={len(a) / wall:.1f} req/s")
    print(f"client latency ms  p50={np.percentile(a, 50):.0f}  p95={np.percentile(a, 95):.0f}  p99={np.percentile(a, 99):.0f}")
    if server:
        s = np.array(server)
        print(f"server pipeline ms p50={np.percentile(s, 50):.0f}  p95={np.percentile(s, 95):.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--images", type=Path)
    ap.add_argument("--requests", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--key")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()

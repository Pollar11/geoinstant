# Design

## Pipeline

```
device: EXIF GPS? ── yes → /v1/reverse → exact result (photo not uploaded)
        no → downscale 1024 px → POST /v1/locate/stream
server: metadata → decode ─┬─ embedding → classifier + retrieval → partial
                           ├─ cue detector
                           ├─ OCR → text cues
                           └─ Claude (async) ───────────────┐
                           fusion → final (≤ 750 ms) ─── fusion → refined
```

Each stage returns a log-likelihood over leaf geocells. They are fused as follows:

```
log p(c) = [log prior(c) + Σ wᵢ ℓᵢ(c)] / T
```

- **Hierarchy:** continent → country → region → city. Report the deepest level with ≥ 30% mass and ≥ 1.5× lift over its prior.
- **Coordinate:** mean-shift over retrieved photos, VLM point and cue regions. The radius is the 68th-percentile spread.
- **Honesty:** one distinctive cue (e.g. a Joshua tree) gives a confident *region*, not a street. Street level needs retrieval, readable text or GPS.

## Skyline matching

```
photo → ridge line (auto or hand-traced) → elevation angles per lens guess (30–75°)
terrain (SRTM 30 m) → 360° skyline from every viewpoint on a 0.5 km grid (terrain < 0.5 km ignored)
FFT match over direction × lens → top 5 → refine on a finer grid → position, direction, lens
```

- **Confidence:** how clearly the best match beats the runner-up. Near-ties mean the skyline is ambiguous.
- **Test on real Alps terrain, 24 random views, 500 km² area:**
  - 50% of views were found within 1 km.
  - Every search reporting ≥ 50% confidence was correct (8 of 8).
- **Limits:** the ridge must be distant (> 0.5 km) and distinctive, and you need a rough search area.

## Street match

Visual place recognition, album only (`streetmatch/`):

1. List street images in the bbox from Mapillary (400 m tiles; full tiles are split) and Panoramax (STAC search, paged). A failing source is skipped.
2. Embed thumbnails, cached by image id in SQLite. The embedder is `vpr_encoder.onnx` (MegaLoc: DINOv2 ViT + attention aggregation, whole view, no crop) if present, else the main encoder.
3. Take the top 60 by cosine similarity.
4. Verify each on ~1024 px (CLAHE for faded prints), then count RANSAC fundamental-matrix inliers:
   - `matcher.onnx` (LightGlue, a transformer matcher that uses self- and cross-attention) if present
   - else SIFT + ratio test + mutual best match
5. Mark it verified at ≥ 30 inliers and ≥ 1.5× the best candidate more than 60 m away (neighbouring frames of the same spot don't count against it).
6. The house: take OSM addresses within 80 m (Overpass) and pick the nearest one within ±35° of the camera heading, else the nearest overall (shown as "Nearest address").

A verified match becomes the photo's exact location: camera position, heading and the address it faces.

**LightGlue model:** export the end-to-end extractor + LightGlue pipeline with [LightGlue-ONNX](https://github.com/fabio-sim/LightGlue-ONNX).
- Use batch 2 and a fixed size, e.g. 768×1024.
- Use DISK or ALIKED features. SuperPoint weights are non-commercial.
- Save it as `artifacts/matcher.onnx`.
- Expected I/O: `images (2,C,H,W)` → `keypoints (2,N,2)`, `matches (M,3)`, `scores (M)`.

## Automatic search (album)

After analysis and investigation, the worker calls `archive/auto.py` for any photo that isn't pinned:

- **Lead:** the investigation's point at city level or finer (else the quick answer at city level with ≥ 50% confidence).
- **Mountain scenes:** `SkylineService.search` over a 40 × 40 km box around the lead. Kept as a pin at confidence ≥ 0.5.
- **Outdoor scenes:** `StreetMatchService.search_around`:
  - 1 km cells in rings (3 × 3 km for a street-level lead, 5 × 5 km for a city)
  - each ring is embedded, then its top 60 unverified views are checked
  - stops at the first verified match
- **Jobs:** one runs at a time. Progress shows in the album as "searching".

## Models

| Stage | Default | Licence |
| --- | --- | --- |
| Encoder | CLIP ViT-L/14 or SigLIP (ONNX, TensorRT FP16) | MIT / Apache-2.0 |
| Classifier | linear head, haversine-smoothed labels, semantic geocells | own |
| Retrieval | FAISS IVF-PQ over geotagged photos (OSV-5M, Mapillary, YFCC) | MIT; data CC-BY(-SA) |
| Cue detector | RT-DETR / D-FINE (YOLO is AGPL) | Apache-2.0 |
| OCR | PP-OCR via RapidOCR | Apache-2.0 |
| VLM | Claude `claude-opus-5` (optional) | proprietary API |
| Gazetteer | GeoNames | CC-BY 4.0 |
| Map | MapLibre + OpenFreeMap | BSD-3; OSM ODbL |

Do not use Google Street View imagery (its ToS forbid it). StreetCLIP is non-commercial. HEIC decoding (libheif) is LGPL and HEVC is patent-encumbered.

## Latency budget (p50, warm)

| Step | ms |
| --- | --- |
| EXIF + downscale (device) | 60 |
| Upload ~150 KB | 150 |
| Decode | 15 |
| Encoder ‖ detector ‖ OCR (L4 GPU) | 50 |
| Classifier + retrieval | 15 |
| Fusion + reverse geocode | 5 |
| Response | 60 |
| **Total** | **~350–550** |

The VLM (2–8 s) streams later as `refined`. Late stages are dropped at the deadline. Results are cached by image hash. Keep ≥ 1 warm GPU replica so users never hit a cold start. These are estimates: measure with `scripts/benchmark.py`.

## Deploy

- **Inference:** `inference/Dockerfile` (GPU: `--build-arg BASE=nvcr.io/nvidia/tensorrt:24.08-py3 --build-arg ORT=onnxruntime-gpu`), or `modal deploy modal_app.py`.
- **Web:** Vercel (root `web/`) or `web/Dockerfile`. Set `INFERENCE_URL` and `INFERENCE_API_KEY`.
- **Keys:** set `GEOINSTANT_PROXY_API_KEYS` (web) and `GEOINSTANT_API_KEYS` (API clients). Use a Redis or edge rate limiter when running multiple replicas.
- **Album:** SQLite, a background queue and in-memory street-match jobs, so run it as one instance (`docker compose`) with the `archive` and `cache` volumes. Don't enable it on the autoscaled Modal deployment.

## Feedback loop

`/v1/feedback` stores the embedding and the corrected lat/lon (plus the image only with consent) as `pending_review`. After review, rows are added to the index. Retrain the head weekly and promote only if `scripts/evaluate.py` shows no regression.

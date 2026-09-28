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

## Feedback loop

`/v1/feedback` stores the embedding and the corrected lat/lon (plus the image only with consent) as `pending_review`. After review, rows are added to the index. Retrain the head weekly and promote only if `scripts/evaluate.py` shows no regression.

# API

Auth: `X-API-Key` (when keys are configured). Errors: 400 · 401 · 413 · 415 · 422 · 429 (`Retry-After`).

## POST /v1/locate

Body: the raw image (`Content-Type: image/*`) or multipart with an `image` field. Query: `vlm=off|enrich|blocking`.

```bash
curl -X POST 'localhost:8000/v1/locate?vlm=off' -H 'Content-Type: image/jpeg' --data-binary @photo.jpg
```

```json
{
  "request_id": "88567db8…",
  "stage": "final",
  "source": "visual",
  "latitude": 38.7223,
  "longitude": -9.1393,
  "uncertainty_radius_m": 10000,
  "confidence": 75.1,
  "resolution": "city",
  "place": { "name": "Lisbon", "country": "Portugal", "country_code": "PT", "display_name": "Lisbon, Portugal" },
  "hierarchy": [ { "level": "country", "name": "Portugal", "probability": 0.9 } ],
  "candidates": [ { "name": "Porto, Portugal", "probability": 0.15 } ],
  "evidence": [ { "source": "retrieval", "label": "Visually similar geo-tagged photos", "likelihood_ratio": 107, "direction": "supports" } ],
  "regions": [ { "box": [0.1, 0.2, 0.3, 0.6], "label": "Joshua tree", "score": 0.92, "source": "cue" } ],
  "explanation": "Best estimate: Lisbon, Portugal (city-level, 75% confidence)…",
  "timings_ms": { "decode": 4.8, "total": 31 },
  "mode": "production",
  "privacy": { "coarsened": false }
}
```

- `resolution`: one of `exact`, `street`, `city`, `region`, `country`, `continent`, `world`.
- `confidence`: the percentage chance the answer is right at that resolution.

## POST /v1/locate/stream

Same input. Returns Server-Sent Events: `stage`* → `partial`? → `result` → `refined`? → `done` (or `error`).

## POST /v1/skyline

Multipart fields: `image`; `bbox` = `[south, west, north, east]`; optional `trace` = `[[x, y], …]` (normalised image coordinates).

```json
{
  "status": "ok",
  "confidence": 74.2,
  "viewpoints": 2115,
  "candidates": [
    { "latitude": 45.98534, "longitude": 7.787508, "elevation_m": 3049, "azimuth_deg": 162, "fov_deg": 40, "fit_error": 0.08, "match": 1.0, "place": { "display_name": "…" } }
  ],
  "profile": [[0.01, 0.31, 1.0]],
  "heat": [[45.98, 7.78, 1.0]]
}
```

`status` is one of `ok`, `too_flat`, `no_area`, `area_too_large`, `no_data`. `GET /v1/skyline/coverage` lists the prebuilt regions.

## GET /v1/reverse?lat=&lon=

Returns a `Place` for a coordinate.

## POST /v1/feedback

```json
{ "request_id": "…", "latitude": 59.91, "longitude": 10.75, "was_correct": false, "consent_store_image": false }
```

Returns `{ "accepted": true, "feedback_id": "…", "stored_image": false, "stored_embedding": true }`

## GET /healthz

Returns `{ "status": "ok", "mode": "dev|partial|production", "models": {…} }`

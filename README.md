# GeoInstant

Upload a photo and get its likely location: lat/lon, place name, confidence, uncertainty radius and the evidence used.

- **inference/**: FastAPI pipeline (EXIF → cues + OCR + retrieval + geocell classifier → fusion → optional Claude refinement) and mountain skyline matching
- **web/**: Next.js 15 app (upload, live progress, map, feedback, PWA)
- **docs/**: [DESIGN.md](docs/DESIGN.md) · [API.md](docs/API.md)

## Run

```bash
docker compose up --build        # http://localhost:3300
```

Or run each part locally:

```bash
cd inference && pip install -e ".[dev]" && pytest
GEOINSTANT_VLM_MODE=off uvicorn geoinstant.main:app --port 8000

cd web && npm ci && INFERENCE_URL=http://localhost:8000 npm run dev
```

## Deploy (no computer needed)

On [Render](https://render.com), from a phone or browser:

1. Click **New → Blueprint** and pick this repo. `render.yaml` sets up everything.
2. Fill in `ANTHROPIC_API_KEY`, `GEOINSTANT_MAPILLARY_TOKEN` (optional) and `ARCHIVE_PASSWORD`.
3. Click **Apply**. Open the `geoinstant` URL, then `/album`.

What it creates:
- `geoinstant-engine`: a private location engine with a 10 GB disk for the album and caches
- `geoinstant`: the public website
- Cost: about $32 a month (Standard + Starter plans).

Vercel can host only the website, not the engine: the engine needs a disk and long background searches.

## Models

Without artifacts the service runs in **dev mode**: EXIF GPS, text cues and exact-duplicate retrieval only. Build real artifacts into `inference/artifacts/`:

```bash
pip install -e ".[build,faiss,ocr]"
python scripts/fetch_gazetteer.py
python scripts/export_onnx.py
python scripts/build_cells.py --coords coords.csv
python scripts/build_prototypes.py
python scripts/build_index.py --manifest train.csv --faiss
python scripts/evaluate.py --manifest test.csv
```

## Investigation

After the quick answer, Claude investigates like a GeoGuessr pro. It zooms into details, searches the web for any names or text it reads, and looks up addresses on OpenStreetMap. Then it reports:

> **This photo was taken at** Taverna Nikos, Oia, Santorini · street level · 82%
> *1. Greek menu with drachma prices → Greece before 2002 · 2. Awning "Taverna Nikos" → … · 3. Web search → Oia*

**The place today** shows recent street photos of the spot (set `MAPILLARY_TOKEN`), plus Street View and satellite links.

- **Exact spots** need something identifying: a name, a sign, text, a landmark, a distinctive view. Plain interiors usually stay at country or region level.
- **Cost:** each investigation makes several Claude calls with web search.

## Family album

The album lives at `/album` and is protected by a password. Drop in a whole folder and every photo gets:

- A clue board: scene, decade, clues and top 3 guesses (needs `ANTHROPIC_API_KEY`).
- GPS, if the file has it.
- The skyline tool, for photos with mountains.

Select photos from the same trip and click **Link as same place/event**; once one is located, the rest inherit it. Click the map to set a location yourself.

```bash
ARCHIVE_PASSWORD=… ANTHROPIC_API_KEY=… docker compose up --build   # http://localhost:3300/album
```

## Automatic exact search (album)

Every album photo goes through this automatically. You don't need to zoom the map.

1. **Investigation:** clues, web search and map lookup give a lead (a street, neighbourhood or town).
2. **Mountains visible:** skyline match within 20 km of the lead. It pins the photo only at ≥ 50% confidence.
3. **Outdoors:** street match in 1 km rings outward from the lead: 3 × 3 km for a street lead, up to 5 × 5 km for a town. It stops at the first verified match.
4. **Result:** the photo is pinned only on a verified match. Otherwise the album shows the lead and why the photo couldn't be pinned.

Indoor photos and leads vaguer than a town (region, country) aren't searched automatically. Add what the family remembers in the notes and click **Analyse again**.

## Street match (exact spot)

For outdoor photos of streets, houses, shops or squares, the GeoSpy approach. No sign or landmark is needed. It runs automatically (above), or by hand:

1. Open the photo in the album and zoom the map to the suspected area (≤ 6 km²).
2. Click **Search this map area**.

Your photo is compared with every street photo there, then the best 60 are checked point by point (windows, corners, rooflines). A verified match (≥ 30 matching points, well ahead of anywhere else) pins the photo to the exact spot, shows then and now side by side, and names the house the camera faces (OpenStreetMap address).

- **Sources:** Panoramax (open, no token) and Mapillary (`GEOINSTANT_MAPILLARY_TOKEN`). Real-estate sites (Zillow, Redfin) are not used: no open API, and scraping breaks their terms.
- **Models:** works out of the box with SIFT. For much better results on old photos, add the transformer models:
  - `python scripts/export_vpr.py` → `artifacts/vpr_encoder.onnx` (MegaLoc, finds similar views)
  - LightGlue → `artifacts/matcher.onnx` (checks point by point, see [DESIGN.md](docs/DESIGN.md#street-match))
- **Limits:** the area needs street photo coverage, and the place must still look similar.

## Mountain skyline matching

For photos with mountains in the background:

1. Zoom the map to the suspected area (≤ 5,000 km²).
2. Optionally trace the ridge on the photo.
3. Click **Search map area**.

It returns camera position, facing direction and lens, found by matching the ridge against SRTM terrain. The first search in an area downloads terrain and takes about 15 s; later searches take about 1–3 s. Prebuild large regions for instant search:

```bash
python scripts/build_skyline_index.py --name alps --bbox 45.8 5.9 47.9 10.5
```

## Privacy

- GPS is read on the device, so those photos are never uploaded.
- Uploads are processed in memory and nothing is stored without consent.
- Precision is capped at city level when people are the subject.

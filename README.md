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

## Family album

The album lives at `/album` and is protected by a password. Drop in a whole folder and every photo gets:

- A clue board: scene, decade, clues and top 3 guesses (needs `ANTHROPIC_API_KEY`).
- GPS, if the file has it.
- The skyline tool, for photos with mountains.

Select photos from the same trip and click **Link as same place/event**; once one is located, the rest inherit it. Click the map to set a location yourself.

```bash
ARCHIVE_PASSWORD=… ANTHROPIC_API_KEY=… docker compose up --build   # http://localhost:3300/album
```

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

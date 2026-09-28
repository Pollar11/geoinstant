# GeoInstant

Upload a photo and get its likely location: lat/lon, place name, confidence, uncertainty radius and the evidence used.

- **inference/**: FastAPI pipeline (EXIF → cues + OCR + retrieval + geocell classifier → fusion → optional Claude refinement)
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

## Privacy

- GPS is read on the device, so those photos are never uploaded.
- Uploads are processed in memory and nothing is stored without consent.
- Precision is capped at city level when people are the subject.

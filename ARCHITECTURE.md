# Architecture
```mermaid
flowchart LR
  A[Lab report PDF / scan / CSV] --> B[extract.py: text layer + Tesseract OCR]
  B --> C[Clinician review: edit / approve / reject]
  C --> D[(SQLite: measurements, documents, fields)]
  E[Time-series CSV] --> F[Validate: timestamps, ranges, duplicates, gaps]
  F --> G[(SQLite: timeseries, audit)]
  D --> H[twin.py update: fuse + derive features]
  G --> H
  H --> I[(snapshots: version, state, diff, sources)]
  I --> J{Inputs sufficient?}
  J -- yes --> K[model: 2 h glucose-excursion score] --> L[(predictions, outcomes)]
  J -- no --> M[Prediction unavailable + reasons]
  I --> N[Web UI + PDF report]
  L --> N
```
Layers: `naari/twin.py` (engine, schema), `naari/model.py` (task, training, metrics), `naari/extract.py`, `naari/records.py`, `naari/server.py` (stdlib HTTP + JSON API, cookie sessions), `web/index.html` (single-page UI, inline SVG charts). Designed so the engine can later sit behind FastAPI/React unchanged.

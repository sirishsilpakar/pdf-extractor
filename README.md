# PDF TextExtract

> A PDF text extraction pipeline with fully paginated REST API, OCR strategy pattern, SHA-256 deduplication, server-side rendered state, and an independent CLI module.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Quick Start](#quick-start)
4. [CLI Usage](#cli-usage)
5. [API Reference](#api-reference)
6. [Configuration](#configuration)
7. [Project Structure](#project-structure)
8. [Development](#development)
9. [Testing](#testing)

---

## Features

| Capability | Detail |
|---|---|
| **Memory-safe uploads** | Files streamed to disk in 1 MB chunks so that RAM stays stable regardless of file count or size |
| **SHA-256 deduplication** | Browser hashes first 64 KB client-side, already-extracted files are **never re-uploaded** |
| **Server-side pagination** | All four data views (job files, results, extracted files, search) are paginated API calls |
| **OCR Strategy pattern** | Startegy patter to allow swapping OCR engines without touching the pipeline code, (default: Tesseract) |
| **Three-layer architecture** | `core/` <-> `services/` <-> `api/` / `cli/` |
| **Independent CLI** | `pdf-extract run <dir>` works without a running server |
| **Versioned REST API** | All endpoints under `/api/v1/` with full OpenAPI docs at `/docs` |
| **Real-time SSE** | Compact metadata-only events, file list fetched separately via pagination |
| **Thread-safe SQLite** | Repository pattern with WAL mode and non-destructive schema migration |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Entry points                         │
│  CLI (cli/main.py)          API (api/app.py + server.py) │
└───────────────┬─────────────────────────┬────────────────┘
                │                         │
                │      DI via Depends()   │
                ▼                         ▼
┌──────────────────────────────────────────────────────────┐
│                  Services layer                          │
│  services/ocr/   (Strategy: OCREngine ABC)               │
│  services/hasher.py  (SHA-256 fingerprinting)            │
│  db/repository.py    (Repository pattern, SQLite)        │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│                    Core layer                            │
│  core/pipeline.py   (hash dedup, multiprocess pool)      │
│  core/worker.py     (per-file extraction, OCR injected)  │
│  core/events.py     (typed StrEnum events + dataclasses) │
│  core/transform.py  (text normalisation utilities)       │
└──────────────────────────────────────────────────────────┘
```

**Design patterns used:** Strategy (OCR), Repository (DB), Observer (SSE broadcast), Factory (`create_app`), Dependency Injection (`Depends`).

---

## Quick Start

### Prerequisites

```bash
# macOS
brew install tesseract tesseract-lang

# Debian/Ubuntu
apt-get install tesseract-ocr tesseract-ocr-deu
```

Python ≥ 3.9 required.

### Install

```bash
git clone <repo-url>
cd pdf-extractor

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### Run the server

```bash
python server.py
# -> http://localhost:8080
# -> http://localhost:8080/docs  (Swagger UI)
```

Open `http://localhost:8080` in your browser to use the dashboard.

---

## CLI Usage

```
pdf-extract --help

Commands:
  run      Run the extraction pipeline on a directory
  status   Show database extraction statistics
  reset    Wipe all pipeline state from the database
  engines  List registered OCR engines and availability
```

### Examples

```bash
# Extract a directory tree
pdf-extract run ./my-pdfs/

# Custom output dir, force reprocess, custom DB
pdf-extract run ./my-pdfs/ --output-dir /data/output --force --db /data/state.db

# Fast mode (lower DPI and shorter timeouts)
pdf-extract run ./my-pdfs/ --fast

# OCR-free (direct text extraction only)
pdf-extract run ./my-pdfs/ --no-ocr

# Check DB stats
pdf-extract status

# List available OCR engines
pdf-extract engines
```

---

## Service Level Agreement (SLA) & Extraction Boundaries

**Scope of Operations:** This extraction pipeline is specifically engineered for processing formal financial documentation in the German language (e.g., quarterly reports, balance sheets).

**1. Supported Data Profiles (In Scope)**
*   **Structured Digital PDFs:** Native, digitally generated PDF documents with machine readable text layers.
*   **Scanned Financial Documents:** Flattened scans of financial reports requiring Optical Character Recognition (OCR) processing via Tesseract.

**2. Formatting & Structural Degradation**
*   **Positional Autonomy:** The pipeline extracts raw textual payloads. Physical layout positioning, font formatting, and typographical styling will **not** be preserved.
*   **Table Data Constraints:** While tabular data is successfully extracted into text, specific row/column structural integrity, bounds, and cell layouts are not strictly guaranteed to map to the output identically.

**3. Unsupported Elements (Out of Scope)**
*   **Graphical Elements:** Illustrative diagrams, visual blocks, and standalone images containing no textual payload are ignored.
*   **Degraded Inputs:** Heavily blurred scans, low DPI legacy documents, and physically damaged records will yield degraded confidence scores and may trigger extraction failures.
*   **Non-Standard Inputs:** Handwritten notes, cursive inscriptions, or unrecognized encodings are not supported and are subject to failure.

---

## API Reference

Interactive docs always available at **`/docs`** (Swagger) and **`/redoc`** (ReDoc) when the server is running.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/upload` | Stream upload one or more PDFs (chunked, 1 MB/chunk) |
| `POST` | `/api/v1/upload/check-hashes` | Bulk hash check to see which files are already extracted |
| `POST` | `/api/v1/job/start` | Start the extraction pipeline for uploaded file IDs |
| `POST` | `/api/v1/job/cancel` | Cancel the running job |
| `GET`  | `/api/v1/job/status` | Compact job metadata (no files list) |
| `GET`  | `/api/v1/job/files?page=1&size=50` | Paginated active job file list |
| `GET`  | `/api/v1/results?page=1&size=50` | Paginated extraction results from DB |
| `GET`  | `/api/v1/results/{id}` | Single result with full text content |
| `DELETE` | `/api/v1/results/{id}` | Remove a DB record (keeps .txt on disk) |
| `GET`  | `/api/v1/files?page=1&size=50` | Paginated on disk `.txt` file listing |
| `DELETE` | `/api/v1/files/{rel_path}` | Delete a `.txt` output file from disk |
| `GET`  | `/api/v1/search?q=term&page=1&size=20` | Full text search across extracted documents |
| `GET`  | `/api/events` | SSE stream for real-time progress |

### Upload flow

```
Browser                             Server
  │                                    │
  │── hash first 64 KB of each file ──▶│
  │◀── POST /check-hashes ────────────▶│ (already extracted? skip upload)
  │                                    │
  │── POST /upload (stream, 1 MB/ch) ─▶│ (only unprocessed files)
  │◀── [{file_id, content_hash}] ──────│
  │                                    │
  │── POST /job/start {file_ids} ─────▶│
  │◀── {ok: true} ─────────────────────│
  │                                    │
  │── GET /api/events (SSE) ──────────▶│ real-time progress
  │── GET /job/files?page=1 ───────────│ poll for file status table
```

---

## Configuration

All settings are controlled via **environment variables**:

| Variable | Default | Description |
|---|---|---|
| `WORKERS` / `WORKERS_OVERRIDE` | `cpu_count // 2` | Parallel worker processes |
| `OCR_DPI` | `200` | Rendering DPI for OCR |
| `TESSERACT_LANG` | `deu` | Tesseract language(s) |
| `TESSERACT_PAGE_TIMEOUT_SECONDS` | `30` | Per-page OCR timeout |
| `OCR_ON_IMAGE_AREA_THRESHOLD` | `0.15` | Image coverage ratio to trigger OCR |
| `EXTRACTOR_DB_PATH` | `state.db` | SQLite database path |
| `UPLOAD_DIR` | `uploads/` | Where uploaded files are stored |
| `OUTPUT_DIR` | `extracted_files/` | Where `.txt` outputs are written |
| `UPLOAD_CHUNK_SIZE` | `1048576` | Upload chunk size in bytes (1 MB) |
| `PAGE_SIZE` | `50` | Default API page size |
| `MAX_PAGE_SIZE` | `200` | Maximum API page size |
| `OMP_THREAD_LIMIT` | `1` | OpenMP threads per worker (prevents thread storms) |

---

## Project Structure

```
pdf-extractor/
├── core/                    # Zero dependency domain logic
│   ├── events.py            # StrEnum events + typed dataclasses
│   ├── pipeline.py          # Multiprocess pipeline, hash dedup
│   ├── worker.py            # Per-file extraction, OCR injected
│   └── transform.py         # Text normalisation utilities
│
├── services/                # Infrastructure implementations
│   ├── hasher.py            # SHA-256 file fingerprinting
│   └── ocr/
│       ├── base.py          # OCREngine abstract base + OCRResult
│       ├── tesseract.py     # TesseractOCREngine (picklable)
│       └── registry.py      # Engine registry & get_default_engine()
│
├── db/
│   └── repository.py        # DatabaseRepository (SQLite, thread-safe)
│
├── api/
│   ├── app.py               # create_app() factory
│   ├── lifespan.py          # Startup / shutdown context
│   ├── job_manager.py       # JobManager class (O(1) dispatch table)
│   ├── sse.py               # SSE broadcast utility
│   └── v1/
│       ├── schemas.py       # All Pydantic request/response models
│       ├── deps.py          # FastAPI Depends() providers
│       ├── router.py        # Versioned router aggregator
│       └── routers/
│           ├── upload.py    # POST /upload, POST /check-hashes
│           ├── jobs.py      # start / cancel / status / files
│           ├── results.py   # Paginated DB results
│           ├── files.py     # Paginated on-disk .txt listing
│           └── search.py    # Full-text search
│
├── cli/
│   └── main.py              # click CLI: run / status / reset / engines
│
├── ui/
│   ├── index.html           # Single-page dashboard
│   ├── app.js               # API client, SSE, pagination, hash check
│   └── style.css            # Design system
│
├── tests/
│   ├── conftest.py
│   ├── core/test_transform.py
│   ├── services/test_hasher.py
│   ├── services/test_ocr_strategy.py
│   ├── db/test_repository.py
│   └── api/test_upload.py
│
├── config.py                # All constants + env-var overrides
├── server.py                # Uvicorn launcher
├── main.py                  # Backward-compatible CLI
├── transform.py             # Backward-compatible import
└── pyproject.toml
```

---

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Format
black .

# Run server with auto-reload
uvicorn api.app:app --reload --port 8080

# Run tests
pytest tests/ -v
```

### Adding a new OCR engine

```python
# 1. Subclass OCREngine
from services.ocr.base import OCREngine, OCRResult

class MyEngine(OCREngine):
    @property
    def name(self) -> str: return "my-engine"
    def is_available(self) -> bool: return True
    def run(self, image, **kwargs) -> OCRResult:
        text = my_ocr_library.extract(image)
        return OCRResult(text=text, engine=self.name)

# 2. Register it once on startup
from services.ocr.registry import register
register(MyEngine())
```

No other code needs to change, the pipeline picks it up automatically.

---

## Testing

```bash
# All unit tests (no external services required)
pytest tests/core/ tests/services/ tests/db/ -v

# API integration tests (requires httpx)
pytest tests/api/ -v

# Full suite
pytest tests/ -v
```

Current coverage: **30 tests, 0 failures**.

---

## License

MIT © 2026 Sanjay Gurung, Sapna Luthra, Sirish Silpakar — Philipps-Universität Marburg
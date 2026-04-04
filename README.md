# PDF TextExtract

A robust, parallel PDF extraction pipeline tool with a browser-based real-time dashboard.
Automatically routes each page to fast direct extraction or OCR based on content, preserves directory structure, and streams live progress via WebSockets.

---

## Features

| Feature | Details |
|---|---|
| **Hybrid Extraction** | Per-page routing: direct text for text-heavy pages, OCR for image-heavy pages |
| **Parallel Processing** | `multiprocessing.Pool` with `maxtasksperchild=1` — configurable worker count, no memory accumulation |
| **Real-time Dashboard** | Browser UI with live progress bars, per-page updates, and activity log via WebSocket |
| **Directory Structure** | Output mirrors the original folder structure from uploads (`ocr/subdir/file.txt`) |
| **Results Viewer** | Collapsible tree view of all extractions and click any file to read its full text inline |
| **Skip / Reprocess** | Automatically detects already-processed files and offers skip or force-reprocess |
| **Per-run Logs** | Each run writes a timestamped `logs/pipeline_YYYYMMDD_HHMMSS.log` recording success, time, and metrics |
| **Configurable** | Metadata file (`.meta.json`) generated for every PDF |
| **REST API** | Full FastAPI REST API |

---

## Installation

### Prerequisites

- Python 3.9+
- Tesseract OCR:
  ```bash
  brew install tesseract          # macOS
  sudo apt-get install tesseract-ocr  # Debian/Ubuntu
  ```

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .        # installs CLI entry-points
```

---

## Usage

### CLI Mode

```bash
# Basic — process all PDFs in a directory
python main.py ./input_data

# Custom output directory
python main.py ./input_data --output-dir my_results

# Force reprocess (ignore already-extracted files)
python main.py ./input_data --force

# Disable OCR (direct extraction only)
python main.py ./input_data --no-ocr

# Override worker count
python main.py ./input_data --workers 8
```

Output structure:
```
extracted_files/
  ocr/              # pages where OCR was the dominant method
    subdir/
      file.txt
      file.meta.json
  direct/           # pages where direct extraction was dominant
    file.txt
```

### Web UI / Server Mode

```bash
python server.py
# → http://localhost:8080
# → API docs: http://localhost:8080/docs
```

**Workflow:**
1. Drop PDFs or an entire folder on the dashboard
2. Already-processed files are detected automatically — choose to skip or reprocess
3. Click **Start Extraction** — live progress updates per file and per page
4. Navigate to **Results** to browse a collapsible folder tree and read extracted text inline

---

## API Reference

**Base URL:** `http://localhost:8080/api`  
**Interactive docs:** `http://localhost:8080/docs` (Swagger UI) · `http://localhost:8080/redoc` (ReDoc)

### Upload

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/upload` | Upload one or more PDFs (multipart). Returns `[{file_id, name, size_bytes}]` |

### Job Control

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/start` | Start the pipeline. Body: `{file_ids, force?, output_dir?, settings?}` |
| `POST` | `/api/cancel` | Cancel the running job |
| `GET`  | `/api/status` | Full job state: status, progress%, ETA, per-file list, last 500 log lines |
| `POST` | `/api/check_files` | Body: `{names: ["file.pdf"]}` → `{processed: {name: record}, unprocessed: []}` |

### Results

| Method | Path | Description |
|---|---|---|
| `GET`    | `/api/results` | List all extraction records (metadata only, no content) |
| `GET`    | `/api/results/{id}` | Get record + full text content read from disk |
| `DELETE` | `/api/results/{id}` | Remove DB record (`.txt` file kept on disk) |

### Extracted Files

| Method | Path | Description |
|---|---|---|
| `GET`    | `/api/files` | List all `.txt` files in the output directory |
| `DELETE` | `/api/files/{rel_path}` | Delete `.txt` + companion `.meta.json` |

### Search

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/search?q=term` | Grep-style search across all extracted `.txt` files |

### WebSocket

```
ws://localhost:8080/api/ws
```

Pushed message types:

| `type` | Payload | Description |
|---|---|---|
| `state_update` | Full job state | Broadcast on any status change |
| `log` | `{message}` | Single log line |
| `file_progress` | `{file, pct, page, total_pages}` | Per-page progress for a single file |
| `ping` | — | Keep-alive every 30 s |

---

## Configuration

All settings live in `config.py` and can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `EXTRACTOR_DB_PATH` | `state.db` (beside this file) | SQLite database path |
| `EXTRACTOR_LOG_DIR` | `logs/` (beside this file) | Directory for per-run log files |
| `EXTRACTOR_LOG_MAX_FILES` | `30` | Maximum log files to keep |
| `OCR_DPI` | `200` | Tesseract render DPI (150 = fast, 300 = high quality) |
| `TESSERACT_LANG` | `eng+deu` | Tesseract language model |
| `TESSERACT_OEM` | `1` | OCR Engine Mode |
| `TESSERACT_PSM` | `6` | Page Segmentation Mode |
| `TESSERACT_PAGE_TIMEOUT_SECONDS` | `30` | Per-page OCR timeout |
| `OMP_THREAD_LIMIT` | `1` | OpenMP thread limit inside Tesseract |
| `DISABLE_OCR` | unset | Set to `true` to force direct-only extraction |

---

## Database Schema

Two SQLite tables in `state.db`:

**`files`** — intra-run pipeline state (PROCESSING / COMPLETED / FAILED per absolute path)

**`extracted_texts`** — cross-run extraction index:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `filename` | TEXT UNIQUE | Basename of the PDF (latest run wins) |
| `rel_path` | TEXT | Relative path from input root (`subdir/file.pdf`) |
| `txt_path` | TEXT | Absolute path to the `.txt` on disk |
| `method` | TEXT | `ocr` or `direct` |
| `char_count` | INTEGER | Character count of extracted text |
| `page_count` | INTEGER | Number of pages processed |
| `processed_at` | TIMESTAMP | When this record was last written |

---

## Logs & Metadata

- **Per-run logs:** `logs/pipeline_YYYYMMDD_HHMMSS.log` — newest 30 kept automatically
- **Metadata sidecars:** `extracted_files/{ocr|direct}/path/file.meta.json` — per-page method, timing, char count
- **Benchmark output:** `benchmark_output/` — CSV summary of each run

---

## Development

```bash
pip install -e .[dev]
pre-commit install     # black, isort, flake8

# Format
black .

# Run server with auto-reload (dev only)
uvicorn server:app --reload --port 8080
```
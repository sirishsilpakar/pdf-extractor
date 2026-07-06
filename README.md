# PDF Textract (Backend)

> A high performance PDF text extraction pipeline with REST APIs, OCR strategy pattern, SHA-256 deduplication, and an independent CLI module.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-57%20passed-brightgreen.svg)](https://pytest.org/)


---

## Features

* **SHA-256 Deduplication:** Already extracted files are identified via hashing and skipped.
* **OCR Strategy Pattern:** Abstract interface supporting multiple OCR engines (Tesseract by default).
* **High Performance Multiprocessing:** Dynamically runs processing across CPU cores with memory limits and OMP thread safety.
* **Robust SQLite Schema:** Repository pattern using Write-Ahead Logging (WAL) mode for safe concurrency.
* **CLI & API Support:** Runs headlessly via terminal commands or as a full FastAPI server.

For more technical specifications, architecture diagrams, internal event loops, and extension guides, please refer to [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Quick Start

### 1. Prerequisites

Ensure you have Tesseract OCR installed on your system.

```bash
# macOS
brew install tesseract tesseract-lang

# Debian/Ubuntu
apt-get install tesseract-ocr tesseract-ocr-deu
```

*Python ≥ 3.9 is required.*

### 2. Installation

Clone this repository and set up a virtual environment:

```bash
git clone <repo-url>
cd pdf-extractor

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 3. Running the Server

Start the FastAPI backend server:

```bash
python server.py
# -> Server runs at: http://localhost:8080
# -> OpenAPI Documentation: http://localhost:8080/docs
```

---

## CLI Usage

The independent CLI works without a running server.

```bash
python main.py --help

Commands:
  run      Run the extraction pipeline on a directory
  status   Show database extraction statistics
  reset    Wipe all pipeline state from the database
  engines  List registered OCR engines and availability
```

### CLI Examples

```bash
# Extract all PDFs in a directory tree
python main.py run ./my-pdfs/

# Use a custom output directory, force reprocessing, and specify database path
python main.py run ./my-pdfs/ --output-dir /data/output --force --db /data/state.db

# Skip OCR (direct text extraction only)
python main.py run ./my-pdfs/ --no-ocr

# Check database statistics
python main.py status
```

---

## Configuration

All pipeline settings can be customized using environment variables:

| Variable | Default | Description |
|---|---|---|
| `WORKERS` / `WORKERS_OVERRIDE` | `cpu_count // 2` | Parallel worker processes |
| `OCR_DPI` | `200` | Rendering DPI for OCR |
| `TESSERACT_LANG` | `deu` | Tesseract language(s) |
| `TESSERACT_PAGE_TIMEOUT_SECONDS` | `30` | Per-page OCR timeout |
| `EXTRACTOR_DB_PATH` | `state.db` | SQLite database path |
| `UPLOAD_DIR` | `uploads/` | Upload directory |
| `OUTPUT_DIR` | `extracted_files/` | Extracted text files output directory |
| `OMP_THREAD_LIMIT` | `1` | OpenMP threads per worker (prevents thread storms) |

---

## License

MIT © 2026 Sanjay Gurung, Sapna Luthra, Sirish Silpakar — Philipps-Universität Marburg
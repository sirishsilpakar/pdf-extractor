# PDF Textract - Developer & Contributor Guide

Welcome to the PDF Textract developer documentation. This guide details the software architecture, internal components, runtime behavior (multiprocessing and event queues), development tooling, and step-by-step instructions on how to extend the codebase.

---

## 1. System Architecture

The project utilizes a decoupled **Three-Layer Architecture** to keep the core domain logic independent of execution layers (web API or terminal CLI) and infrastructure components.

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

### Design Patterns Used
* **Strategy Pattern:** Decouples OCR engines (`OCREngine`) from page processing logic, making OCR engines interchangeable.
* **Repository Pattern:** Encapsulates database queries behind `DatabaseRepository`, decoupling business logic from SQL/SQLite dialects.
* **Dependency Injection:** Done via FastAPI's `Depends()` and Python's parameter injection in `run_pipeline()`.
* **Observer / Event Queue Pattern:** Asynchronous background processes emit typed events to a queue, broadcasted to client listeners.

---

## 2. Project Directory Structure

```
pdf-extractor/
├── core/                    # The main domain logic
│   ├── events.py            # Event dataclasses + lifecycle Enums
│   ├── pipeline.py          # Multiprocess pipeline orchestrator & hash dedup
│   ├── worker.py            # PDF loading (PyMuPDF) and OCR page routing
│   ├── resources.py         # Memory capacity calculations (psutil)
│   └── transform.py         # DocumentSanitizer (Regex + spatial layout engine)
│
├── services/                # Infrastructure implementations
│   ├── hasher.py            # SHA-256 file hashing
│   └── ocr/
│       ├── base.py          # OCREngine abstract base class & result schemas
│       ├── tesseract.py     # Tesseract engine wrapper
│       └── registry.py      # Engine registry database
│
├── db/
│   └── repository.py        # DatabaseRepository (SQLite thread-safe WAL mode)
│
├── api/
│   ├── app.py               # FastAPI application factory
│   ├── lifespan.py          # App startup and shutdown contexts
│   ├── job_manager.py       # Thread-safe extraction job state tracker
│   ├── sse.py               # Server-Sent Events subscriber broadcaster
│   └── v1/
│       ├── schemas.py       # Pydantic serialization schemas
│       ├── deps.py          # Dependency injection targets
│       └── routers/         # Versioned endpoint routes (Upload, Job, Search, Runs)
│
├── cli/
│   └── main.py              # Click command-line interface implementation
│
├── tests/                   # Pytest suite (Unit + API Integration tests)
├── config.py                # Global settings and environment fallback variables
└── server.py                # Uvicorn entry point
```

---

## 3. Core Internal Mechanisms

### 3.1. Multiprocessing and Memory Management
To bypass Python's **Global Interpreter Lock (GIL)**, PDF processing is carried out using a `multiprocessing.Pool` across multiple CPU cores.
* **`maxtasksperchild=1`**: Important for memory safety. PyMuPDF and OCR operations allocate heavy C/C++ memory pools. Enforcing workers to exit and respawn after every file processed completely prevents RAM accumulation.
* **Memory Capping (`core/resources.py`)**: Prior to spawning, `safe_worker_count()` queries host RAM using `psutil`. If the configured worker count exceeds system memory (based on `RAM_PER_WORKER_MB`), the pipeline automatically downscales the process pool.
* **OpenMP Thread Limit (`OMP_THREAD_LIMIT=1`)**: External packages like Tesseract default to multi-threaded execution. When combined with a multiprocessing pool, this triggers thread storming and slows processing. Setting the environment variable to `1` limits each worker to a single CPU thread.

### 3.2. Queue-based Event Loop (Observer Pattern)
Workers operate in separate memory spaces and cannot write directly to the database or invoke HTTP endpoints. Instead:
1. Workers serialize telemetry (e.g. `PageDoneEvent`) into standard Python dicts.
2. Dicts are pushed into a shared `multiprocessing.Manager().Queue()`.
3. A background **Consumer Thread** (`core/pipeline.py`) drains the queue and routes logs/events to the injected `progress_callback`.
4. In the API layer, the callback broadcasts events to clients using Server-Sent Events (SSE).

### 3.3. Document Sanitizer Spatial Profiling
The `DocumentSanitizer` in `core/transform.py` identifies running headers/footers dynamically:
1. **Layout Profiling:** Before cleaning, it records the relative Y-coordinate (`y0 / height`) of text blocks across all document pages.
2. **Frequency Threshold:** If a text block falls in the header region (top 20%) or footer region (bottom 20%) and repeats on at least 75% of the pages (default threshold), it is identified as document noise and stripped.
3. **Regex Pipelines:** Applies targeted formatting cleans, removes page number strings, and strips trailing bullets/numeric structures.

---

## 4. Extension Guides

### 4.1. Adding a New OCR Engine
OCR engines are registered using the Strategy pattern. To implement a new OCR engine:

1. Subclass `OCREngine` from `services/ocr/base.py`.
2. Implement your logic in the `run()` method (receives a greyscale PIL image).
3. Register the engine in `services/ocr/registry.py`.

```python
# 1. Create services/ocr/my_engine.py
from PIL import Image
from services.ocr.base import OCREngine, OCRResult

class MyOcrEngine(OCREngine):
    @property
    def name(self) -> str:
        return "my_custom_ocr"

    def is_available(self) -> bool:
        # Check system binaries, API keys, or library availability
        return True

    def run(self, image: Image.Image, **kwargs) -> OCRResult:
        # Run custom OCR processing
        extracted_text = "Extracted text here..."
        confidence = 0.95
        return OCRResult(
            text=extracted_text, 
            confidence=confidence, 
            engine=self.name
        )

# 2. Register in api/lifespan.py or application entry point
from services.ocr.registry import register
from services.ocr.my_engine import MyOcrEngine

register(MyOcrEngine())
```

### 4.2. Changing the Database Repository (e.g. Migrating to PostgreSQL)
To swap the storage engine from SQLite to another database like PostgreSQL:
1. Ensure the new backend driver dependencies are listed in `pyproject.toml`.
2. Keep the `DatabaseRepository` API surface in `db/repository.py` intact.
3. Replace SQLite operations (e.g. connections, transactions, FTS search queries) with PostgreSQL equivalents (e.g., using `psycopg2` or `asyncpg`).

### 4.3. Adding a Post-Processing Node (e.g. LLM Key-Value Extraction)
To pass extracted text to a Large Language Model (LLM) for key-value extraction after file processing:
1. Add an LLM service class under `services/llm.py`.
2. Inject this service or invoke it inside `pipeline.py` right after the file is successfully extracted:
```python
# In core/pipeline.py (Inside imap loop, on sucess)
if result.outcome.is_success:
    db.mark_completed(result.file_path)
    
    # Read the extracted text file
    extracted_text = Path(result.txt_path).read_text(encoding="utf-8")
    
    # Invoke LLM key-value extraction node
    structured_data = llm_service.extract_structured_metadata(extracted_text)
    
    # Save structured metadata to database
    db.save_structured_metadata(record_id, structured_data)
```

---

## 5. Development Setup & Commands

### Setting Up the Development Environment

Follow these steps to establish a local development environment:

1. **Install System Prerequisites:**
   Refer to the main `README.md` to install Tesseract OCR for your specific operating system.

2. **Initialize a Virtual Environment:**
   Run the following commands to create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows, run: .venv\Scripts\activate
   ```

3. **Install Dependencies:**
   Install the package in editable mode along with development dependencies:
   ```bash
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

4. **Set Up Pre-Commit Hooks:**
   The project contains a `.pre-commit-config.yaml` file. Register it to run styling checks automatically on commit:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

### Code Formatting
We enforce formatting checks using `black`, `isort`, and code quality checks using `flake8`.

```bash
# Auto-format imports and styles
isort --profile black .
black .

# Static code quality check
flake8 .
```

### Running Tests
The backend test suite is run using `pytest`.

```bash
# Run unit tests
pytest tests/core/ tests/db/ tests/services/ -v

# Run integration / API tests
pytest tests/api/ -v

# Run all tests
pytest tests/ -v
```

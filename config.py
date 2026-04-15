import multiprocessing
import os
from pathlib import Path

# Controls the number of parallel processes.
# A good starting point is half your CPU cores to avoid memory exhaustion,
# especially with OCR. Adjust this based on system RAM and performance.
# e.g. For an 8-core machine, start with 4.
_workers_override = os.getenv("WORKERS_OVERRIDE")
WORKERS = (
    int(_workers_override)
    if _workers_override
    else max(1, multiprocessing.cpu_count() // 2)
)

# The maximum time in seconds a single PDF is allowed to take.
# If a file processing takes longer, the job is cancelled and marked as a failure.
# This prevents a single corrupt or complex file from stalling the entire pipeline.
JOB_TIMEOUT_SECONDS = 120  # 2 minutes

# If the average number of text characters on the first few pages is below this,
# the file is classified as needing OCR.
TEXT_CHARACTER_THRESHOLD = 50

# To speed up the check, only analyze the first N pages of a document.
PAGES_TO_CHECK_FOR_OCR = 3

# The resolution (Dots Per Inch) for rendering PDF pages to images before OCR.
# Lower DPI is MUCH faster and uses significantly less memory.
# - 150: Fastest, lowest quality. Good for clean documents.
# - 200: A great balance of speed and quality. (Recommended)
# - 300: Slower, higher quality. Use for documents with small or unclear text.
OCR_DPI = int(os.getenv("OCR_DPI", "200"))

# OCR engine configuration
# Language models to use (e.g., "eng", "deu", or "eng+deu").
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "eng+deu")

# OCR Engine Mode (OEM):
# 0 = Legacy engine only, 1 = Neural nets LSTM only, 2 = Legacy + LSTM, 3 = Default based on what is available
TESSERACT_OEM = int(os.getenv("TESSERACT_OEM", "1"))

# Page Segmentation Mode (PSM): common fast choice is 6 (Assume a single uniform block of text)
# See `tesseract --help-psm` for options.
TESSERACT_PSM = int(os.getenv("TESSERACT_PSM", "6"))

# Per-page OCR timeout (in seconds). Slow or problematic pages will be skipped after this time.
TESSERACT_PAGE_TIMEOUT_SECONDS = int(os.getenv("TESSERACT_PAGE_TIMEOUT_SECONDS", "30"))

# Limit OpenMP thread usage inside libraries (e.g., Leptonica/BLAS) used by Tesseract
# Helps prevent oversubscription when running multiple processes
OMP_THREAD_LIMIT = 1

# Optional page-level OCR threading within a single PDF. This trades memory for speed.
# If enabled, each worker will use a small thread pool to OCR multiple pages concurrently.
# Keep this low (e.g., 2-4) to avoid high RAM usage.
# Allow environment variable overrides so CLI can control behavior before workers spawn.
ENABLE_PAGE_LEVEL_OCR_THREADS = os.getenv(
    "ENABLE_PAGE_LEVEL_OCR_THREADS", "false"
).lower() in (
    "1",
    "true",
    "yes",
    "on",
)
PAGE_LEVEL_OCR_MAX_WORKERS = int(
    os.getenv(
        "PAGE_LEVEL_OCR_MAX_WORKERS", str(max(1, multiprocessing.cpu_count() // 4))
    )
)

# Threshold of (Image Area / Page Area) to trigger OCR for a page.
OCR_ON_IMAGE_AREA_THRESHOLD = float(os.getenv("OCR_ON_IMAGE_AREA_THRESHOLD", "0.20"))

# If direct extraction returns fewer characters than this, fallback to OCR for that page.
OCR_LOW_TEXT_LENGTH_THRESHOLD = 10

_CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))

# SQLite database path — override with EXTRACTOR_DB_PATH env var
DB_PATH = os.path.abspath(
    os.getenv("EXTRACTOR_DB_PATH", os.path.join(_CURRENT_PATH, "state.db"))
)

# Directory where per-run log files are stored
LOG_DIR = os.path.abspath(
    os.getenv("EXTRACTOR_LOG_DIR", os.path.join(_CURRENT_PATH, "logs"))
)

# Maximum number of pipeline_*.log files to keep (oldest are deleted)
LOG_MAX_FILES = int(os.getenv("EXTRACTOR_LOG_MAX_FILES", "100"))

# Chunk size for streaming file uploads to disk
UPLOAD_CHUNK_SIZE: int = int(
    os.getenv("UPLOAD_CHUNK_SIZE", str(1 * 1024 * 1024))
)  # 1 MB

# Default page size for paginated API responses
PAGE_SIZE: int = int(os.getenv("PAGE_SIZE", "50"))
MAX_PAGE_SIZE: int = int(os.getenv("MAX_PAGE_SIZE", "200"))

# Bytes sampled for SHA-256 content fingerprinting: first 32 KB + last 32 KB = 64 KB total
HASH_SAMPLE_BYTES: int = 64 * 1024

# Default Directories
UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", os.path.join(_CURRENT_PATH, "uploads")))
OUTPUT_DIR: str = os.getenv(
    "OUTPUT_DIR", os.path.join(_CURRENT_PATH, "extracted_files")
)

UPLOAD_DIR.mkdir(exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Estimated peak RAM consumption per worker process (in MB).
# Includes the in-memory OCR image buffer (200 DPI page render of a 50 MB PDF ~ 200–400 MB) plus Python interpreter overhead
# Reduce this if workers get OOM error or increase
# if your machine has plenty of RAM and you want more parallelism
RAM_PER_WORKER_MB: int = int(os.getenv("RAM_PER_WORKER_MB", "800"))

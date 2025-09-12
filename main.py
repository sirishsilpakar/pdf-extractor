import os
import sys
import argparse
import pymupdf

from extractor import run_pipeline
from config import OMP_THREAD_LIMIT

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF extraction pipeline")
    parser.add_argument("input_directory", help="Directory containing PDFs")
    parser.add_argument("--workers", type=int, default=None, help="Override number of worker processes")
    parser.add_argument(
        "--enable-page-ocr", action="store_true", help="Enable page-level OCR threading inside each worker"
    )
    parser.add_argument(
        "--page-ocr-workers", type=int, default=None, help="Threads per worker for page-level OCR (2-4 recommended)"
    )
    args = parser.parse_args()

    # Limit OpenMP threads used inside libraries called by Tesseract to avoid oversubscription.
    os.environ.setdefault("OMP_THREAD_LIMIT", str(OMP_THREAD_LIMIT))
    os.environ.setdefault("OMP_NUM_THREADS", str(OMP_THREAD_LIMIT))

    # Export page-level OCR options so children inherit before spawning
    if args.enable_page_ocr:
        os.environ["ENABLE_PAGE_LEVEL_OCR_THREADS"] = "true"
    if args.page_ocr_workers is not None:
        os.environ["PAGE_LEVEL_OCR_MAX_WORKERS"] = str(max(1, args.page_ocr_workers))

    pymupdf.TOOLS.mupdf_display_errors(False)

    input_directory = args.input_directory
    if not os.path.isdir(input_directory):
        print(f"ERROR: The directory '{input_directory}' was not found.")
        sys.exit(1)

    try:
        # Optionally override worker count through environment to keep config centralized
        if args.workers is not None and args.workers > 0:
            os.environ["WORKERS_OVERRIDE"] = str(args.workers)
        run_pipeline(input_dir=input_directory)
    except Exception as e:
        print(f"A critical error occurred in the main process: {e}")

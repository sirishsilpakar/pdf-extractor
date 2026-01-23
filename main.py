import os
import sys
import argparse
import pymupdf

from extractor import run_pipeline
from config import OMP_THREAD_LIMIT

def main():
    parser = argparse.ArgumentParser(description="PDF extraction pipeline")
    parser.add_argument("input_directory", help="Directory containing PDFs")
    parser.add_argument("--workers", type=int, default=None, help="Override number of worker processes")
    parser.add_argument(
        "--enable-page-ocr", action="store_true", help="Enable page-level OCR threading inside each worker"
    )
    parser.add_argument(
        "--page-ocr-workers", type=int, default=None, help="Threads per worker for page-level OCR (2-4 recommended)"
    )
    parser.add_argument(
        "--no-ocr", action="store_true", help="Disable OCR completely (force direct extraction)"
    )
    parser.add_argument(
        "--output-dir", default="extracted_files", help="Directory to save extracted files (default: extracted_files)"
    )
    parser.add_argument(
        "--fast", action="store_true", help="Enable fast mode (lower DPI, conservative OCR, shorter timeouts)"
    )
    args = parser.parse_args()

    if args.no_ocr:
        os.environ["DISABLE_OCR"] = "true"

    if args.fast:
        os.environ["OCR_DPI"] = "150"
        os.environ["TESSERACT_PAGE_TIMEOUT_SECONDS"] = "15"
        os.environ["OCR_ON_IMAGE_AREA_THRESHOLD"] = "0.30"
        print("Fast mode enabled: DPI=150, Timeout=15s, ImageThreshold=0.30")

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
        run_pipeline(input_dir=input_directory, output_dir=args.output_dir, force=args.force)
    except Exception as e:
        import logging

        logging.exception(e)
        print(f"A critical error occurred in the main process: {e}")


if __name__ == "__main__":
    main()

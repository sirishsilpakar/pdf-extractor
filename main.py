import os
import sys
import pymupdf

from extractor import run_pipeline

if __name__ == "__main__":
    pymupdf.TOOLS.mupdf_display_errors(False)

    if len(sys.argv) < 2:
        print("Usage: python main.py <directory_with_pdfs>")
        sys.exit(1)

    input_directory = sys.argv[1]
    if not os.path.isdir(input_directory):
        print(f"ERROR: The directory '{input_directory}' was not found.")
        sys.exit(1)

    try:
        run_pipeline(input_dir=input_directory)
    except Exception as e:
        print(f"A critical error occurred in the main process: {e}")

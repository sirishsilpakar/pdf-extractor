import os
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
import time
import csv
import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Optional

from config import WORKERS, JOB_TIMEOUT_SECONDS, LOG_DIR
from worker import process_file
import database

logger = logging.getLogger(__name__)

def _configure_logging(log_dir: str) -> str:
    """Create a timestamped log file. Returns its path."""
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"pipeline_{ts}.log")
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        force=True,
    )

def run_pipeline(
    input_dir: str,
    output_dir: str = "extracted_files",
    batch_size: int = 10,
    force: bool = False,
    log_dir: str = None,
):
    log_dir = log_dir or LOG_DIR
    _configure_logging(log_dir)
    def _log(msg: str):
        logger.info(msg)
    _log(f"Pipeline started: input={input_dir!r} output={output_dir!r} force={force}")
    print(f"--------- Starting PDF Extraction Pipeline ---------")
    print(f"Using {WORKERS} worker processes")
    print(f"Job timeout set to {JOB_TIMEOUT_SECONDS} seconds.")
    print(f"Output Directory: {output_dir}")

    # Initialize Database
    database.init_db()

    # Get list of already completed files
    if not force:
        completed_files = database.get_processed_files()
        print(f"Found {len(completed_files)} already processed files in DB.")
    else:
        completed_files = set()
        print("Force mode enabled: Reprocessing all files.")

    all_files = [
        os.path.abspath(os.path.join(r, f))
        for r, _, fs in os.walk(input_dir)
        for f in fs
        if f.lower().endswith(".pdf")
    ]

    if not all_files:
        print(f"No PDF files found in directory : {input_dir}")
        return

    # Filter files
    files_to_process = []
    skipped_count = 0
    for f in all_files:
        if f in completed_files:
            skipped_count += 1
        else:
            files_to_process.append(f)

    _log(
        f"Found {len(all_files)} files. Processing {len(files_to_process)}. Skipped {skipped_count}."
    )

    if not files_to_process:
        print("No new files to process.")
        return

    direct_extraction_success, ocr_extraction_success, failures, timeouts = 0, 0, 0, 0

    # Using partial to pre-fill the input_dir argument for every worker call
    task_function = partial(
        process_file, input_dir_root=input_dir, output_dir_root=output_dir
    )

    per_file_rows = []

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        # Mark files as STARTED and submit
        future_to_file = {}
        for f in files_to_process:
            database.mark_started(f)  # Start tracking
            future = executor.submit(task_function, f)
            future_to_file[future] = f

        for future in tqdm(
            as_completed(future_to_file),
            total=len(files_to_process),
            desc="Processing pdf files",
        ):
            file_path = future_to_file[future]
            try:
                result = future.result(timeout=JOB_TIMEOUT_SECONDS)
                if isinstance(result, tuple) and len(result) == 5:
                    _, status, message, elapsed, char_count = result
                elif isinstance(result, tuple) and len(result) == 4:
                    _, status, message, elapsed = result
                    char_count = ""
                else:
                    _, status, message = result
                    elapsed = None
                    char_count = ""

                if "SUCCESS" in status:
                    database.mark_completed(file_path)
                    method = "direct" if status == "SUCCESS_DIRECT" else "ocr"
                    if method == "direct":
                        direct_success += 1
                    else:
                        ocr_success += 1
                    log_msg = (
                        f"[ OK ] {basename} | {method.upper()} | "
                        f"{elapsed:.1f}s | {char_count:,} chars"
                    )
                    _log(log_msg)
                    print(
                        f"  [{done_count}/{total}] OK    {basename}  ({elapsed:.1f}s)"
                    )

                else:
                    failures += 1
                    database.mark_failed(file_path, message)
                    print(f"\nERROR: {os.path.basename(file_path)} --> {message}")
                    logging.error(f"FAILURE: {file_path} - {message}")
                    method = "error"
                    _log(f"[FAIL] {basename} -> {message}")
                    print(f"  [{done_count}/{total}] ERROR {basename} -> {message}")

                per_file_rows.append(
                    {
                        "file": file_path,
                        "status": status,
                        "method": method,
                        "seconds": f"{elapsed:.3f}" if elapsed is not None else "",
                        "chars": str(char_count) if char_count != "" else "",
                        "message": message,
                    }
                )

            except TimeoutError:
                failures += 1
                timeouts += 1
                msg = f"TIMEOUT ({JOB_TIMEOUT_SECONDS}s)"
                database.mark_failed(file_path, msg)
                print(
                    f"\nTIMEOUT ERROR: {os.path.basename(file_path)} took longer than {JOB_TIMEOUT_SECONDS}s and was skipped."
                )
                logging.error(f"TIMEOUT: {file_path}")

            except Exception as e:
                failures += 1
                msg = str(e)
                database.mark_failed(file_path, msg)
                print(
                    f"\nERROR: An unexpected error occurred for {os.path.basename(file_path)}: {e}"
                )
                logging.error(f"EXCEPTION: {file_path} - {e}")

    print("\n--------- Pipeline Complete ---------")
    print(f"  Direct: {direct_success}  OCR: {ocr_success}  Failed: {failures}")

    _log(
        f"Pipeline finished. Direct: {direct_success}, OCR: {ocr_success}, "
        f"Failed: {failures}, Timeouts: {timeouts}"
    )

    # Persist per-run summary
    summary_dir = "benchmark_output"
    os.makedirs(summary_dir, exist_ok=True)
    try:
        with open(
            os.path.join(summary_dir, "pipeline_summary.csv"),
            "w",
            newline="",
            encoding="utf-8",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["file", "status", "method", "seconds", "chars", "message"],
            )
            writer.writeheader()
            writer.writerows(per_file_rows)
    except Exception:
        pass
    try:
        with open(
            os.path.join(summary_dir, "pipeline_summary.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(per_file_rows, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

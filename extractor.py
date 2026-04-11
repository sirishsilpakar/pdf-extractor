import os
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
import time
import csv
import json
from functools import partial

from tqdm import tqdm

from config import WORKERS, JOB_TIMEOUT_SECONDS
from worker import process_file
import database

# Configure logging
logging.basicConfig(
    filename="pipeline.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def run_pipeline(
    input_dir: str,
    output_dir: str = "extracted_files",
    force: bool = False,
    remove_header: bool = False,
    remove_footer: bool = False,
    remove_page_number: bool = False,
    remove_numerics: bool = False,
    lemma: bool = False,
    job_id: str = "",
    progress_callback=None
):
    """Runs the entire PDF processing pipeline"""
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

    print(f"Total PDFs found: {len(all_files)}")
    print(f"Skipping: {skipped_count}")
    print(f"To Process: {len(files_to_process)}")

    logging.info(
        f"Pipeline started. Found {len(all_files)} files. Processing {len(files_to_process)}. Skipped {skipped_count}."
    )

    if not files_to_process:
        print("No new files to process.")
        raise Exception("No new files to process.")

    direct_extraction_success, ocr_extraction_success, failures, timeouts = 0, 0, 0, 0

    # Using partial to pre-fill the input_dir argument for every worker call
    task_function = partial(
        process_file, input_dir_root=input_dir, output_dir_root=output_dir
    )

    per_file_rows = []

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        # Mark files as STARTED and submit
        future_to_file = {}
        total = len(files_to_process)
        for f in files_to_process:
            database.mark_started(f)  # Start tracking
            future = executor.submit(task_function, f)
            future_to_file[future] = f
        pbar = tqdm(
            as_completed(future_to_file),
            total=len(files_to_process),
            desc="Processing pdf files",
            mininterval=0.0001,
        )
        for index, future in enumerate(pbar):
            file_path = future_to_file[future]
            pbar.set_description(f'Processing {file_path.rsplit('/', 1)[1]}')
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
                    if status == "SUCCESS_DIRECT":
                        direct_extraction_success += 1
                        method = "direct"
                    elif status == "SUCCESS_OCR":
                        ocr_extraction_success += 1
                        method = "ocr"
                    else:
                        method = "unknown"

                    log_msg = f"PROCESSED: {file_path} | Method: {method} | Time: {elapsed:.3f}s | Chars: {char_count}"
                    logging.info(log_msg)

                    if char_count == 0:
                        logging.warning(
                            f"EMPTY_OUTPUT: {file_path} extraction yielded 0 characters."
                        )

                else:
                    failures += 1
                    database.mark_failed(file_path, message)
                    print(f"\nERROR: {os.path.basename(file_path)} --> {message}")
                    logging.error(f"FAILURE: {file_path} - {message}")
                    method = "error"

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
                if progress_callback:
                  progress_callback({
                    "current": index + 1,
                    "total": total,
                    "file_path": os.path.basename(file_path),
                    "status": status,          # SUCCESS_DIRECT, SUCCESS_OCR, FAILURE, TIMEOUT
                    "method": method,          # "direct", "ocr", "error"
                    "elapsed": round(elapsed, 3) if elapsed is not None else None,
                    "chars": char_count if char_count != "" else None,
                    "message": message,
                    "size": os.path.getsize(file_path),
                  })
                  param = {
                    "batch_id": job_id,
                    "file_name": os.path.basename(file_path), 
                    "method": method, 
                    "status": "completed", 
                    "size": os.path.getsize(file_path),
                    "error": ""
                  }
                  database.insert_file_log(**param)

            except TimeoutError:
                failures += 1
                timeouts += 1
                msg = f"TIMEOUT ({JOB_TIMEOUT_SECONDS}s)"
                database.mark_failed(file_path, msg)
                print(
                    f"\nTIMEOUT ERROR: {os.path.basename(file_path)} took longer than {JOB_TIMEOUT_SECONDS}s and was skipped."
                )
                logging.error(f"TIMEOUT: {file_path}")
                if progress_callback:
                  progress_callback({
                      "current": index + 1, "total": total,
                      "file_path": os.path.basename(file_path),
                      "status": "TIMEOUT", "method": "error",
                      "elapsed": None, "chars": None, "message": msg,
                  })
                  param = {
                    "batch_id": job_id,
                    "file_name": os.path.basename(file_path), 
                    "method": method, 
                    "status": "failed", 
                    "size": os.path.getsize(file_path), 
                    "error": "Timeout error"
                  }
                  database.insert_file_log(**param)

            except Exception as e:
                failures += 1
                msg = str(e)
                database.mark_failed(file_path, msg)
                print(
                    f"\nERROR: An unexpected error occurred for {os.path.basename(file_path)}: {e}"
                )
                logging.error(f"EXCEPTION: {file_path} - {e}")
                if progress_callback:
                  progress_callback({
                      "current": index + 1, "total": total,
                      "file_path": os.path.basename(file_path),
                      "status": "EXCEPTION", "method": "error",
                      "elapsed": None, "chars": None, "message": str(e),
                  })
                  param = {
                    "batch_id": job_id,
                    "file_name": os.path.basename(file_path), 
                    "method": method, 
                    "status": "failed", 
                    "size": os.path.getsize(file_path), 
                    "error": str(e)
                  }
                  database.insert_file_log(**param)

    print("\n--------- Pipeline Complete ---------")
    print(f"Successfully processed (Direct): {direct_extraction_success}")
    print(f"Successfully processed (OCR): {ocr_extraction_success}")
    print(f"Failed to process: {failures}")
    if timeouts > 0:
        print(f"  ({timeouts} of these failures were due to timeout)")

    logging.info(
        f"Pipeline finished. Direct: {direct_extraction_success}, OCR: {ocr_extraction_success}, Failed: {failures}, Timeouts: {timeouts}"
    )

    # Save the pipeline summary
    summary_dir = "benchmark_output"
    os.makedirs(summary_dir, exist_ok=True)
    csv_path = os.path.join(summary_dir, "pipeline_summary.csv")
    json_path = os.path.join(summary_dir, "pipeline_summary.json")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["file", "status", "method", "seconds", "chars", "message"],
            )
            writer.writeheader()
            writer.writerows(per_file_rows)
    except Exception:
        pass
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(per_file_rows, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

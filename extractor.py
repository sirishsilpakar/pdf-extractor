import os

from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
import time
import csv
import json
from functools import partial

from tqdm import tqdm

from config import WORKERS, JOB_TIMEOUT_SECONDS
from worker import process_file


def run_pipeline(input_dir: str, batch_size: int = 10):
    """Runs the entire PDF processing pipeline"""
    print(f"--------- Starting PDF Extraction Pipeline ---------")
    print(f"Using {WORKERS} worker processes, batch_size: {batch_size}")
    print(f"Job timeout set to {JOB_TIMEOUT_SECONDS} seconds.")

    all_files = [
        os.path.join(r, f)
        for r, _, fs in os.walk(input_dir)
        for f in fs
        if f.lower().endswith(".pdf")
    ]
    if not all_files:
        print(f"No PDF files found in directory : {input_dir}")
        return

    print(f"Found {len(all_files)} PDF files to process.")

    direct_extraction_success, ocr_extraction_success, failures, timeouts = 0, 0, 0, 0

    # Using partial to pre-fill the input_dir argument for every worker call
    task_function = partial(process_file, input_dir_root=input_dir)

    per_file_rows = []

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        future_to_file = {executor.submit(task_function, f): f for f in chunks(all_files, batch_size)}

        for future in tqdm(
            as_completed(future_to_file),
            total=len(all_files),
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

                if status == "SUCCESS_DIRECT":
                    direct_extraction_success += 1
                    method = "direct"
                elif status == "SUCCESS_OCR":
                    ocr_extraction_success += 1
                    method = "ocr"
                else:
                    failures += 1
                    print(f"\nERROR: {os.path.basename(file_path)} --> {message}")
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

            except TimeoutError:
                failures += 1
                timeouts += 1
                print(
                    f"\nTIMEOUT ERROR: {os.path.basename(file_path)} took longer than {JOB_TIMEOUT_SECONDS}s and was skipped."
                )
            except Exception as e:
                failures += 1
                print(
                    f"\nERROR: An unexpected error occurred for {os.path.basename(file_path)}: {e}"
                )

    print("\n--------- Pipeline Complete ---------")
    print(f"Successfully processed (Direct): {direct_extraction_success}")
    print(f"Successfully processed (OCR): {ocr_extraction_success}")
    print(f"Failed to process: {failures}")
    if timeouts > 0:
        print(f"  ({timeouts} of these failures were due to timeout)")

    # Write pipeline summary next to outputs
    summary_dir = "benchmark_output"
    os.makedirs(summary_dir, exist_ok=True)
    csv_path = os.path.join(summary_dir, "pipeline_summary.csv")
    json_path = os.path.join(summary_dir, "pipeline_summary.json")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["file", "status", "method", "seconds", "chars", "message"]
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

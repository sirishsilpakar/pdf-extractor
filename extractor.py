import os

from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError
from functools import partial

from tqdm import tqdm

from config import WORKERS, JOB_TIMEOUT_SECONDS
from worker import process_file


def run_pipeline(input_dir: str):
    """Runs the entire PDF processing pipeline"""
    print(f"--------- Starting PDF Extraction Pipeline ---------")
    print(f"Using {WORKERS} worker processes.")
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

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        future_to_file = {executor.submit(task_function, f): f for f in all_files}

        for future in tqdm(
            as_completed(future_to_file),
            total=len(all_files),
            desc="Processing pdf files",
        ):
            file_path = future_to_file[future]
            try:
                _, status, message = future.result(timeout=JOB_TIMEOUT_SECONDS)

                if status == "SUCCESS_DIRECT":
                    direct_extraction_success += 1
                elif status == "SUCCESS_OCR":
                    ocr_extraction_success += 1
                else:
                    failures += 1
                    print(f"\nERROR: {os.path.basename(file_path)} --> {message}")

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

import os
import logging
import multiprocessing
import threading
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

# Shared queue for events from worker processes to the main process
_WORKER_QUEUE: Optional[multiprocessing.Queue] = None

# Pool initializer for shared event queue (set once per worker process)
# Stores the shared queue in a module-level global so _tracked_process_file can access it
# without needing it passed as an argument (which would require pickling it into every task call)
def _pool_init(q):
    global _WORKER_QUEUE
    _WORKER_QUEUE = q


def _tracked_process_file(file_path: str, input_dir: str, output_dir: str):
    """Module level picklable wrapper around process_file(). 
    
    This function is called by the worker processes to process a file. It emits a 'file_started' event before delegating to the real process_file().
    """
    if _WORKER_QUEUE is not None:
        # put_nowait is used to put the event into the queue without blocking
        # This is important because the worker processes are running in parallel
        # and we don't want to block the worker processes from processing files
        _WORKER_QUEUE.put_nowait(
            {
                "type": "file_started",
                "file_path": file_path,
                "file": os.path.basename(file_path),
                "pid": os.getpid(),
            }
        )
    return process_file(
        file_path,
        input_dir_root=input_dir,
        output_dir_root=output_dir,
        event_queue=_WORKER_QUEUE,
    )


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
    print(f"Using {WORKERS} worker processes (Pool, maxtasksperchild=1)")
    print(f"Job timeout set to {JOB_TIMEOUT_SECONDS} seconds.")
    print(f"Output Directory: {output_dir}")

    # Initialize Database
    database.init_db()

    # Skip filenames already present in extracted_texts
    if not force:
        completed_filenames = database.get_processed_filenames()
        print(
            f"Resuming: {len(completed_filenames)} already extracted files will be skipped."
        )
    else:
        completed_filenames = set()
        print("Force mode: reprocessing all files.")

    all_files = [
        os.path.abspath(os.path.join(r, f))
        for r, _, fs in os.walk(input_dir)
        for f in fs
        if f.lower().endswith(".pdf")
    ]

    if not all_files:
        print(f"No PDF files found in directory : {input_dir}")
        return

    files_to_process = [
        f for f in all_files if os.path.basename(f) not in completed_filenames
    ]
    skipped_count = len(all_files) - len(files_to_process)

    print(
        f"Total PDFs: {len(all_files)} | Skipping: {skipped_count} | To process: {len(files_to_process)}"
    )
    _log(
        f"Found {len(all_files)} files. Processing {len(files_to_process)}. Skipped {skipped_count}."
    )

    if not files_to_process:
        print("Nothing new to process.")
        return

    # Smallest files first to keep all workers busy at the end of the batch
    files_to_process.sort(key=lambda p: os.path.getsize(p))

    for f in files_to_process:
        database.mark_started(f)

    total = len(files_to_process)
    direct_success = ocr_success = failures = timeouts = done_count = 0
    per_file_rows = []

    # Shared event queue: workers push file_started / page_done events
    # Using a Manager-managed queue allows the queue to be shared between processes
    manager = multiprocessing.Manager()
    event_q = manager.Queue()
    # This allows the main thread to continue processing files while the consumer thread
    # handles the events from the workers
    _stop_consumer = threading.Event()

    # Flag to signal if OCR engine or library is missing
    ocr_engine_missing_flag = threading.Event()

    def _consume():
        while not (_stop_consumer.is_set() and event_q.empty()):
            try:
                ev = event_q.get(timeout=0.3)
                if ev.get("type") == "ocr_engine_missing":
                    ocr_engine_missing_flag.set()
            except Exception:
                pass

    # Start the consumer thread
    # Daemon=True means the thread will exit when the main program exits
    # This is important because the consumer thread is not a part of the pipeline
    # and should not keep the program alive
    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()

    # Partial function to create a task function with the input and output directories already set
    # This allows the task function to be called with only the file path
    task_fn = partial(_tracked_process_file, input_dir=input_dir, output_dir=output_dir)

    try:
        # Create a pool of worker processes
        # maxtasksperchild=1 ensures that each worker process is terminated after processing one file
        # Since the worker processes are running in parallel we don't want to block the worker processes from processing files
        # initializer is used to initialize the worker processes with the event queue
        # initargs is used to pass arguments to the initializer
        with multiprocessing.Pool(
            processes=WORKERS,
            maxtasksperchild=1,
            initializer=_pool_init,
            initargs=(event_q,),
        ) as pool:
            # imap_unordered yields results as workers complete them (not in submission order)
            # chunksize=1 is used to process one file at a time
            for result in pool.imap_unordered(task_fn, files_to_process, chunksize=1):

                # ocr_engine_missing is checked here in the main thread but was set by a worker process 
                # worker process -> main process consumer thread -> threading.Event (the full cross-process signal chain)
                if ocr_engine_missing_flag.is_set():
                    _log(
                        "OCR engine or library is missing. Terminating pipeline as requested."
                    )
                    pool.terminate()
                    break

                done_count += 1
                progress_pct = int(done_count / total * 100)

                file_path, status, message, elapsed, char_count = (
                    result if len(result) == 5 else (*result, "")
                )
                basename = os.path.basename(file_path)

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
                    event_type, event_status = "file_done", "completed"

                else:
                    failures += 1
                    database.mark_failed(file_path, message)
                    method = "error"
                    event_type = (
                        "file_timeout" if "TIMEOUT" in status else "file_failed"
                    )
                    event_status = "timeout" if "TIMEOUT" in status else "failed"
                    if "TIMEOUT" in status:
                        timeouts += 1
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

    finally:
        # Always shut down consumer thread cleanly
        _stop_consumer.set()
        consumer.join(timeout=5)
        manager.shutdown()

    print("\n--------- Pipeline Complete ---------")
    print(f"  Direct: {direct_success}  OCR: {ocr_success}  Failed: {failures}")

    if ocr_engine_missing_flag.is_set():
        print("\n[CRITICAL ERROR] OCR Library or Engine (Tesseract) is not installed.")
        print("                 OCR-only tasks failed. Pipeline stopped early.")
        _log("CRITICAL: OCR Library or Engine missing. Pipeline stopped early.")

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

"""PDF extraction pipeline pure domain logic.

The 'run_pipeline' function is the single entry point used by both the
CLI and the API layer.  All dependencies (DB, OCR engine, progress callback,
cancellation event) are injected so the pipeline can be unit-tested without
a running server or real OCR engine.

Event flow
----------
Workers emit dict-serialised events into a 'multiprocessing.Queue'.
A consumer thread drains that queue and calls 'progress_callback' on each
event.  The pipeline itself only calls 'progress_callback' for aggregate
events ('file_done', 'done').

Event types emitted
-------------------
- 'log'              plain log message
- 'file_started'     worker picked up a file
- 'page_done'        a single page finished
- 'file_done'        file completed successfully
- 'file_failed'      file completed with error
- 'file_timeout'     file timed out
- 'done'             all files finished
- 'ocr_engine_missing' OCR binary not found pipeline stops
"""

from __future__ import annotations

import csv
import json
import logging
import multiprocessing
import os
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import (
    JOB_TIMEOUT_SECONDS,
    LOG_DIR,
    LOG_MAX_FILES,
    RAM_PER_WORKER_MB,
    WORKERS,
)
from core.events import (
    EventCallback,
    FileDoneEvent,
    FileFailedEvent,
    FileResult,
    FileTimeoutEvent,
    LogEvent,
    PipelineDoneEvent,
    PipelineOutcome,
)
from core.resources import safe_worker_count
from core.worker import make_task_fn, pool_init
from db.repository import DatabaseRepository
from services.ocr.base import OCREngine

logger = logging.getLogger(__name__)


def _configure_logging(log_dir: str) -> None:
    """Create a timestamped log file and rotate old ones"""
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"pipeline_{ts}.log")

    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        force=True,
    )

    # Prune oldest logs beyond retention limit
    logs = sorted(Path(log_dir).glob("pipeline_*.log"))
    while len(logs) > LOG_MAX_FILES:
        try:
            logs.pop(0).unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Internal helper functions
# ---------------------------------------------------------------------------


def _emit(callback: Optional[EventCallback], event) -> None:
    """Serialise event dataclass to a dict and call callback safely"""
    if callback:
        try:
            callback(asdict(event) if hasattr(event, "__dataclass_fields__") else event)
        except Exception as exc:
            logger.warning("progress_callback raised: %s", exc)


def _emit_done(
    callback: Optional[EventCallback],
    done: int,
    total: int,
    direct: int,
    ocr: int,
    failed: int,
    timeouts: int,
) -> None:
    _emit(
        callback,
        PipelineDoneEvent(
            done=done,
            total=total,
            direct=direct,
            ocr=ocr,
            failed=failed,
            timeouts=timeouts,
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(
    input_dir: str,
    output_dir: str = "extracted_files",
    force: bool = False,
    progress_callback: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    log_dir: Optional[str] = None,
    ocr_engine: Optional[OCREngine] = None,
    db: Optional[DatabaseRepository] = None,
    run_id: Optional[str] = None,
    total_timeout_seconds: int = 0,
) -> None:
    """Run the PDF extraction pipeline.

    Args:
        input_dir:         Directory tree containing PDF files.
        output_dir:        Root directory for extracted '.txt' outputs.
        force:             Reprocess files already present in the database.
        progress_callback: Called with a dict for each pipeline event.
        cancel_event:      'threading.Event' checked between files; set to stop early.
        log_dir:           Override for the rotating log file directory.
        ocr_engine:        OCR engine to pass to worker processes.  If 'None'
                           OCR is disabled (direct extraction only).
        db:                'DatabaseRepository' instance.  Created with the
                           configured 'DB_PATH' when not provided so the pipeline
                           works standalone from the CLI with zero setup.
        run_id:            UUID for this run.  A fresh one is created if not supplied.
    """
    import uuid as _uuid

    _configure_logging(log_dir or LOG_DIR)

    # Allow standalone CLI use, create a default DB if none injected
    if db is None:
        from config import DB_PATH

        db = DatabaseRepository(DB_PATH)
        db.init_schema()

    # Ensure every run has an ID for DB
    if not run_id:
        run_id = _uuid.uuid4().hex

    def _log(msg: str, level: str = "info") -> None:
        logger.info(msg)
        _emit(progress_callback, LogEvent(message=msg, level=level))

    _log(f"Pipeline started - input={input_dir!r} output={output_dir!r} force={force}")
    logger.info("Workers=%d timeout=%ds", WORKERS, JOB_TIMEOUT_SECONDS)

    # Discover files to process
    all_files = [
        os.path.abspath(os.path.join(root, fname))
        for root, _, fnames in os.walk(input_dir)
        for fname in fnames
        if fname.lower().endswith(".pdf")
    ]

    if not all_files:
        _log(f"No PDF files found in {input_dir!r}")
        _emit_done(progress_callback, 0, 0, 0, 0, 0, 0)
        return

    # Hash-based deduplication (with filename fallback for legacy records)
    if not force:
        processed_hashes = db.get_processed_hashes()
        processed_filenames = db.get_processed_filenames()  # legacy fallback
    else:
        processed_hashes = set()
        processed_filenames = set()
        _log("Force mode: reprocessing all files")

    files_to_process: list[str] = []
    skipped_count = 0

    for fp in all_files:
        try:
            from services.hasher import compute_file_hash

            h = compute_file_hash(fp)
            if h in processed_hashes:
                skipped_count += 1
                continue
            # Fallback for legacy records without a hash
            if os.path.basename(fp) in processed_filenames:
                skipped_count += 1
                continue
        except Exception:
            pass  # If we can't hash, include the file to be safe
        files_to_process.append(fp)

    _log(
        f"Total:{len(all_files)} ToProcess:{len(files_to_process)} Skipped:{skipped_count}"
    )

    if not files_to_process:
        _log("Nothing new to process.")
        _emit_done(progress_callback, 0, 0, 0, 0, 0, 0)
        return

    # Smallest files first so that it keeps all workers busy towards end of batch
    files_to_process.sort(key=lambda p: os.path.getsize(p))

    # Create run record before work begins
    # Scope all extracted files for this run to their own sub-directory
    # so that extracted_files/<run_id>/ocr/... and .../direct/... are isolated
    run_output_dir = os.path.join(output_dir, run_id)
    os.makedirs(run_output_dir, exist_ok=True)

    db.create_run(
        run_id=run_id,
        total_files=len(files_to_process),
        input_dir=input_dir,
    )

    # Pre mark all files as started in one transaction
    db.mark_started_batch(files_to_process)

    total = len(files_to_process)
    direct_success = ocr_success = failures = timeouts = done_count = 0
    per_file_rows: list[dict] = []

    # Multiprocessing pool + event consumer
    manager = multiprocessing.Manager()
    event_q = manager.Queue()
    stop_consumer = threading.Event()
    ocr_missing_flag = threading.Event()
    start_time = datetime.now()

    def _consume() -> None:
        while not (stop_consumer.is_set() and event_q.empty()):
            try:
                ev = event_q.get(timeout=0.3)
                if ev.get("type") == "ocr_engine_missing":
                    ocr_missing_flag.set()
                _emit(progress_callback, ev)
            except Exception:
                pass

    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()

    task_fn = make_task_fn(input_dir=input_dir, output_dir=run_output_dir)

    effective_workers = safe_worker_count(WORKERS, RAM_PER_WORKER_MB)
    _log(
        f"Workers: {effective_workers} (CPU cap: {WORKERS}, RAM/worker: {RAM_PER_WORKER_MB} MB)"
    )

    try:
        with multiprocessing.Pool(
            processes=effective_workers,
            maxtasksperchild=1,
            initializer=pool_init,
            initargs=(event_q, ocr_engine),
        ) as pool:
            for result in pool.imap_unordered(task_fn, files_to_process, chunksize=1):
                result: FileResult

                if ocr_missing_flag.is_set():
                    _log("OCR engine missing - stopping pipeline.", level="error")
                    pool.terminate()
                    break

                if cancel_event is not None and cancel_event.is_set():
                    _log("Pipeline cancelled by user.")
                    pool.terminate()
                    break

                if total_timeout_seconds > 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed > total_timeout_seconds:
                        _log(
                            f"Pipeline timed out after {elapsed:.1f}s (limit: {total_timeout_seconds}s)."
                        )
                        pool.terminate()
                        break

                done_count += 1
                progress_pct = int(done_count / total * 100)
                basename = os.path.basename(result.file_path)

                # DB update all writes happen in the main process
                if result.outcome.is_success:
                    db.mark_completed(result.file_path)
                    record_id = db.save_extracted_text(
                        source_path=result.file_path,
                        filename=basename,
                        rel_path=result.rel_path,
                        txt_path=result.txt_path,
                        method=result.method.value,
                        char_count=result.char_count,
                        page_count=result.page_count,
                        content_hash=result.content_hash,
                        run_id=run_id,
                        confidence=result.confidence,
                        flags=result.flags,
                        txt_hash=result.txt_hash,
                    )
                    # FTS index split txt into page_count chunks for per-page indexing
                    try:
                        import math
                        from pathlib import Path as _Path

                        txt_content = _Path(result.txt_path).read_text(
                            encoding="utf-8", errors="replace"
                        )
                        n_pages = max(1, result.page_count)
                        chunk_size = math.ceil(len(txt_content) / n_pages)
                        page_chunks = [
                            txt_content[i : i + chunk_size]
                            for i in range(0, len(txt_content), chunk_size)
                        ]
                        db.fts_index_pages(
                            doc_id=record_id,
                            filename=basename,
                            rel_path=result.rel_path,
                            page_texts=page_chunks,
                        )
                    except Exception as _fts_exc:
                        logger.warning(
                            "FTS index failed for %s: %s", basename, _fts_exc
                        )

                    if result.outcome == PipelineOutcome.SUCCESS_DIRECT:
                        direct_success += 1
                    else:
                        ocr_success += 1
                else:
                    db.mark_failed(result.file_path, result.message)
                    failures += 1
                    if result.outcome == PipelineOutcome.TIMEOUT:
                        timeouts += 1

                # Build and emit file completion event
                event_cls = {
                    "file_done": FileDoneEvent,
                    "file_failed": FileFailedEvent,
                    "file_timeout": FileTimeoutEvent,
                }[result.outcome.as_event_type]

                _emit(
                    progress_callback,
                    event_cls(
                        file=basename,
                        file_path=result.file_path,
                        status=result.outcome.as_file_status.value,
                        elapsed=round(result.elapsed, 3),
                        char_count=result.char_count,
                        method=result.method.value,
                        message=result.message,
                        done=done_count,
                        total=total,
                        progress_pct=progress_pct,
                    ),
                )

                per_file_rows.append(
                    {
                        "file": result.file_path,
                        "status": result.outcome.value,
                        "method": result.method.value,
                        "seconds": f"{result.elapsed:.3f}",
                        "chars": str(result.char_count),
                        "message": result.message,
                        "confidence": result.confidence,
                        "flags": result.flags,
                    }
                )

    finally:
        stop_consumer.set()
        consumer.join(timeout=5)
        manager.shutdown()

    _log(
        f"Pipeline finished - Direct:{direct_success} OCR:{ocr_success} "
        f"Failed:{failures} Timeouts:{timeouts}"
    )

    if ocr_missing_flag.is_set():
        _log(
            "CRITICAL: OCR engine/binary is not installed. Pipeline stopped early.",
            level="error",
        )

    if run_id and db:
        # Update the run record with final status
        final_status = "done"
        is_cancelled = cancel_event is not None and cancel_event.is_set()
        is_timeout = False
        if total_timeout_seconds > 0:
            elapsed = (datetime.now() - start_time).total_seconds()
            is_timeout = elapsed > total_timeout_seconds

        if is_cancelled:
            final_status = "cancelled"
        elif is_timeout:
            final_status = "timeout"
        elif ocr_missing_flag.is_set():
            final_status = "failed"

        db.update_run(
            run_id,
            final_status,
            done_files=direct_success + ocr_success,
            failed_files=failures,
            direct_files=direct_success,
            ocr_files=ocr_success,
        )

    _emit_done(
        progress_callback,
        done_count,
        total,
        direct_success,
        ocr_success,
        failures,
        timeouts,
    )

    _write_summary(per_file_rows)


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------


def _write_summary(rows: list[dict]) -> None:
    """Persist a per-run CSV and JSON summary to 'benchmark_output/'.

    Filenames include the run datetime so each run's summary is preserved
    rather than overwritten
    """
    summary_dir = "benchmark_output"
    os.makedirs(summary_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        csv_path = os.path.join(summary_dir, f"pipeline_summary_{ts}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "file",
                    "status",
                    "method",
                    "seconds",
                    "chars",
                    "message",
                    "confidence",
                    "flags",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        logger.info("Benchmark CSV: %s", csv_path)
    except Exception as exc:
        logger.warning("Could not write CSV summary: %s", exc)

    try:
        json_path = os.path.join(summary_dir, f"pipeline_summary_{ts}.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        logger.info("Benchmark JSON: %s", json_path)
    except Exception as exc:
        logger.warning("Could not write JSON summary: %s", exc)

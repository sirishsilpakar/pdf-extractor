"""Background job state and lifecycle management.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

from extractor import run_pipeline


@dataclass
class FileEntry:
    name: str
    path: str
    size_bytes: int
    status: str = (
        "queued"  # queued | uploaded | processing | completed | failed | timeout
    )
    progress_pct: int = 0
    method: str = ""
    elapsed: float = 0.0
    char_count: int = 0
    message: str = ""
    current_page: int = 0
    total_pages: int = 0
    upload_rel: str = (
        ""  # relative path within upload UUID dir (for dir structure preservation)
    )


@dataclass
class JobState:
    status: str = "idle"  # idle | running | done | cancelled
    files: List[FileEntry] = field(default_factory=list)
    log_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    done_count: int = 0
    total_count: int = 0
    failed_count: int = 0
    direct_count: int = 0
    ocr_count: int = 0
    current_file: str = ""


# Singletons that exist for the entire lifetime of the server process
_state = JobState()
_cancel_event = threading.Event()
_state_lock = threading.Lock()
_broadcast_fn: Optional[Callable] = None


# Register a callback to broadcast state updates
# This is globally unique for each server process
def register_broadcast(fn: Callable):
    global _broadcast_fn
    _broadcast_fn = fn


def _broadcast(event: dict):
    if _broadcast_fn:
        _broadcast_fn(event)


def get_state() -> dict:
    with _state_lock:
        elapsed = _calc_elapsed()
        remaining = _state.total_count - _state.done_count
        eta = None
        if _state.done_count > 0 and elapsed > 0 and _state.status == "running":
            rate = _state.done_count / elapsed
            eta = round(remaining / rate) if rate > 0 else None

        return {
            "status": _state.status,
            "done": _state.done_count,
            "total": _state.total_count,
            "failed": _state.failed_count,
            "progress_pct": (
                int(_state.done_count / _state.total_count * 100)
                if _state.total_count > 0
                else 0
            ),
            "elapsed": elapsed,
            "eta_seconds": eta,
            "current_file": _state.current_file,
            "files": [_entry_dict(f) for f in _state.files],
            "log": list(_state.log_lines),
        }


def start_job(
    file_entries: List[FileEntry],
    input_dir: str,
    output_dir: str = "extracted_files",
    force: bool = False,
    settings: Optional[dict] = None,
) -> bool:
    """Start the pipeline in a background thread. Returns False if already running."""
    with _state_lock:
        if _state.status == "running":
            return False

        _cancel_event.clear()

        _state.status = "running"
        _state.files = list(file_entries)
        _state.log_lines.clear()
        _state.start_time = time.time()
        _state.end_time = None
        _state.done_count = 0
        _state.total_count = len(file_entries)
        _state.failed_count = 0
        _state.direct_count = 0
        _state.ocr_count = 0
        _state.current_file = ""

    if settings:
        _add_log(f"[INFO] Settings (UI stubs, not yet applied): {settings}")

    # Broadcast immediately so the UI sees the file list and "running" status
    _broadcast({"type": "state_update", **get_state()})

    thread = threading.Thread(
        target=_run, args=(input_dir, output_dir, force), daemon=True
    )
    thread.start()
    return True


def cancel_job():
    _cancel_event.set()
    with _state_lock:
        if _state.status == "running":
            _state.status = "cancelled"
    _broadcast({"type": "state_update", **get_state()})


def add_log_line(msg: str):
    _add_log(msg)


# Internal helper functions


def _calc_elapsed() -> float:
    """Calculate the elapsed time since the job started."""
    if _state.start_time is None:
        return 0.0
    if _state.status == "running":
        return round(time.time() - _state.start_time, 1)
    if _state.end_time:
        return round(_state.end_time - _state.start_time, 1)
    return 0.0


def _entry_dict(f: FileEntry) -> dict:
    """Convert a FileEntry to a dictionary."""
    return {
        "name": f.name,
        "path": f.path,
        "size_bytes": f.size_bytes,
        "status": f.status,
        "progress_pct": f.progress_pct,
        "method": f.method,
        "elapsed": f.elapsed,
        "char_count": f.char_count,
        "message": f.message,
        "current_page": f.current_page,
        "total_pages": f.total_pages,
    }


def _add_log(msg: str):
    """Add a log line to the job state and broadcast it."""
    with _state_lock:
        _state.log_lines.append(msg)
    _broadcast({"type": "log", "message": msg})


def _find_entry(file_path: str) -> Optional[FileEntry]:
    """Find a FileEntry by its file path."""
    name = os.path.basename(file_path)
    for entry in _state.files:
        if entry.path == file_path or entry.name == name:
            return entry
    return None


def _find_entry_by_name(name: str) -> Optional[FileEntry]:
    """Find a FileEntry by its name."""
    for entry in _state.files:
        if entry.name == name:
            return entry
    return None


# Progress callback (called from extractor thread)
def _progress_callback(event: dict):
    """Handle progress events from the extractor thread."""
    etype = event.get("type")

    # Plain log line
    if etype == "log":
        _add_log(event.get("message", ""))
        return

    # Worker picked up a file
    if etype == "file_started":
        file_path = event.get("file_path", "")
        basename = event.get("file", "")
        pid = event.get("pid", "?")

        with _state_lock:
            entry = _find_entry(file_path)
            if entry:
                entry.status = "processing"
                entry.progress_pct = 0
                entry.current_page = 0
            _state.current_file = basename

        _add_log(f"[INFO] Worker {pid} started → {basename}")
        _broadcast({"type": "state_update", **get_state()})
        return

    # Page finished (progress within a file)
    if etype == "page_done":
        basename = event.get("file", "")
        page = event.get("page", 0)
        total_pages = event.get("total_pages", 0)
        method = event.get("method", "")
        pid = event.get("pid", "?")
        secs = event.get("seconds", 0)

        with _state_lock:
            entry = _find_entry_by_name(basename)
            if entry and total_pages > 0:
                entry.current_page = page
                entry.total_pages = total_pages
                entry.progress_pct = min(99, int(page / total_pages * 100))

        log_tag = "OCR  " if method == "ocr" else "DIRECT"
        _add_log(
            f"[INFO] W{pid} · {basename}  page {page}/{total_pages}  "
            f"{log_tag}  {secs:.2f}s"
        )

        # Lightweight file_progress event so the UI can update just the row
        _broadcast(
            {
                "type": "file_progress",
                "file": basename,
                "pct": min(99, int(page / total_pages * 100)) if total_pages else 0,
                "page": page,
                "total_pages": total_pages,
            }
        )
        return

    # File completed / failed / timed out
    if etype in ("file_done", "file_failed", "file_timeout"):
        file_path = event.get("file_path", "")

        with _state_lock:
            entry = _find_entry(file_path)
            if entry:
                entry.status = event.get("status", "completed")
                entry.progress_pct = 100
                entry.method = event.get("method", "")
                entry.elapsed = event.get("elapsed", 0)
                entry.char_count = event.get("char_count", 0)
                entry.message = event.get("message", "")

            _state.done_count = event.get("done", _state.done_count)
            _state.current_file = ""

            if etype in ("file_failed", "file_timeout"):
                _state.failed_count += 1
            elif event.get("method") == "ocr":
                _state.ocr_count += 1
            else:
                _state.direct_count += 1

        _broadcast({"type": "state_update", **get_state()})
        return

    # All files done
    if etype == "done":
        with _state_lock:
            # Don't overwrite "cancelled" if the user cancelled mid-run
            if _state.status != "cancelled":
                _state.status = "done"
            _state.end_time = time.time()
            _state.current_file = ""

        _add_log(
            f"[OK ] Pipeline complete — "
            f"done:{event.get('done',0)} "
            f"direct:{event.get('direct',0)} "
            f"ocr:{event.get('ocr',0)} "
            f"failed:{event.get('failed',0)}"
        )
        _broadcast({"type": "state_update", **get_state()})


# Background thread
def _run(input_dir: str, output_dir: str, force: bool):
    """Run the pipeline in a background thread."""
    import logging
    from config import LOG_DIR

    try:
        run_pipeline(
            input_dir=input_dir,
            output_dir=output_dir,
            force=force,
            progress_callback=_progress_callback,
            cancel_event=_cancel_event,
            log_dir=LOG_DIR,
        )
    except Exception as exc:
        logging.exception(exc)
        _add_log(f"[ERROR] Pipeline crashed: {exc}")
        with _state_lock:
            _state.status = "done"
            _state.end_time = time.time()
        _broadcast({"type": "state_update", **get_state()})

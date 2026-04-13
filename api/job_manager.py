"""Encapsulated JobManager class replacing module-level globals.

- Plain class -> injectable via FastAPI 'Depends()' and unit-testable
- 'dict'based file index for O(1) lookups (vs O(n) linear scan)
- Handler dispatch table replaces long if-elif chain
- Typed 'JobStatus' / 'FileStatus' / 'ExtractionMethod' enums
- Broadcast decoupled from state mutation
- 'LogBuffer' value object for log management
- 'get_files_page()' for server-side pagination (SSE no longer carries files[])
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional

from core.events import (
    ExtractionMethod,
    FileStatus,
    JobStatus,
)


@dataclass
class FileEntry:
    name: str
    path: str
    size_bytes: int
    status: FileStatus = FileStatus.QUEUED
    progress_pct: int = 0
    method: ExtractionMethod = ExtractionMethod.DIRECT
    elapsed: float = 0.0
    char_count: int = 0
    message: str = ""
    current_page: int = 0
    total_pages: int = 0
    upload_rel: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "status": self.status.value,
            "progress_pct": self.progress_pct,
            "method": self.method.value,
            "elapsed": self.elapsed,
            "char_count": self.char_count,
            "message": self.message,
            "current_page": self.current_page,
            "total_pages": self.total_pages,
        }


class LogBuffer:
    """Fixed capacity FIFO log line store."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lines: Deque[str] = deque(maxlen=maxlen)

    def append(self, msg: str) -> None:
        self._lines.append(msg)

    def snapshot(self) -> list[str]:
        return list(self._lines)

    def clear(self) -> None:
        self._lines.clear()

    def __len__(self) -> int:
        return len(self._lines)


@dataclass
class _JobState:
    status: JobStatus = JobStatus.IDLE
    # O(1) lookups keyed by name and path
    _files_by_name: dict[str, FileEntry] = field(default_factory=dict)
    _files_by_path: dict[str, FileEntry] = field(default_factory=dict)
    log: LogBuffer = field(default_factory=LogBuffer)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    done_count: int = 0
    total_count: int = 0
    failed_count: int = 0
    direct_count: int = 0
    ocr_count: int = 0
    current_file: str = ""
    run_id: str = ""

    def reset(self, entries: list[FileEntry], run_id: str = "") -> None:
        self.status = JobStatus.RUNNING
        self._files_by_name = {e.name: e for e in entries}
        self._files_by_path = {e.path: e for e in entries}
        self.log.clear()
        self.start_time = time.time()
        self.end_time = None
        self.done_count = 0
        self.total_count = len(entries)
        self.failed_count = 0
        self.direct_count = 0
        self.ocr_count = 0
        self.current_file = ""
        self.run_id = run_id

    def find_by_name(self, name: str) -> Optional[FileEntry]:
        return self._files_by_name.get(name)

    def find_by_path(self, path: str) -> Optional[FileEntry]:
        entry = self._files_by_path.get(path)
        if entry is None:
            # Fallback: match by basename
            name = os.path.basename(path)
            entry = self._files_by_name.get(name)
        return entry

    def all_entries(self) -> list[FileEntry]:
        return list(self._files_by_name.values())

    def entries_page(self, page: int, size: int) -> tuple[int, list[FileEntry]]:
        all_e = self.all_entries()
        total = len(all_e)
        offset = (max(page, 1) - 1) * size
        return total, all_e[offset : offset + size]


class JobManager:
    """Manages the lifecycle of a single background extraction job.

    Designed to live as a singleton for the duration of the server process
    (injected via ``api.v1.deps.get_job_manager``)
    """

    def __init__(self, broadcast_fn: Optional[Callable[[dict], None]] = None) -> None:
        self._state = _JobState()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._broadcast_fn = broadcast_fn or (lambda _: None)

        # Handler dispatch table replaces long if-elif in _progress_callback
        self._handlers: dict[str, Callable[[dict], None]] = {
            "log": self._on_log,
            "file_started": self._on_file_started,
            "page_done": self._on_page_done,
            "file_done": self._on_file_complete,
            "file_failed": self._on_file_complete,
            "file_timeout": self._on_file_complete,
            "done": self._on_pipeline_done,
            "ocr_engine_missing": self._on_ocr_missing,
        }

    def register_broadcast(self, fn: Callable[[dict], None]) -> None:
        self._broadcast_fn = fn

    def start_job(
        self,
        file_entries: list[FileEntry],
        input_dir: str,
        output_dir: str = "extracted_files",
        force: bool = False,
        settings: Optional[dict] = None,
        ocr_engine=None,
        db=None,
    ) -> bool:
        """Start the pipeline in a background thread.

        Returns 'False' if a job is already running
        """
        import uuid as _uuid

        run_id = _uuid.uuid4().hex

        with self._lock:
            if self._state.status == JobStatus.RUNNING:
                return False
            self._cancel.clear()
            self._state.reset(file_entries, run_id=run_id)

        if settings:
            self._append_log(f"[INFO] Settings received: {settings}")

        self._emit({"type": "state_update", **self.get_status()})

        thread = threading.Thread(
            target=self._run,
            args=(input_dir, output_dir, force, ocr_engine, db, run_id),
            daemon=True,
        )
        thread.start()
        return True

    def cancel_job(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state.status == JobStatus.RUNNING:
                self._state.status = JobStatus.CANCELLED
        self._emit({"type": "state_update", **self.get_status()})

    def get_status(self) -> dict:
        """Return compact metadata dict (does not include the files list)"""
        with self._lock:
            elapsed = self._calc_elapsed()
            remaining = self._state.total_count - self._state.done_count
            eta = None
            if (
                self._state.done_count > 0
                and elapsed > 0
                and self._state.status == JobStatus.RUNNING
            ):
                rate = self._state.done_count / elapsed
                eta = round(remaining / rate) if rate > 0 else None

            total = self._state.total_count
            done = self._state.done_count

            return {
                "status": self._state.status.value,
                "done": done,
                "total": total,
                "failed": self._state.failed_count,
                "progress_pct": int(done / total * 100) if total > 0 else 0,
                "elapsed": elapsed,
                "eta_seconds": eta,
                "current_file": self._state.current_file,
                "log": self._state.log.snapshot(),
            }

    def get_files_page(self, page: int = 1, size: int = 50) -> tuple[int, list[dict]]:
        """Returns the file pages as (total, [file_entry_dict, ...])"""
        with self._lock:
            total, entries = self._state.entries_page(page, size)
        return total, [e.to_dict() for e in entries]

    def handle_event(self, event: dict) -> None:
        """Route an event dict to the appropriate handler (progress callback)"""
        etype = event.get("type", "")
        handler = self._handlers.get(etype)
        if handler:
            handler(event)

    # ------------------------------------------------------------------
    # Event handlers (dispatch table targets)
    # ------------------------------------------------------------------

    def _on_log(self, event: dict) -> None:
        self._append_log(event.get("message", ""))

    def _on_file_started(self, event: dict) -> None:
        file_path = event.get("file_path", "")
        basename = event.get("file", "")
        pid = event.get("pid", "?")

        with self._lock:
            entry = self._state.find_by_path(file_path)
            if entry:
                entry.status = FileStatus.PROCESSING
                entry.progress_pct = 0
                entry.current_page = 0
            self._state.current_file = basename

        self._append_log(f"[INFO] Worker {pid} → {basename}")
        self._emit({"type": "state_update", **self.get_status()})

    def _on_page_done(self, event: dict) -> None:
        basename = event.get("file", "")
        page = event.get("page", 0)
        total_pages = event.get("total_pages", 0)
        method = event.get("method", "")
        pid = event.get("pid", "?")
        secs = event.get("seconds", 0)

        with self._lock:
            entry = self._state.find_by_name(basename)
            if entry and total_pages > 0:
                entry.current_page = page
                entry.total_pages = total_pages
                entry.progress_pct = min(99, int(page / total_pages * 100))

        tag = "OCR" if method == ExtractionMethod.OCR.value else "DIRECT"
        self._append_log(
            f"[INFO] W{pid} · {basename}  pg {page}/{total_pages}  {tag}  {secs:.2f}s"
        )
        # Lightweight targeted event to avoid full state_update on every page
        self._emit(
            {
                "type": "file_progress",
                "file": basename,
                "pct": min(99, int(page / total_pages * 100)) if total_pages else 0,
                "page": page,
                "total_pages": total_pages,
            }
        )

    def _on_file_complete(self, event: dict) -> None:
        etype = event.get("type", "file_done")
        file_path = event.get("file_path", "")

        with self._lock:
            entry = self._state.find_by_path(file_path)
            if entry:
                entry.status = FileStatus(
                    event.get("status", FileStatus.COMPLETED.value)
                )
                entry.progress_pct = 100
                try:
                    entry.method = ExtractionMethod(
                        event.get("method", ExtractionMethod.DIRECT.value)
                    )
                except ValueError:
                    entry.method = ExtractionMethod.ERROR
                entry.elapsed = event.get("elapsed", 0)
                entry.char_count = event.get("char_count", 0)
                entry.message = event.get("message", "")

            self._state.done_count = event.get("done", self._state.done_count)
            self._state.current_file = ""

            if etype in ("file_failed", "file_timeout"):
                self._state.failed_count += 1
            elif event.get("method") == ExtractionMethod.OCR.value:
                self._state.ocr_count += 1
            else:
                self._state.direct_count += 1

        self._emit({"type": "state_update", **self.get_status()})

    def _on_pipeline_done(self, event: dict) -> None:
        with self._lock:
            if self._state.status != JobStatus.CANCELLED:
                self._state.status = JobStatus.DONE
            self._state.end_time = time.time()
            self._state.current_file = ""

        self._append_log(
            f"[OK] Pipeline complete - "
            f"done: {event.get('done', 0)} "
            f"direct: {event.get('direct', 0)} "
            f"ocr: {event.get('ocr', 0)} "
            f"failed: {event.get('failed', 0)}"
        )
        self._emit({"type": "state_update", **self.get_status()})

    def _on_ocr_missing(self, _event: dict) -> None:
        self._append_log("[ERROR] OCR engine/binary not installed - pipeline stopped.")

    # ------------------------------------------------------------------
    # Internal helper methods
    # ------------------------------------------------------------------

    def _calc_elapsed(self) -> float:
        if self._state.start_time is None:
            return 0.0
        if self._state.status == JobStatus.RUNNING:
            return round(time.time() - self._state.start_time, 1)
        if self._state.end_time:
            return round(self._state.end_time - self._state.start_time, 1)
        return 0.0

    def _append_log(self, msg: str) -> None:
        """Append to log buffer then broadcast state mutation and side effect separated"""
        with self._lock:
            self._state.log.append(msg)
        self._emit({"type": "log", "message": msg})

    def _emit(self, event: dict) -> None:
        """Broadcast an event, swallow exceptions so a stalled client never kills the pipeline"""
        try:
            self._broadcast_fn(event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(
        self,
        input_dir: str,
        output_dir: str,
        force: bool,
        ocr_engine,
        db,
        run_id: str = "",
    ) -> None:
        """Execute ``run_pipeline`` in a background daemon thread"""
        import logging as _logging
        from core.pipeline import run_pipeline

        try:
            run_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                force=force,
                progress_callback=self.handle_event,
                cancel_event=self._cancel,
                ocr_engine=ocr_engine,
                db=db,
                run_id=run_id or None,
            )
        except Exception as exc:
            _logging.exception("Pipeline thread crashed: %s", exc)
            self._append_log(f"[ERROR] Pipeline crashed: {exc}")
            with self._lock:
                self._state.status = JobStatus.DONE
                self._state.end_time = time.time()
            self._emit({"type": "state_update", **self.get_status()})


# Module-level singleton (used by server.py lifespan and deps.py)

_default_manager: Optional[JobManager] = None


def get_manager() -> JobManager:
    """Return the process level singleton ``JobManager``"""
    global _default_manager
    if _default_manager is None:
        _default_manager = JobManager()
    return _default_manager

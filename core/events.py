"""Typed event system for the PDF extraction pipeline.

Every event emitted by workers or the pipeline is defined here as a
dataclass with an associated Enum, giving a single source-of-truth for all
status strings used across the DB, API payloads, SSE messages, and the CLI.

Design: events are serialised to plain dicts before entering a
multiprocessing.Queue so they survive the pickle round-trip.  Use
'asdict(event)' from 'dataclasses' for that serialisation
"""

from __future__ import annotations

from dataclasses import asdict, dataclass  # noqa: F401 – re-exported for convenience
from enum import Enum
from typing import Any, Callable, Union


class JobStatus(str, Enum):
    """Overall job lifecycle status"""

    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


class FileStatus(str, Enum):
    """Per-file processing status"""

    QUEUED = "queued"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ExtractionMethod(str, Enum):
    """Extraction method used on a page or file"""

    DIRECT = "direct"
    OCR = "ocr"
    ERROR = "error"


class PipelineOutcome(str, Enum):
    """Raw result code returned by the worker process"""

    SUCCESS_DIRECT = "SUCCESS_DIRECT"
    SUCCESS_OCR = "SUCCESS_OCR"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"

    @property
    def is_success(self) -> bool:
        return self in (PipelineOutcome.SUCCESS_DIRECT, PipelineOutcome.SUCCESS_OCR)

    @property
    def as_file_status(self) -> FileStatus:
        if self == PipelineOutcome.TIMEOUT:
            return FileStatus.TIMEOUT
        return FileStatus.COMPLETED if self.is_success else FileStatus.FAILED

    @property
    def as_method(self) -> ExtractionMethod:
        if self == PipelineOutcome.SUCCESS_DIRECT:
            return ExtractionMethod.DIRECT
        if self == PipelineOutcome.SUCCESS_OCR:
            return ExtractionMethod.OCR
        return ExtractionMethod.ERROR

    @property
    def as_event_type(self) -> str:
        if self == PipelineOutcome.TIMEOUT:
            return "file_timeout"
        return "file_done" if self.is_success else "file_failed"


# ---------------------------------------------------------------------------
# Event dataclasses which are emitted by workers and the pipeline
# ---------------------------------------------------------------------------


@dataclass
class LogEvent:
    message: str
    level: str = "info"
    type: str = "log"


@dataclass
class FileStartedEvent:
    file: str
    file_path: str
    pid: int
    type: str = "file_started"


@dataclass
class PageDoneEvent:
    file: str
    file_path: str
    page: int
    total_pages: int
    method: str  # ExtractionMethod value
    pid: int
    seconds: float
    confidence: float | None = None
    type: str = "page_done"


@dataclass
class OcrEngineMissingEvent:
    type: str = "ocr_engine_missing"


@dataclass
class FileResult:
    """Returned by 'process_file()'; consumed by the pipeline to update the
    DB and emit downstream events.  Not put on the multiprocessing queue
    directly, returned via 'pool.imap_unordered'"""

    file_path: str
    outcome: PipelineOutcome
    message: str
    elapsed: float
    char_count: int
    method: ExtractionMethod
    page_count: int
    rel_path: str
    txt_path: str
    content_hash: str
    txt_hash: str
    confidence: float | None = None
    flags: list[str] | None = None


@dataclass
class FileDoneEvent:
    file: str
    file_path: str
    status: str  # FileStatus value
    elapsed: float
    char_count: int
    method: str  # ExtractionMethod value
    message: str
    done: int
    total: int
    progress_pct: int
    type: str = "file_done"


@dataclass
class FileFailedEvent:
    file: str
    file_path: str
    status: str
    elapsed: float
    char_count: int
    method: str
    message: str
    done: int
    total: int
    progress_pct: int
    type: str = "file_failed"


@dataclass
class FileTimeoutEvent:
    file: str
    file_path: str
    status: str
    elapsed: float
    char_count: int
    method: str
    message: str
    done: int
    total: int
    progress_pct: int
    type: str = "file_timeout"


@dataclass
class PipelineDoneEvent:
    done: int
    total: int
    direct: int
    ocr: int
    failed: int
    timeouts: int
    type: str = "done"


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# Union of all event types (for type-checker hints only not enforced at runtime)
Event = Union[
    LogEvent,
    FileStartedEvent,
    PageDoneEvent,
    OcrEngineMissingEvent,
    FileDoneEvent,
    FileFailedEvent,
    FileTimeoutEvent,
    PipelineDoneEvent,
]

# Callback signature expected by run_pipeline() which receives events as plain dicts
EventCallback = Callable[[dict[str, Any]], None]

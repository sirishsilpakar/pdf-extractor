"""Unit tests for the dynamic extraction method tracking in JobManager"""

from __future__ import annotations

import pytest

from api.job_manager import FileEntry, JobManager
from core.events import ExtractionMethod, FileStatus


def test_job_manager_dynamic_method_tracking():
    emitted_events = []

    def mock_broadcast(event: dict):
        emitted_events.append(event)

    jm = JobManager(broadcast_fn=mock_broadcast)

    file_entries = [
        FileEntry(name="test.pdf", path="/path/to/test.pdf", size_bytes=100)
    ]

    # Reset / start the job tracking
    jm._state.reset(file_entries, run_id="test_run_1")

    # 1. Start file progress event
    jm.handle_event(
        {
            "type": "file_started",
            "file": "test.pdf",
            "file_path": "/path/to/test.pdf",
            "pid": 1,
        }
    )

    # We should have emitted a file_progress starting event with method direct
    assert emitted_events[-2]["type"] == "file_progress"
    assert emitted_events[-2]["file"] == "test.pdf"
    assert emitted_events[-2]["method"] == "direct"

    # 2. First page finishes with 'ocr'
    jm.handle_event(
        {
            "type": "page_done",
            "file": "test.pdf",
            "file_path": "/path/to/test.pdf",
            "page": 1,
            "total_pages": 3,
            "method": "ocr",
            "pid": 1,
            "seconds": 0.5,
        }
    )

    # Overall method should transition to ocr (since 1 OCR > 0 DIRECT)
    assert emitted_events[-1]["type"] == "file_progress"
    assert emitted_events[-1]["file"] == "test.pdf"
    assert emitted_events[-1]["method"] == "ocr"

    # 3. Second page finishes with 'direct'
    jm.handle_event(
        {
            "type": "page_done",
            "file": "test.pdf",
            "file_path": "/path/to/test.pdf",
            "page": 2,
            "total_pages": 3,
            "method": "direct",
            "pid": 1,
            "seconds": 0.5,
        }
    )

    # Overall method should transition to direct (since 1 OCR is NOT > 1 DIRECT)
    assert emitted_events[-1]["type"] == "file_progress"
    assert emitted_events[-1]["file"] == "test.pdf"
    assert emitted_events[-1]["method"] == "direct"

    # 4. Third page finishes with 'ocr'
    jm.handle_event(
        {
            "type": "page_done",
            "file": "test.pdf",
            "file_path": "/path/to/test.pdf",
            "page": 3,
            "total_pages": 3,
            "method": "ocr",
            "pid": 1,
            "seconds": 0.5,
        }
    )

    # Overall method should transition to ocr (since 2 OCR > 1 DIRECT)
    assert emitted_events[-1]["type"] == "file_progress"
    assert emitted_events[-1]["file"] == "test.pdf"
    assert emitted_events[-1]["method"] == "ocr"

    # 5. File completes with outcome ocr
    jm.handle_event(
        {
            "type": "file_done",
            "file": "test.pdf",
            "file_path": "/path/to/test.pdf",
            "status": "completed",
            "elapsed": 1.5,
            "char_count": 100,
            "method": "ocr",
            "message": "success",
            "done": 1,
            "total": 1,
            "progress_pct": 100,
        }
    )

    assert emitted_events[-2]["type"] == "file_progress"
    assert emitted_events[-2]["file"] == "test.pdf"
    assert emitted_events[-2]["pct"] == 100
    assert emitted_events[-2]["method"] == "ocr"

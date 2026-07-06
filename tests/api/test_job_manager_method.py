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


def test_job_manager_sorting():
    jm = JobManager(broadcast_fn=lambda event: None)

    file_entries = [
        FileEntry(name="c_file.pdf", path="/path/to/c_file.pdf", size_bytes=100),
        FileEntry(name="a_file.pdf", path="/path/to/a_file.pdf", size_bytes=200),
        FileEntry(name="b_file.pdf", path="/path/to/b_file.pdf", size_bytes=300),
    ]

    jm._state.reset(file_entries, run_id="test_run_sorting")

    # Set different statuses and progress for entries to verify sorting
    entry_a = jm._state.find_by_name("a_file.pdf")
    entry_a.status = FileStatus.PROCESSING
    entry_a.progress_pct = 50

    entry_b = jm._state.find_by_name("b_file.pdf")
    entry_b.status = FileStatus.COMPLETED
    entry_b.progress_pct = 100

    entry_c = jm._state.find_by_name("c_file.pdf")
    entry_c.status = FileStatus.QUEUED
    entry_c.progress_pct = 0

    # Sort by name asc
    total, items = jm.get_files_page(page=1, size=10, sort_by="name", sort_order="asc")
    assert [i["name"] for i in items] == ["a_file.pdf", "b_file.pdf", "c_file.pdf"]

    # Sort by name desc
    total, items = jm.get_files_page(page=1, size=10, sort_by="name", sort_order="desc")
    assert [i["name"] for i in items] == ["c_file.pdf", "b_file.pdf", "a_file.pdf"]

    # Sort by progress asc
    total, items = jm.get_files_page(
        page=1, size=10, sort_by="progress", sort_order="asc"
    )
    assert [i["name"] for i in items] == ["c_file.pdf", "a_file.pdf", "b_file.pdf"]

    # Sort by progress desc
    total, items = jm.get_files_page(
        page=1, size=10, sort_by="progress", sort_order="desc"
    )
    assert [i["name"] for i in items] == ["b_file.pdf", "a_file.pdf", "c_file.pdf"]

    # Sort by status asc (completed, processing, queued)
    total, items = jm.get_files_page(
        page=1, size=10, sort_by="status", sort_order="asc"
    )
    assert [i["name"] for i in items] == ["b_file.pdf", "a_file.pdf", "c_file.pdf"]

    # Set size and method explicitly
    entry_a.size_bytes = 200
    entry_a.method = ExtractionMethod.DIRECT

    entry_b.size_bytes = 300
    entry_b.method = ExtractionMethod.OCR

    entry_c.size_bytes = 100
    entry_c.method = ExtractionMethod.ERROR

    # Sort by size desc
    total, items = jm.get_files_page(page=1, size=10, sort_by="size", sort_order="desc")
    assert [i["name"] for i in items] == ["b_file.pdf", "a_file.pdf", "c_file.pdf"]

    # Sort by method asc (direct < error < ocr)
    total, items = jm.get_files_page(
        page=1, size=10, sort_by="method", sort_order="asc"
    )
    assert [i["name"] for i in items] == ["a_file.pdf", "c_file.pdf", "b_file.pdf"]

    # Combined sort: status asc, name desc
    entry_a.status = FileStatus.PROCESSING
    entry_c.status = FileStatus.PROCESSING
    total, items = jm.get_files_page(
        page=1, size=10, sort_by="status,name", sort_order="asc,desc"
    )
    assert [i["name"] for i in items] == ["b_file.pdf", "c_file.pdf", "a_file.pdf"]

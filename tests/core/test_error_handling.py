import os
from unittest.mock import MagicMock, patch

import pytest

from core.events import ExtractionMethod, PipelineOutcome
from core.pipeline import run_pipeline
from core.worker import process_file
from db.repository import DatabaseRepository


def test_process_empty_file(tmp_path):
    input_file = tmp_path / "empty.pdf"
    input_file.write_bytes(b"")  # 0 bytes

    result = process_file(
        file_path=str(input_file),
        input_dir_root=str(tmp_path),
        output_dir_root=str(tmp_path / "outputs"),
    )
    assert result.outcome == PipelineOutcome.FAILURE
    assert result.method == ExtractionMethod.ERROR
    assert "empty_file" in result.flags
    assert "Empty file" in result.message


def test_process_nonexistent_file(tmp_path):
    input_file = tmp_path / "does_not_exist.pdf"

    result = process_file(
        file_path=str(input_file),
        input_dir_root=str(tmp_path),
        output_dir_root=str(tmp_path / "outputs"),
    )
    assert result.outcome == PipelineOutcome.FAILURE
    assert result.method == ExtractionMethod.ERROR
    assert "inaccessible_file" in result.flags
    assert "does not exist" in result.message.lower()


def test_process_protected_file(tmp_path):
    input_file = tmp_path / "protected.pdf"
    input_file.write_bytes(b"%PDF-1.4 dummy content")

    # Mock pymupdf.open to return a document with is_encrypted = True
    mock_doc = MagicMock()
    mock_doc.is_encrypted = True

    with patch("pymupdf.open", return_value=mock_doc):
        result = process_file(
            file_path=str(input_file),
            input_dir_root=str(tmp_path),
            output_dir_root=str(tmp_path / "outputs"),
        )
    assert result.outcome == PipelineOutcome.FAILURE
    assert result.method == ExtractionMethod.ERROR
    assert "protected_file" in result.flags
    assert "password-protected" in result.message.lower()


def test_pipeline_saves_failed_file(tmp_path):
    db_file = tmp_path / "test.db"
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Create an empty file in input_dir
    empty_pdf = input_dir / "empty.pdf"
    empty_pdf.write_bytes(b"")

    # Initialize repository
    db = DatabaseRepository(str(db_file))
    db.init_schema()

    # Run the pipeline
    run_pipeline(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        db=db,
        run_id="run-error-test",
    )

    # Verify that the failed file was saved in extracted_texts table
    total, rows = db.get_extracted_texts(run_id="run-error-test")
    assert total == 1
    row = rows[0]
    assert row["filename"] == "empty.pdf"
    assert row["method"] == "error"
    assert "empty_file" in row["flags"]
    assert "Empty file" in row["error_message"]

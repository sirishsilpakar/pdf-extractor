import os
import shutil

import pytest

from core.pipeline import run_pipeline
from db.repository import DatabaseRepository


def test_pipeline_run_dir_naming(tmp_path):
    # Setup temp paths
    db_file = tmp_path / "test.db"
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Create a dummy PDF in input_dir
    src_pdf = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "test_hybrid.pdf"
    )
    dest_pdf = input_dir / "test_hybrid.pdf"
    shutil.copy(src_pdf, dest_pdf)

    # Initialize repository
    db = DatabaseRepository(str(db_file))
    db.init_schema()

    # Verify initial run number is 1
    assert db.get_next_run_number() == 1

    # Run the pipeline
    run_pipeline(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        db=db,
        run_id="abc-123-xyz",
    )

    # Verify run number in DB is 1
    run = db.get_run("abc-123-xyz")
    assert run is not None
    assert run["run_number"] == 1

    # Check directory created
    expected_dir_name = "run1_abc-123-xyz"
    expected_path = output_dir / expected_dir_name
    assert os.path.exists(expected_path)
    assert os.path.isdir(expected_path)
    assert run["output_dir"] == str(expected_path)

    # Run again to verify run_number increments to 2
    run_pipeline(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        db=db,
        run_id="def-456-uvw",
        force=True,
    )

    run2 = db.get_run("def-456-uvw")
    assert run2 is not None
    assert run2["run_number"] == 2

    expected_dir_name2 = "run2_def-456-uvw"
    expected_path2 = output_dir / expected_dir_name2
    assert os.path.exists(expected_path2)
    assert os.path.isdir(expected_path2)
    assert run2["output_dir"] == str(expected_path2)

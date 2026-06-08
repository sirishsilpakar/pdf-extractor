"""Runs router /api/v1/runs

Each extraction pipeline invocation is stored as a run record
Endpoints: list (paginated), single, per-run file list, per run activity log
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from api.v1.deps import DBDep
from api.v1.schemas import (
    PagedResponse,
    ResultRecord,
    ResultTreeResponse,
    RunIdItem,
    RunRecord,
)

router = APIRouter()


@router.get(
    "",
    response_model=PagedResponse[RunRecord],
    summary="List all pipeline runs",
    description="Returns a paginated list of all extraction runs, newest first.",
)
async def list_runs(
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> PagedResponse[RunRecord]:
    total, rows = db.get_runs(page=page, size=size)
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[RunRecord(**r) for r in rows],
    )


@router.get(
    "/ids",
    response_model=PagedResponse[RunIdItem],
    summary="List all run IDs",
    description="Returns a paginated list of all run IDs with run numbers, newest first, for filtering",
)
async def list_run_ids(
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> PagedResponse[RunIdItem]:
    total, rows = db.get_all_run_ids(page=page, size=size)
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[RunIdItem(**r) for r in rows],
    )


@router.get(
    "/{run_id}",
    response_model=RunRecord,
    summary="Get a single run by ID",
)
async def get_run(
    run_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
) -> RunRecord:
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(404, detail=f"Run {run_id!r} not found.")
    return RunRecord(**row)


@router.get(
    "/{run_id}/tree",
    response_model=ResultTreeResponse,
    summary="Get directory and file tree for a run",
    description="Returns a structural breakdown of the run (directories and top-level files)",
)
async def get_run_tree(
    run_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
) -> ResultTreeResponse:
    if not db.get_run(run_id):
        raise HTTPException(404, detail=f"Run {run_id!r} not found.")
    tree = db.get_result_tree(run_id)

    tree["top_level_files"] = [ResultRecord(**r) for r in tree["top_level_files"]]

    return ResultTreeResponse.build(tree)


@router.get(
    "/{run_id}/files",
    response_model=PagedResponse[ResultRecord],
    summary="List files processed in a specific run",
    description="Returns a paginated list of extracted_texts records linked to this run.",
)
async def get_run_files(
    run_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    directory: str | None = Query(
        None, description="Optional relative directory path prefix to filter files"
    ),
) -> PagedResponse[ResultRecord]:
    # Verify run exists
    if not db.get_run(run_id):
        raise HTTPException(404, detail=f"Run {run_id!r} not found.")
    total, rows = db.get_run_files(
        run_id=run_id, page=page, size=size, directory=directory
    )
    # get_run_files returns minimal columns and fill missing ones with defaults
    items = []
    for r in rows:
        r.setdefault("run_id", run_id)
        r.setdefault("source_path", "")
        r.setdefault("txt_path", "")
        r.setdefault("run_started_at", None)
        items.append(ResultRecord(**r))
    return PagedResponse.build(total=total, page=page, size=size, items=items)


@router.get(
    "/{run_id}/log",
    response_class=PlainTextResponse,
    summary="Get the activity log for a run",
    description=(
        "Returns the per run activity log as plain text. "
        "The log is written to disk when the pipeline finishes. "
        "Returns 404 if the run does not exist or the log has not been written yet."
    ),
    tags=["Runs"],
)
async def get_run_log(
    run_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
) -> PlainTextResponse:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, detail=f"Run {run_id!r} not found.")

    log_path = run.get("log_path")
    if not log_path:
        raise HTTPException(
            404,
            detail=(
                f"Activity log for run {run_id!r} has not been written yet. "
                "The log is saved when the pipeline completes."
            ),
        )

    try:
        content = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise HTTPException(404, detail=f"Log file missing on disk: {log_path}")
    except Exception as exc:
        raise HTTPException(500, detail=f"Could not read log file: {exc}")

    return PlainTextResponse(content)

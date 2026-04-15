"""Runs router /api/v1/runs

Each extraction pipeline invocation is stored as a run record
Endpoints: list (paginated), single, per-run file list
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.v1.deps import DBDep
from api.v1.schemas import PagedResponse, ResultRecord, RunRecord

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
) -> PagedResponse[ResultRecord]:
    # Verify run exists
    if not db.get_run(run_id):
        raise HTTPException(404, detail=f"Run {run_id!r} not found.")
    total, rows = db.get_run_files(run_id=run_id, page=page, size=size)
    # get_run_files returns minimal columns and fill missing ones with defaults
    items = []
    for r in rows:
        r.setdefault("run_id", run_id)
        r.setdefault("source_path", "")
        r.setdefault("txt_path", "")
        r.setdefault("run_started_at", None)
        items.append(ResultRecord(**r))
    return PagedResponse.build(total=total, page=page, size=size, items=items)

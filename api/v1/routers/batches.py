"""Batch ingestion router: POST /api/v1/batches and GET /api/v1/batches/{id}/files.

POST /batches - registers a server side path, returns immediately (202) with
                scan_status='scanning'. Actual directory scan runs as a BackgroundTask.
                Progress is pushed via GET /api/events/batch (SSE).

GET /batches/{id}/files - paginated file list, available once scan_status='done'.

Backwards compatibility
-----------------------
The legacy POST /upload/reference endpoint in upload.py is kept unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from api.v1.deps import DBDep
from api.v1.enums import ScanStatus
from api.v1.schemas import PagedResponse
from api.v1.tasks.scan_batch import scan_batch_task

router = APIRouter()


# Request / response models


class RegisterBatchRequest(BaseModel):
    path: str = Field(
        description=(
            "Absolute path on the server's filesystem. "
            "A directory is walked recursively; a single .pdf file is registered as-is."
        )
    )


class BatchResponse(BaseModel):
    batch_id: str
    resolved_path: str
    mode: str
    is_folder: bool
    scan_status: str
    pdf_count: int
    already_processed_count: int | None = None
    error_message: str | None = None


class BatchFileItem(BaseModel):
    batch_id: str
    name: str
    rel_path: str
    size_bytes: int
    content_hash: str | None = None
    is_processed: bool
    method: str | None = None


# Routes


@router.post(
    "",
    response_model=BatchResponse,
    status_code=202,
    summary="Register a local path as a batch (non-blocking, idempotent)",
    description=(
        "If the path was already registered (and scanning completed), the existing batch "
        "is returned immediately, no duplicate scan is started. "
        "Pass ?reprocess=true to force a fresh scan regardless."
    ),
)
async def register_batch(
    req: RegisterBatchRequest,
    background_tasks: BackgroundTasks,
    db: DBDep = ...,  # type: ignore[assignment]
    reprocess: bool = False,
) -> BatchResponse:
    resolved = Path(req.path).resolve()

    if not resolved.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Path does not exist on the server: {req.path!r}",
        )

    if resolved.is_dir():
        is_folder = True
    elif resolved.suffix.lower() == ".pdf":
        is_folder = False
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Path must be a directory or a .pdf file, got: {req.path!r}",
        )

    # Look up existing batch by exact path to respect unique constraints
    existing = db.get_batch_by_path(str(resolved))

    if existing is not None:
        batch_id = existing["batch_id"]
        # Force set back to SCANNING and flush any historical state hints
        db.update_batch_status(batch_id, ScanStatus.SCANNING.value)
    else:
        batch_id = uuid.uuid4().hex
        db.create_batch(batch_id, str(resolved), "local_ref", is_folder)

    # Unconditionally trigger a lightweight background scan to guarantee that count changes
    # and deleted files are correctly synced even if the path was registered previously only
    background_tasks.add_task(scan_batch_task, batch_id, str(resolved), is_folder, db)

    return BatchResponse(
        batch_id=batch_id,
        resolved_path=str(resolved),
        mode="local_ref",
        is_folder=is_folder,
        scan_status=ScanStatus.SCANNING.value,
        pdf_count=existing["pdf_count"] if existing else 0,
        already_processed_count=None,
        error_message=None,
    )


@router.get(
    "/{batch_id}",
    response_model=BatchResponse,
    summary="Get details for a single batch",
)
async def get_batch(
    batch_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
) -> BatchResponse:
    b = db.get_batch(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id!r}")

    return BatchResponse(
        batch_id=b["batch_id"],
        resolved_path=b["resolved_path"],
        mode=b.get("mode", "local_ref"),
        is_folder=bool(b["is_folder"]),
        scan_status=b["scan_status"],
        pdf_count=b["pdf_count"],
        already_processed_count=b.get("already_processed_count"),
        error_message=b.get("error_message"),
    )


@router.get(
    "/{batch_id}/files",
    response_model=PagedResponse,
    summary="List files in a batch (paginated)",
)
async def list_batch_files(
    batch_id: str,
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),  # noqa: B008
    size: int = Query(50, ge=1, le=500),  # noqa: B008
    skip_processed: bool = Query(  # noqa: B008
        False,
        description=(
            "When true, only return files that have not yet been extracted. "
            "Use this after the user chooses to skip already-processed files "
            "so that the UI reflects the actual set queued for processing."
        ),
    ),
    sort_by: str | None = Query(  # noqa: B008
        None, description="Field to sort by: status, name, progress"
    ),
    sort_order: str = Query("asc", description="Sort order: asc, desc"),  # noqa: B008
) -> PagedResponse:
    batch = db.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id!r}")

    # Translate the API level boolean into the repository's generalised filter dict
    filters = {"is_processed": False} if skip_processed else None
    total, rows = db.get_batch_files(
        batch_id, page, size, filters=filters, sort_by=sort_by, sort_order=sort_order
    )

    items = [
        BatchFileItem(
            batch_id=r["batch_id"],
            name=r["name"],
            rel_path=r["rel_path"],
            size_bytes=r["size_bytes"],
            content_hash=r["content_hash"],
            is_processed=bool(r["is_processed"]),
            method=r["method"],
        )
        for r in rows
    ]

    return PagedResponse.build(total=total, page=page, size=size, items=items)

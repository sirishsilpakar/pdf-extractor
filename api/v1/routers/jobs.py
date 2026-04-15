"""Job control router /api/v1/job/

Endpoints: start, cancel, status, files (paginated)
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query

from api.job_manager import FileEntry, JobManager
from api.v1.deps import DBDep, JobManagerDep, OCRDep
from api.v1.schemas import (
    FileEntryResponse,
    JobStatusResponse,
    PagedResponse,
    StartJobRequest,
)
from config import OUTPUT_DIR as _PKG_OUTPUT_DIR
from config import UPLOAD_DIR

router = APIRouter()


@router.post(
    "/start",
    summary="Start the extraction pipeline",
    description=(
        "Start the pipeline for a set of previously uploaded file_ids."
        "Set 'force=true' to reprocess files whose hash is already in the DB."
    ),
)
async def start_job(
    req: StartJobRequest,
    db: DBDep = ...,  # type: ignore[assignment]
    ocr: OCRDep = ...,  # type: ignore[assignment]
    jm: JobManagerDep = ...,  # type: ignore[assignment]
) -> dict:
    jm: JobManager

    # Collect PDFs from each uploaded UUID directory
    entries: List[FileEntry] = []
    missing: List[str] = []

    for fid in req.file_ids:
        fid_dir = UPLOAD_DIR / fid
        if not fid_dir.exists():
            missing.append(fid)
            continue
        pdfs = list(fid_dir.rglob("*.pdf"))
        if not pdfs:
            missing.append(fid)
            continue
        for pdf in pdfs:
            entries.append(
                FileEntry(
                    name=pdf.name,
                    path=str(pdf),
                    size_bytes=pdf.stat().st_size,
                )
            )

    # Missing file_ids are non-fatal, they likely weren't uploaded because
    # the browser's pre-upload hash-check found them already processed
    if missing and not entries:
        raise HTTPException(
            400,
            detail=f"No PDFs found for any of the {len(missing)} file ID(s).",
        )
    if not entries:
        raise HTTPException(400, detail="No PDFs found in the uploaded file IDs.")

    # Build a manifest directory of hard-links (or symlinks as fallback)
    # Hard-links: zero data copy, zero extra disk space, instant at any scale
    # The pipeline's os.walk then sees a flat directory of all PDFs to process
    manifest_dir = UPLOAD_DIR / f"manifest_{uuid.uuid4().hex[:8]}"
    manifest_dir.mkdir()

    for entry in entries:
        dest = manifest_dir / entry.name
        # Resolve name collisions (different dirs may have same filename)
        if dest.exists():
            prefix = hex(abs(hash(entry.path)) % 0xFFFF)[2:]
            dest = manifest_dir / f"{prefix}_{entry.name}"
        try:
            os.link(entry.path, dest)  # hard-link means no copy, no extra space
        except OSError:
            try:
                dest.symlink_to(Path(entry.path).resolve())  # symlink fallback
            except OSError:
                shutil.copy2(entry.path, str(dest))  # copy as last resort
        entry.path = str(dest)

    # Resolve output directory
    output_dir = (
        str(Path(_PKG_OUTPUT_DIR) / req.output_dir)
        if not Path(req.output_dir).is_absolute()
        else req.output_dir
    )

    started = jm.start_job(
        file_entries=entries,
        input_dir=str(manifest_dir),
        output_dir=output_dir,
        force=req.force,
        settings=req.settings,
        ocr_engine=ocr,
        db=db,
    )

    if not started:
        raise HTTPException(409, detail="A job is already running.")

    resp: dict = {"ok": True, "total": len(entries)}
    if missing:
        resp["warnings"] = (
            f"{len(missing)} file ID(s) not found on disk "
            "(already processed or upload incomplete)."
        )
    return resp


@router.post("/cancel", summary="Cancel the running job")
async def cancel_job(jm: JobManagerDep = ...) -> dict:  # type: ignore[assignment]
    jm: JobManager
    jm.cancel_job()
    return {"ok": True}


@router.get(
    "/status",
    response_model=JobStatusResponse,
    summary="Get current job status",
    description="Returns metadata only no files[]. Fetch the file list via GET /job/files",
)
async def get_status(jm: JobManagerDep = ...) -> JobStatusResponse:  # type: ignore[assignment]
    jm: JobManager
    return JobStatusResponse(**jm.get_status())


@router.get(
    "/files",
    response_model=PagedResponse[FileEntryResponse],
    summary="Paginated active job file list",
    description=(
        "Returns one page of the current job's file entries. "
        "Poll this endpoint (or re-fetch on 'state_update' SSE events) "
        "instead of reading the files[] array from the SSE payload."
    ),
)
async def list_job_files(
    jm: JobManagerDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1, description="1-indexed page number"),
    size: int = Query(50, ge=1, le=200, description="Items per page"),
) -> PagedResponse[FileEntryResponse]:
    jm: JobManager
    total, items = jm.get_files_page(page, size)
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[FileEntryResponse(**item) for item in items],
    )

"""Job control router /api/v1/job/

Endpoints: start, cancel, status, files (paginated)

File ingestion
--------------
'StartJobRequest.file_ids' accepts two kinds of IDs, resolved in this order:

1. Upload IDs - UUID directories under 'UPLOAD_DIR' created by 'POST /upload'. Files are hard linked into a manifest directory.

2. Reference IDs - UUIDs registered by 'POST /upload/reference'. The pipeline reads files directly from the server's local path no copy is made.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query

from api.job_manager import FileEntry, JobManager
from api.v1 import ref_registry
from api.v1.deps import DBDep, JobManagerDep, OCRDep
from api.v1.schemas import (
    FileEntryResponse,
    JobStatusResponse,
    PagedResponse,
    StartJobRequest,
    ValidateDirectoryRequest,
)
from api.validators.filesystem import require_writable_directory
from common.fs.paths import resolve_output_path
from config import OUTPUT_DIR as _PKG_OUTPUT_DIR
from config import UPLOAD_DIR

router = APIRouter()


@router.post(
    "/validate-directory",
    summary="Validate if directory is writable",
)
async def validate_directory(req: ValidateDirectoryRequest) -> dict:
    target_path = resolve_output_path(req.path, _PKG_OUTPUT_DIR)
    require_writable_directory(target_path)
    return {"ok": True}


@router.post(
    "/start",
    summary="Start the extraction pipeline",
    description=(
        "Start the pipeline for a set of previously uploaded file_ids or "
        "reference IDs registered via POST /upload/reference. "
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

    # Collect PDFs (supports upload IDs, reference IDs, and batch IDs)
    entries: List[FileEntry] = []
    missing: List[str] = []
    # Track which entries came from references (processed in place, no hard link)
    ref_entries: List[FileEntry] = []
    # If partial files are picked, or it's a single file input, must use a manifest dir
    # to ensure pipeline iterates only targeted items and handles files correctly
    has_partial_selection = False

    if req.batch_ids:
        raise HTTPException(
            status_code=400,
            detail="'batch_ids' is not currently supported, use 'batch_id'.",
        )

    if not req.any_ids():
        raise HTTPException(400, detail="Provide at least one file_id or batch_id.")

    batch_ids = [req.batch_id] if req.batch_id else []

    for fid in req.file_ids:
        # Try as an uploaded file directory
        fid_dir = UPLOAD_DIR / fid
        if fid_dir.exists() and fid_dir.is_dir():
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
                        upload_rel=str(pdf.relative_to(fid_dir)),
                    )
                )
            continue

        # Try as a local path reference ID
        ref_path_str = ref_registry.lookup(fid)
        if ref_path_str is not None:
            ref_path = Path(ref_path_str)
            if not ref_path.exists():
                missing.append(fid)
                continue

            if ref_path.is_dir():
                # Filter by selected files if provided for this ref_id
                selected = (req.selected_files or {}).get(fid)
                if selected:
                    has_partial_selection = True
                    pdfs = [ref_path / f for f in selected if (ref_path / f).exists()]
                else:
                    # Recursive search as before (case-insensitive)
                    pdfs = [
                        p for p in ref_path.rglob("*") if p.suffix.lower() == ".pdf"
                    ]

                if not pdfs:
                    missing.append(fid)
                    continue
                for pdf in pdfs:
                    entry = FileEntry(
                        name=pdf.name,
                        path=str(pdf),
                        size_bytes=pdf.stat().st_size if pdf.exists() else 0,
                        # Store the relative path from the ref root for hierarchy
                        upload_rel=str(pdf.relative_to(ref_path)),
                    )
                    entries.append(entry)
                    ref_entries.append(entry)
            else:
                # Single PDF file reference --> Force manifest directory packaging
                has_partial_selection = True
                entry = FileEntry(
                    name=ref_path.name,
                    path=str(ref_path),
                    size_bytes=ref_path.stat().st_size,
                    upload_rel=ref_path.name,
                )
                entries.append(entry)
                ref_entries.append(entry)
            continue

        # Neither uploaded nor registered
        missing.append(fid)

    # Resolve batch_ids, look up the pre-scanned folder path from DB
    for bid in batch_ids:
        resolved_path_str = db.get_batch_resolved_path(bid)
        if resolved_path_str is None:
            missing.append(bid)
            continue
        ref_path = Path(resolved_path_str)
        if not ref_path.exists():
            missing.append(bid)
            continue
        if ref_path.is_dir():
            pdfs = [p for p in ref_path.rglob("*") if p.suffix.lower() == ".pdf"]
        else:
            # Single file batch --> Force manifest directory packaging to feed pipeline correctly
            has_partial_selection = True
            pdfs = [ref_path] if ref_path.suffix.lower() == ".pdf" else []
        if not pdfs:
            missing.append(bid)
            continue
        for pdf in pdfs:
            entry = FileEntry(
                name=pdf.name,
                path=str(pdf),
                size_bytes=pdf.stat().st_size if pdf.exists() else 0,
                upload_rel=(
                    str(pdf.relative_to(ref_path)) if ref_path.is_dir() else pdf.name
                ),
            )
            entries.append(entry)
            ref_entries.append(entry)

    # Missing IDs are only a hard error when nothing resolved
    if missing and not entries:
        raise HTTPException(
            400,
            detail=f"No PDFs found for any of the {len(missing)} ID(s): {missing[:5]}",
        )
    if not entries:
        raise HTTPException(400, detail="No PDFs found in the provided IDs.")

    # Build a manifest directory for uploaded files with hard links
    # Reference entries are excluded as the pipeline reads them in-place
    ref_paths = {e.path for e in ref_entries}
    upload_entries = [e for e in entries if e.path not in ref_paths]

    # Determine the input root for the pipeline
    # If all files are references from the same folder, use that folder directly
    # Otherwise (mix of uploads + references, or references from multiple folders),
    # build a manifest directory and symlink everything into it
    manifest_dir: Path | None = None

    if (
        upload_entries
        or has_partial_selection
        or (ref_entries and len({Path(e.path).parent for e in ref_entries}) > 1)
    ):
        # Need a unified manifest directory
        manifest_dir = UPLOAD_DIR / f"manifest_{uuid.uuid4().hex[:8]}"
        manifest_dir.mkdir()

        # Hard-link uploaded files
        for entry in upload_entries:
            rel = Path(entry.upload_rel) if entry.upload_rel else Path(entry.name)
            dest = manifest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                prefix = hex(abs(hash(entry.path)) % 0xFFFF)[2:]
                dest = dest.with_name(f"{prefix}_{dest.name}")
            try:
                os.link(entry.path, dest)
            except OSError:
                try:
                    dest.symlink_to(Path(entry.path).resolve())
                except OSError:
                    shutil.copy2(entry.path, str(dest))
            entry.path = str(dest)

        # Symlink reference files into manifest, preserving relative hierarchy
        for entry in ref_entries:
            rel = Path(entry.upload_rel) if entry.upload_rel else Path(entry.name)
            dest = manifest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    dest.symlink_to(Path(entry.path).resolve())
                except OSError:
                    shutil.copy2(entry.path, str(dest))
            entry.path = str(dest)

        input_dir = str(manifest_dir)

    elif ref_entries:
        # All files are from a single reference folder so can use it directly
        single_ref_root = str(Path(ref_entries[0].path).parent)
        # If it's a single file ref, the root is just that file's parent
        # For folder refs, reconstruct the original registered root
        registered_root = None
        if req.file_ids:
            ref_id_for_single = req.file_ids[0]
            registered_root = ref_registry.lookup(ref_id_for_single)
        elif batch_ids:
            registered_root = db.get_batch_resolved_path(batch_ids[0])

        input_dir = registered_root if registered_root else single_ref_root

    else:
        # No entries after filtering
        raise HTTPException(400, detail="No processable PDFs found.")

    # Resolve output directory
    output_dir = (
        str(Path(_PKG_OUTPUT_DIR) / req.output_dir)
        if not Path(req.output_dir).is_absolute()
        else req.output_dir
    )

    # Use request timeout if provided, otherwise fallback to global config
    from config import GLOBAL_JOB_TIMEOUT_SECONDS

    timeout_seconds = (
        req.timeout_seconds
        if req.timeout_seconds is not None
        else GLOBAL_JOB_TIMEOUT_SECONDS
    )

    # Compute skip_count so the job manager can report accurate progress via SSE
    # This mirrors the exact deduplication logic inside pipeline.run_pipeline():
    # files whose content hash are already present in extracted_texts will be skipped by the pipeline
    # count them here and pass it to jm.start_job() so total_count = queued files
    skip_count = 0
    if not req.force and db is not None:
        try:
            processed_hashes = db.get_processed_hashes()
            processed_filenames = db.get_processed_filenames()

            from services.hasher import compute_file_hash

            for entry in entries:
                try:
                    h = compute_file_hash(entry.path)
                    if h in processed_hashes:
                        skip_count += 1
                        entry.is_processed = True
                        continue
                except Exception:
                    pass
                if entry.name in processed_filenames:
                    skip_count += 1
                    entry.is_processed = True
        except Exception:
            # If we can't compute skip_count the total will be
            # slightly over-reported but processing will still work correctly
            skip_count = 0

    run_id = jm.start_job(
        file_entries=entries,
        input_dir=input_dir,
        output_dir=output_dir,
        force=req.force,
        settings=req.settings,
        ocr_engine=ocr,
        db=db,
        timeout_seconds=timeout_seconds,
        batch_ids=batch_ids,
        skip_count=skip_count,
    )

    if not run_id:
        raise HTTPException(409, detail="A job is already running.")

    resp: dict = {
        "ok": True,
        "total": len(entries) - skip_count,
        "skipped": skip_count,
        "run_id": run_id,
    }
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
    skip_processed: bool = Query(
        False,
        description=(
            "When true, only return files that have not yet been extracted. "
            "Use this after the user chooses to skip already-processed files "
            "so that the UI reflects the actual set queued for processing."
        ),
    ),
) -> PagedResponse[FileEntryResponse]:
    jm: JobManager
    total, items = jm.get_files_page(page, size, skip_processed)
    status = jm.get_status()
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[FileEntryResponse(**item) for item in items],
        run_id=status.get("run_id"),
    )

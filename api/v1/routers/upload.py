"""Upload router POST /api/v1/upload

Two ingestion modes
-------------------
1. File upload ('POST /'): Stream PDF files from the client to the server's 'uploads/' directory. Works for remote or local clients.

2. File reference ('POST /reference'): Register a local filesystem path (folder or single PDF) already on the server. No data is copied. Intended for desktop / local deployments where the backend and files share the same machine.

Both modes return an ID that is passed in 'StartJobRequest.file_ids'.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.v1 import ref_registry
from api.v1.deps import DBDep
from api.v1.schemas import (
    CheckHashesRequest,
    CheckHashesResponse,
    FileReferenceResponse,
    FileReferenceSchema,
    UploadedFileSchema,
)
from config import UPLOAD_CHUNK_SIZE, UPLOAD_DIR
from services.hasher import HASH_SAMPLE_BYTES

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def _stream_to_disk(upload: UploadFile, dest: Path) -> str:
    """Write upload to dest in chunks; return SHA-256 of first 64 KB.

    Peak memory per file: 'UPLOAD_CHUNK_SIZE' (default 1 MB), not the
    entire file
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    sampled = 0

    with open(dest, "wb") as out:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            out.write(chunk)
            if sampled < HASH_SAMPLE_BYTES:
                take = min(len(chunk), HASH_SAMPLE_BYTES - sampled)
                h.update(chunk[:take])
                sampled += take

    return h.hexdigest()


def _validate_pdf(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are accepted, got: {filename!r}",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=List[UploadedFileSchema],
    summary="Upload PDF files",
    description=(
        "Stream one or more PDF files to disk. Supports single files and entire "
        "folder trees (via 'webkitRelativePath' subpaths sent as the filename). "
        "Returns a 'file_id' and 'content_hash' per file. "
        "Pass hashes to 'POST /check-hashes' before starting the job to avoid "
        "reuploading already extracted files."
    ),
)
async def upload_pdfs(
    files: List[UploadFile] = File(...),
    db: DBDep = ...,  # type: ignore[assignment]
) -> List[UploadedFileSchema]:
    results = []

    for upload in files:
        _validate_pdf(upload.filename)

        file_id = uuid.uuid4().hex
        # Preserve relative sub-path for folder uploads (browser sends "subdir/file.pdf")
        safe_rel = Path(upload.filename).as_posix().lstrip("/").lstrip("../")
        dest = UPLOAD_DIR / file_id / safe_rel

        content_hash = await _stream_to_disk(upload, dest)
        size_bytes = dest.stat().st_size

        results.append(
            UploadedFileSchema(
                file_id=file_id,
                name=Path(upload.filename).name,
                size_bytes=size_bytes,
                content_hash=content_hash,
            )
        )

    return results


@router.post(
    "/check-hashes",
    response_model=CheckHashesResponse,
    summary="Bulk hash check",
    description=(
        "Given a list of SHA-256 hashes (one per candidate file), return which "
        "are already in the database.  The browser computes hashes client-side "
        "from the first 64 KB of each file before uploading, so files that are "
        "already extracted are never uploaded at all."
    ),
)
async def check_hashes(
    req: CheckHashesRequest,
    db: DBDep = ...,  # type: ignore[assignment]
) -> CheckHashesResponse:
    already = db.get_by_hashes(req.hashes)
    unprocessed = [h for h in req.hashes if h not in already]
    return CheckHashesResponse(
        already_processed=already,
        unprocessed=unprocessed,
    )


@router.post(
    "/reference",
    response_model=FileReferenceResponse,
    summary="Register a local server-side path (no upload)",
    description=(
        "Register a folder or single PDF file that already exists on the "
        "server's filesystem. No data is transferred, the pipeline will "
        "read the files directly from the given path. "
        "Returns a 'ref_id' that is passed in 'StartJobRequest.file_ids' "
        "alongside regular upload file_ids. "
        "Intended for desktop / local deployments where the backend and files "
        "share the same machine."
    ),
)
async def register_file_reference(
    req: FileReferenceSchema,
    db: DBDep = ...,  # type: ignore[assignment]
) -> FileReferenceResponse:
    resolved = Path(req.path).resolve()

    if not resolved.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Path does not exist on the server: {req.path!r}",
        )

    if resolved.is_dir():
        pdfs = [p for p in resolved.rglob("*") if p.suffix.lower() == ".pdf"]
        is_folder = True
    elif resolved.suffix.lower() == ".pdf":
        pdfs = [resolved]
        is_folder = False
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Path must be a directory or a .pdf file, got: {req.path!r}",
        )

    if not pdfs:
        raise HTTPException(
            status_code=400,
            detail=f"No PDF files found under: {req.path!r}",
        )

    # Check for duplicates in the database via hash check
    from api.v1.schemas import FileReferenceItem
    from services.hasher import compute_file_hash

    already_processed_count = 0
    hashes = []
    file_items = []

    for pdf_path in pdfs:
        h = ""
        try:
            # We only hash the first 64KB (HASH_SAMPLE_BYTES) as per our standard
            h = compute_file_hash(str(pdf_path))
            hashes.append(h)
        except Exception as e:
            print(f"DEBUG: Hashing failed for {pdf_path}: {e}")
            # Continue without hash, but still add to list

        file_items.append(
            FileReferenceItem(
                name=pdf_path.name,
                size_bytes=pdf_path.stat().st_size if pdf_path.exists() else 0,
                content_hash=h,
                rel_path=(
                    str(pdf_path.relative_to(resolved)) if is_folder else pdf_path.name
                ),
            )
        )

    if hashes:
        existing = db.get_by_hashes(hashes)
        already_processed_count = len(existing)

    ref_id = ref_registry.register(str(resolved))

    return FileReferenceResponse(
        ref_id=ref_id,
        resolved_path=str(resolved),
        pdf_count=len(pdfs),
        already_processed_count=already_processed_count,
        is_folder=is_folder,
        files=file_items,
    )

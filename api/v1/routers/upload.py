"""Upload router POST /api/v1/upload

Streams files to disk in fixed-size chunks so RAM usage stays constant
regardless of file count or file size.  Computes SHA-256 of the first 64 KB
as each file is written so the client immediately has a hash for dedup
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.v1.deps import DBDep
from api.v1.schemas import (
    CheckHashesRequest,
    CheckHashesResponse,
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
        size_before = 0

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

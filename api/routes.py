from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import database
from api import job_manager, sse

_PKG_DIR = Path(__file__).parent.parent
UPLOAD_DIR = _PKG_DIR / "uploads"
OUTPUT_DIR = _PKG_DIR / "extracted_files"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

router = APIRouter()

# OpenAPI tag groups
TAG_UPLOAD = "Upload"
TAG_JOB = "Job Control"
TAG_RESULTS = "Results"
TAG_FILES = "Extracted Files"
TAG_SEARCH = "Search"
TAG_WS = "WebSocket"


class StartRequest(BaseModel):
    file_ids: List[str]
    output_dir: Optional[str] = "extracted_files"
    force: bool = False
    settings: Optional[dict] = None


class UploadedFile(BaseModel):
    file_id: str
    name: str  # basename only
    size_bytes: int


class CheckFilesRequest(BaseModel):
    names: List[str]


@router.post(
    "/upload",
    response_model=List[UploadedFile],
    tags=[TAG_UPLOAD],
    summary="Upload PDF files",
    description="Accept one or more PDF uploads (single files or an entire folder tree). "
    "Returns a `file_id` for each upload to pass to `/api/start`.",
)
async def upload_files(files: List[UploadFile] = File(...)):
    results = []
    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            raise HTTPException(
                400, detail=f"Only PDF files accepted: {upload.filename}"
            )

        file_id = str(uuid.uuid4())
        dest_dir = UPLOAD_DIR / file_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Preserve the relative sub-path sent by the browser (webkitRelativePath
        # is forwarded as the filename for folder uploads: "subdir/file.pdf").
        safe_rel = Path(upload.filename).as_posix().lstrip("/").lstrip("../")
        dest_path = dest_dir / safe_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        content = await upload.read()
        dest_path.write_bytes(content)

        results.append(
            UploadedFile(
                file_id=file_id,
                name=Path(upload.filename).name,
                size_bytes=len(content),
            )
        )
    return results


# For Job control


@router.post(
    "/start",
    tags=[TAG_JOB],
    summary="Start the extraction pipeline",
    description="Kick off the pipeline for a set of previously uploaded `file_id`s. "
    "Set `force=true` to reprocess files that were already extracted.",
)
async def start_job(req: StartRequest):
    if not req.file_ids:
        raise HTTPException(400, detail="No file_ids provided.")

    entries: List[job_manager.FileEntry] = []
    missing: List[str] = []

    # Collect PDFs from each UUID upload dir, preserving directory structure
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
            rel = str(pdf.relative_to(fid_dir))  # e.g. "subfolder/file.pdf"
            entries.append(
                job_manager.FileEntry(
                    name=pdf.name,
                    path=str(pdf),
                    size_bytes=pdf.stat().st_size,
                    upload_rel=rel,
                )
            )

    if missing:
        raise HTTPException(404, detail=f"File IDs not found: {missing}")
    if not entries:
        raise HTTPException(400, detail="No PDFs found in uploaded files.")

    # Create batch dir, preserving original folder structure
    batch_dir = UPLOAD_DIR / f"batch_{uuid.uuid4().hex[:8]}"
    batch_dir.mkdir()
    for entry in entries:
        rel = Path(entry.upload_rel) if entry.upload_rel else Path(entry.name)
        dest = batch_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.path, str(dest))
        entry.path = str(dest)
        entry.upload_rel = ""  # no longer needed after copy

    output_dir = str(_PKG_DIR / req.output_dir)

    started = job_manager.start_job(
        file_entries=entries,
        input_dir=str(batch_dir),
        output_dir=output_dir,
        force=req.force,
        settings=req.settings,
    )

    if not started:
        raise HTTPException(409, detail="A job is already running.")

    return {"ok": True, "total": len(entries)}


@router.post(
    "/cancel",
    tags=[TAG_JOB],
    summary="Cancel the running job",
    description="Signals the pipeline to stop after the current file completes. "
    "Status transitions to `cancelled`.",
)
async def cancel_job():
    job_manager.cancel_job()
    return {"ok": True}


@router.get(
    "/status",
    tags=[TAG_JOB],
    summary="Get current job status",
    description="Returns the full job state: status, progress %, ETA, per-file rows, and the last 500 log lines.",
)
async def get_status():
    return job_manager.get_state()


# Check already-processed files
@router.post(
    "/check_files",
    tags=[TAG_RESULTS],
    summary="Check which files have already been extracted",
    description="Given a list of PDF filenames, returns which ones are already present "
    "in the `extracted_texts` table so the UI can offer a skip/reprocess choice.",
)
async def check_files(req: CheckFilesRequest):
    processed = {}
    for name in req.names:
        rec = database.get_extracted_by_filename(name)
        if rec:
            processed[name] = rec
    unprocessed = [n for n in req.names if n not in processed]
    return {"processed": processed, "unprocessed": unprocessed}


@router.get(
    "/results",
    tags=[TAG_RESULTS],
    summary="List all extraction results",
    description="Returns metadata for every extracted document (no content). "
    "Use `GET /api/results/{id}` to fetch the actual text.",
)
async def list_results(limit: int = 500, offset: int = 0):
    return database.get_extracted_texts(limit=limit, offset=offset)


@router.get(
    "/results/{result_id:int}",
    tags=[TAG_RESULTS],
    summary="Get extracted text for a single result",
    description="Fetches the record from the DB and reads the `.txt` file from disk. "
    "Returns full extracted text plus metadata.",
)
async def get_result(result_id: int):
    row = database.get_extracted_text_by_id(result_id)
    if not row:
        raise HTTPException(404, detail="Result not found.")
    txt_path = row.get("txt_path", "")
    try:
        content = Path(txt_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise HTTPException(404, detail=f"Text file not found on disk: {txt_path}")
    except Exception as exc:
        raise HTTPException(500, detail=f"Could not read file: {exc}")
    return {**row, "content": content}


@router.delete(
    "/results/{result_id:int}",
    tags=[TAG_RESULTS],
    summary="Remove a result record",
    description="Deletes the DB record only. The `.txt` file on disk is **not** deleted.",
)
async def delete_result(result_id: int):
    database.delete_extracted_text(result_id)
    return {"ok": True}


@router.get(
    "/files",
    tags=[TAG_FILES],
    summary="List extracted .txt files on disk",
    description="Walks the `extracted_files/` output directory and returns all `.txt` files with metadata.",
)
async def list_files():
    results = []
    for root, _, files in os.walk(OUTPUT_DIR):
        for fname in sorted(files):
            if fname.endswith(".txt"):
                full = os.path.join(root, fname)
                stat = os.stat(full)
                rel = os.path.relpath(full, OUTPUT_DIR)
                results.append(
                    {
                        "name": fname,
                        "rel_path": rel,
                        "size_bytes": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
    return results


@router.get(
    "/search",
    tags=[TAG_SEARCH],
    summary="Full-text search across extracted documents",
    description="Scans all `.txt` files in the output directory for the query string and returns matching snippets.",
)
async def search(q: str = Query(..., min_length=1)):
    q_lower = q.lower()
    matches = []
    for root, _, files in os.walk(OUTPUT_DIR):
        for fname in files:
            if not fname.endswith(".txt"):
                continue
            full = os.path.join(root, fname)
            try:
                text = Path(full).read_text(encoding="utf-8", errors="ignore")
                idx = text.lower().find(q_lower)
                if idx >= 0:
                    start = max(0, idx - 80)
                    end = min(len(text), idx + 80 + len(q))
                    snippet = text[start:end].replace("\n", " ").strip()
                    matches.append(
                        {
                            "file": fname,
                            "rel_path": os.path.relpath(full, OUTPUT_DIR),
                            "snippet": snippet,
                        }
                    )
            except Exception:
                continue
    return {"query": q, "count": len(matches), "results": matches}


@router.delete(
    "/files/{rel_path:path}",
    tags=[TAG_FILES],
    summary="Delete an extracted .txt file",
    description="Deletes the `.txt` and its companion `.meta.json` from disk.",
)
async def delete_file(rel_path: str):
    target = OUTPUT_DIR / rel_path
    if not target.exists() or not target.is_file():
        raise HTTPException(404, detail="File not found.")
    target.unlink()
    meta = target.with_suffix("").with_suffix(".meta.json")
    if meta.exists():
        meta.unlink()
    return {"ok": True}


# Server-Sent Events
@router.get(
    "/events",
    tags=[TAG_WS],
    summary="SSE real-time event stream",
    description=(
        "Long-lived HTTP stream (`text/event-stream`). "
        "Pushes `state_update`, `log`, and `file_progress` JSON objects. "
        "The browser's built-in `EventSource` API handles reconnection automatically."
    ),
)
async def sse_events(request: Request):
    """One-directional server-push stream.  The browser never sends data here;
    commands (start, cancel) go through the regular REST endpoints.
    """
    q = await sse.subscribe()

    async def generate():
        # Immediately push the current state so the UI syncs on connect.
        initial = json.dumps({"type": "state_update", **job_manager.get_state()})
        yield f"data: {initial}\n\n"

        try:
            while True:
                # Check if the client closed the tab / navigated away.
                if await request.is_disconnected():
                    break
                try:
                    # Block up to 25 s waiting for the next event.
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment line keeps the connection alive without
                    # triggering `onmessage` in the browser.
                    yield ": keepalive\n\n"
        finally:
            sse.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tells nginx not to buffer this response
            "Connection": "keep-alive",
        },
    )

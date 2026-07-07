"""Extracted files router - /api/v1/files/

Paginated listing of '.txt' output files on disk, and deletion
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from api.v1.deps import DBDep
from api.v1.schemas import ExtractedFileRecord, PagedResponse
from config import OUTPUT_DIR

if TYPE_CHECKING:
    from db.repository import DatabaseRepository

router = APIRouter()


def _collect_files(output_dir: Path, db: "DatabaseRepository") -> list[dict]:
    """Go through the output_dir and return metadata for all '.txt' files"""
    db_map = {}
    with db._lock:
        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT txt_hash, confidence, flags FROM extracted_texts WHERE txt_hash IS NOT NULL"
            ).fetchall()
            db_map = {
                r["txt_hash"]: {"confidence": r["confidence"], "flags": r["flags"]}
                for r in rows
            }
        finally:
            conn.close()

    results = []
    for root, _, fnames in os.walk(output_dir):
        for fname in sorted(fnames):
            if not fname.endswith(".txt"):
                continue
            full = Path(root) / fname
            try:
                stat = full.stat()
                conf = None
                flags_str = None

                rel_path = full.relative_to(output_dir).as_posix()

                from services.hasher import compute_file_hash

                current_txt_hash = compute_file_hash(full)

                if current_txt_hash in db_map:
                    conf = db_map[current_txt_hash]["confidence"]
                    flags_str = db_map[current_txt_hash]["flags"]

                results.append(
                    {
                        "name": fname,
                        "rel_path": rel_path,
                        "size_bytes": stat.st_size,
                        "modified": stat.st_mtime,
                        "confidence": conf,
                        "flags": flags_str,
                    }
                )
            except OSError:
                continue
    return results


@router.get(
    "",
    response_model=PagedResponse[ExtractedFileRecord],
    summary="List extracted .txt files on disk (paginated)",
)
async def list_files(
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
) -> PagedResponse[ExtractedFileRecord]:
    output_dir = Path(OUTPUT_DIR)
    all_files = _collect_files(output_dir, db)
    total = len(all_files)
    offset = (page - 1) * size
    page_items = all_files[offset : offset + size]
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[ExtractedFileRecord(**f) for f in page_items],
    )


@router.delete(
    "/{rel_path:path}",
    summary="Delete an extracted .txt file",
    description="Deletes the '.txt' and its companion '.meta.json' from disk.",
)
async def delete_file(rel_path: str) -> dict:
    output_dir = Path(OUTPUT_DIR)
    target = output_dir / rel_path
    if not target.exists() or not target.is_file():
        raise HTTPException(404, detail="File not found.")
    target.unlink()
    meta = target.with_suffix("").with_suffix(".meta.json")
    if meta.exists():
        meta.unlink()
    return {"ok": True}

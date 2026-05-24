"""Results router /api/v1/results/

Paginated extraction result listing, single record fetch with text content,
record deletion, and file download
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from api.v1.deps import DBDep
from api.v1.schemas import PagedResponse, ResultDetail, ResultRecord, RunTreeResponse

router = APIRouter()


@router.get(
    "",
    response_model=PagedResponse[ResultRecord],
    summary="List extraction results (paginated)",
    description="Returns metadata only, text content is fetched via GET /{id}.",
)
async def list_results(
    db: DBDep = ...,  # type: ignore[assignment]
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    run_id: Optional[str] = Query(None, description="Filter by a specific run ID"),
    directory: Optional[str] = Query(
        None, description="Filter by relative path prefix (directory)"
    ),
) -> PagedResponse[ResultRecord]:
    total, rows = db.get_extracted_texts(
        page=page, size=size, run_id=run_id, rel_path_prefix=directory
    )
    return PagedResponse.build(
        total=total,
        page=page,
        size=size,
        items=[ResultRecord(**r) for r in rows],
    )


@router.get(
    "/tree",
    response_model=RunTreeResponse,
    summary="Get directory tree",
    description="Returns a structural breakdown of the results (directories and top-level files)",
)
async def get_results_tree(
    run_id: Optional[str] = Query(None, description="Filter by run ID"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: DBDep = ...,  # type: ignore[assignment]
) -> RunTreeResponse:
    tree = db.get_run_tree(run_id, page=page, size=size)

    tree["top_level_files"] = [ResultRecord(**r) for r in tree["top_level_files"]]
    return RunTreeResponse.build(tree)


@router.get(
    "/{result_id}/download",
    summary="Download extracted .txt file",
    description="Streams the extracted text file as a downloadable attachment.",
    response_class=FileResponse,
)
async def download_result(
    result_id: int,
    db: DBDep = ...,  # type: ignore[assignment]
) -> FileResponse:
    row = db.get_by_id(result_id)
    if not row:
        raise HTTPException(404, detail="Result not found.")
    txt_path = Path(row.get("txt_path", ""))
    if not txt_path.exists():
        raise HTTPException(404, detail=f"Text file not found on disk: {txt_path}")
    filename = txt_path.name
    return FileResponse(
        path=str(txt_path),
        media_type="text/plain; charset=utf-8",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{result_id}",
    response_model=ResultDetail,
    summary="Get extracted text for a single result",
)
async def get_result(
    result_id: int,
    db: DBDep = ...,  # type: ignore[assignment]
) -> ResultDetail:
    row = db.get_by_id(result_id)
    if not row:
        raise HTTPException(404, detail="Result not found.")

    txt_path = row.get("txt_path", "")
    try:
        content = Path(txt_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise HTTPException(404, detail=f"Text file not found on disk: {txt_path}")
    except Exception as exc:
        raise HTTPException(500, detail=f"Could not read file: {exc}")

    return ResultDetail(**row, content=content)


@router.delete(
    "/{result_id}",
    summary="Remove a result record",
    description="Deletes the DB record and its FTS5 index entries. The '.txt' file on disk is NOT deleted.",
)
async def delete_result(
    result_id: int,
    db: DBDep = ...,  # type: ignore[assignment]
) -> dict:
    db.fts_delete_doc(result_id)  # remove FTS pages first
    db.delete_by_id(result_id)
    return {"ok": True}

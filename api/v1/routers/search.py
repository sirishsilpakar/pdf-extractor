"""Search router GET /api/v1/search

Full-text search using SQLite FTS5 per-page index
Falls back gracefully with a helpful message if the FTS index is empty
Includes a reindex endpoint for building the index from existing extractions
"""

from __future__ import annotations

import asyncio
import math
from typing import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.v1.deps import DBDep
from api.v1.schemas import SearchResponse, SearchResultItem

router = APIRouter()


@router.get(
    "",
    response_model=SearchResponse,
    summary="Full-text search across extracted documents (FTS5, paginated)",
    description=(
        "BM25-ranked search across the per-page FTS5 index. "
        "Results are grouped by document; each hit shows the best-matching page, "
        "a highlighted snippet ('<mark>' tags), and the page number. "
        "Returns HTTP 503 with a hint if the index is empty."
    ),
)
async def search(
    q: str = Query(..., min_length=1, description="Search term or FTS5 expression"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: DBDep = ...,  # type: ignore[assignment]
) -> SearchResponse:
    if db.fts_is_empty():
        raise HTTPException(
            503,
            detail=(
                "The full-text search index is empty. "
                "Trigger a rebuild via POST /api/v1/search/reindex."
            ),
        )

    total, rows = db.fts_search(q, page=page, size=size)

    items = [
        SearchResultItem(
            result_id=r["doc_id"],
            file=r["filename"],
            rel_path=r["rel_path"],
            page_no=r["page_no"],
            snippet=r["snippet"],
        )
        for r in rows
    ]

    return SearchResponse(
        query=q,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if size > 0 else 0,
        results=items,
    )


@router.post(
    "/reindex",
    summary="Rebuild the FTS5 search index",
    description=(
        "Rebuilds the per-page FTS5 index from all 'extracted_texts' records "
        "by reading each '.txt' file from disk and splitting it into page-sized chunks. "
        "Runs in the background; progress is reported via the SSE stream ('/api/events'). "
        "Call this once after upgrading to enable search on existing extractions."
    ),
)
async def reindex(
    background_tasks: BackgroundTasks,
    db: DBDep = ...,  # type: ignore[assignment]
) -> dict:
    def _run():
        from api import sse
        import asyncio

        loop = sse._loop  # type: ignore[attr-defined]

        def _cb(done: int, total: int):
            if loop and not loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    sse.broadcast(
                        {
                            "type": "log",
                            "message": f"[FTS reindex] {done}/{total} documents indexed",
                        }
                    ),
                    loop,
                )

        count = db.fts_rebuild(progress_cb=_cb)
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                sse.broadcast(
                    {
                        "type": "log",
                        "message": f"[FTS reindex] Complete - {count} documents indexed.",
                    }
                ),
                loop,
            )

    background_tasks.add_task(_run)
    return {
        "ok": True,
        "message": "FTS reindex started: progress visible in the activity log.",
    }

"""The FastAPI application factory.

Usage:
    app = create_app()          # imported by server.py
    uvicorn.run("api.app:app")  # auto-calls create_app via module load
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api.lifespan import lifespan
from api.v1.router import v1_router
from config import SERVE_UI

_PKG_DIR = Path(__file__).parent.parent
_UI_DIR = _PKG_DIR / "ui"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="PDF TextExtract API",
        version="1.0.0",
        description=(
            "REST API for the PDF TextExtract pipeline.\n\n"
            "Interactive docs: `/docs` (Swagger UI) · `/redoc` (ReDoc)\n\n"
            "SSE stream: `GET /api/events` — real-time progress & log stream.\n\n"
            "All resource endpoints are versioned under `/api/v1/`."
        ),
        openapi_tags=[
            {"name": "Upload", "description": "Upload PDF files for processing."},
            {
                "name": "Job Control",
                "description": "Start, cancel, and monitor the pipeline.",
            },
            {"name": "Results", "description": "Browse DB-indexed extraction results."},
            {"name": "Extracted Files", "description": "Manage raw .txt output files."},
            {
                "name": "Search",
                "description": "Full-text search across extracted documents.",
            },
        ],
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Versioned REST routes
    app.include_router(v1_router, prefix="/api")

    # SSE stream
    app.add_api_route("/api/events", _sse_endpoint, tags=["Events"])

    # Static UI only mounted when SERVE_UI is True (default)
    # Pass --no-ui or set SERVE_UI=false to run in headless / API-only mode
    if SERVE_UI and _UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_UI_DIR)), name="ui")

        @app.get("/")
        async def index():
            return FileResponse(str(_UI_DIR / "index.html"))

    return app


# ---------------------------------------------------------------------------
# SSE endpoint (defined here to keep routes.py clean)
# ---------------------------------------------------------------------------


async def _sse_endpoint(request: Request):
    """Long-lived SSE stream.  One queue per connected client."""
    import asyncio

    from api import job_manager as _jm
    from api import sse

    q = await sse.subscribe()

    async def generate():
        # Push current state immediately so the UI syncs on connect
        initial = json.dumps({"type": "state_update", **_jm.get_manager().get_status()})
        yield f"data: {initial}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            sse.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


app = create_app()

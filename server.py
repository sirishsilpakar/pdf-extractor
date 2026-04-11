"""FastAPI application entry point.

Launch:
    python server.py
    # or: pdf-extractor-ui (after pip install -e .)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is on sys.path when launched as a script
sys.path.insert(0, str(Path(__file__).parent))

from api import job_manager, sse
from api.routes import router


@asynccontextmanager
async def lifespan(application: "FastAPI"):
    # Startup: wire up WebSocket loop reference and initialise DB
    loop = asyncio.get_event_loop()
    sse.set_event_loop(loop)
    job_manager.register_broadcast(sse.broadcast)

    import database

    database.init_db()
    print("PDF TextExtract server ready -> http://localhost:8080")
    yield
    # Shutdown: cancel any running job cleanly
    job_manager.cancel_job()


app = FastAPI(
    title="PDF TextExtract API",
    version="2.0.0",
    description=(
        "REST API for the PDF TextExtract pipeline.\n\n"
        "**Interactive docs:** `/docs` (Swagger UI) · `/redoc` (ReDoc)\n\n"
        "**SSE stream:** `GET http://localhost:8080/api/events` real-time progress & log stream."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Upload", "description": "Upload PDF files for processing."},
        {
            "name": "Job Control",
            "description": "Start, cancel, and monitor the pipeline.",
        },
        {
            "name": "Results",
            "description": "Browse DB-indexed extraction results and read text content.",
        },
        {
            "name": "Extracted Files",
            "description": "Manage raw `.txt` output files on disk.",
        },
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

# Mount static files (CSS, JS)
UI_DIR = Path(__file__).parent / "ui"
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

# Register all REST + WS routes under /api
app.include_router(router, prefix="/api")


@app.get("/")
async def index():
    return FileResponse(str(UI_DIR / "index.html"))


# Entry point
def start():
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",  # shows every HTTP request in the terminal
    )


if __name__ == "__main__":
    start()

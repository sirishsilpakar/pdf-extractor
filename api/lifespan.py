"""Startup / shutdown lifespan for the FastAPI application"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire up event loop reference for SSE, initialise DB, log server ready"""
    from api import sse
    from api.job_manager import get_manager
    from api.v1.deps import get_db

    loop = asyncio.get_event_loop()
    sse.set_event_loop(loop)

    # Wire broadcast into the singleton JobManager
    jm = get_manager()
    jm.register_broadcast(sse.broadcast)

    # Initialise DB schema (idempotent — called for side-effect only)
    get_db()

    # Warn if no OCR engine is available
    try:
        from api.v1.deps import get_ocr_engine

        engine = get_ocr_engine()
        print(f"[startup] OCR engine: {engine.name}")
    except Exception as exc:
        print(f"[startup] WARNING: {exc}")
        print("[startup] OCR is disabled - direct extraction only.")

    print("PDF TextExtract server ready -> http://localhost:8080")
    print("  Docs: http://localhost:8080/docs")

    yield

    # Shutdown: stop any running job cleanly
    jm.cancel_job()

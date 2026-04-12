"""API v1 router, aggregates all sub-routers under /v1"""

from __future__ import annotations

from fastapi import APIRouter

from api.v1.routers import files, jobs, results, runs, search, upload

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(upload.router, prefix="/upload", tags=["Upload"])
v1_router.include_router(jobs.router, prefix="/job", tags=["Job Control"])
v1_router.include_router(results.router, prefix="/results", tags=["Results"])
v1_router.include_router(files.router, prefix="/files", tags=["Extracted Files"])
v1_router.include_router(search.router, prefix="/search", tags=["Search"])
v1_router.include_router(runs.router, prefix="/runs", tags=["Runs"])

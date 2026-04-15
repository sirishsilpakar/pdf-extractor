"""FastAPI dependency providers for API v1.

All injectable singletons live here.  Route functions declare them via
'Annotated[T, Depends(get_xxx)]' type aliases defined at the bottom,
keeping function signatures clean.

Testing: override any dependency with 'app.dependency_overrides[get_db] = lambda: fake_db'
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from config import DB_PATH
from db.repository import DatabaseRepository
from services.ocr.base import OCREngine


@lru_cache(maxsize=1)
def get_db() -> DatabaseRepository:
    """Process-level singleton DatabaseRepository.

    '@lru_cache' ensures only one instance is created even if 'get_db'
    is called from multiple concurrent requests
    """
    db = DatabaseRepository(DB_PATH)
    db.init_schema()
    return db


def get_ocr_engine() -> OCREngine:
    """Return the first available registered OCR engine.

    Raises 'RuntimeError' if no engine is available (caught by the startup
    lifespan and logged as a warning - OCR-free operation is still possible)
    """
    # Imported here to trigger services/__init__.py registration on first call
    import services  # noqa: F401
    from services.ocr.registry import get_default_engine

    return get_default_engine()


def get_job_manager():
    """Return the process level singleton JobManager"""
    from api.job_manager import get_manager

    return get_manager()


# Annotated type aliases
DBDep = Annotated[DatabaseRepository, Depends(get_db)]
OCRDep = Annotated[OCREngine, Depends(get_ocr_engine)]
JobManagerDep = Annotated[
    object, Depends(get_job_manager)
]  # typed as object to avoid circular

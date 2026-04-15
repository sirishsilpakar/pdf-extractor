"""OCR engine registry

Engines are registered at module import time (in 'services/__init__.py')
Both the CLI and the API layer call 'get_default_engine()' to obtain the
first engine that passes 'is_available()'
"""

from __future__ import annotations

from services.ocr.base import OCREngine

_registry: list[OCREngine] = []


def register(engine: OCREngine) -> None:
    """Append engine to the global registry

    Should be called once at startup, e.g. in 'services/__init__.py'
    """
    _registry.append(engine)


def get_default_engine() -> OCREngine:
    """Return the first registered engine that passes 'is_available()'

    Raises:
        RuntimeError: If no registered engine is installed
    """
    for engine in _registry:
        if engine.is_available():
            return engine

    names = [e.name for e in _registry] or ["<none>"]
    raise RuntimeError(
        f"No OCR engine available (checked: {names}). "
        "Install Tesseract and pytesseract, or register an alternative engine."
    )


def list_engines() -> list[tuple[str, bool]]:
    """Return [(name, is_available)] for every registered engine"""
    return [(e.name, e.is_available()) for e in _registry]


def get_all() -> list[OCREngine]:
    """Return all registered engines (available or not)"""
    return list(_registry)

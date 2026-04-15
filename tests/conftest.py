"""Shared pytest fixtures for all test suites"""

from __future__ import annotations

import pytest

from db.repository import DatabaseRepository
from services.ocr.base import OCREngine, OCRResult

# ---------------------------------------------------------------------------
# In-memory database fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path) -> DatabaseRepository:
    """A fresh DatabaseRepository backed by a temp file (not :memory: so WAL works)"""
    db = DatabaseRepository(str(tmp_path / "test.db"))
    db.init_schema()
    return db


# ---------------------------------------------------------------------------
# Mock OCR engine fixture
# ---------------------------------------------------------------------------


class MockOCREngine(OCREngine):
    """Deterministic OCR engine for tests — returns a fixed string"""

    def __init__(self, text: str = "mock OCR text", available: bool = True) -> None:
        self._text = text
        self._available = available

    @property
    def name(self) -> str:
        return "mock-ocr"

    def is_available(self) -> bool:
        return self._available

    def run(self, image, **kwargs) -> OCRResult:
        return OCRResult(text=self._text, engine=self.name)


class UnavailableMockOCREngine(MockOCREngine):
    def __init__(self) -> None:
        super().__init__(available=False)


@pytest.fixture
def mock_ocr() -> MockOCREngine:
    return MockOCREngine()


@pytest.fixture
def unavailable_ocr() -> UnavailableMockOCREngine:
    return UnavailableMockOCREngine()

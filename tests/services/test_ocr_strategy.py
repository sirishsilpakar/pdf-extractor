"""Tests for the OCR strategy interface and registry"""

from __future__ import annotations

import pytest

from services.ocr.base import OCREngine, OCRResult
from services.ocr.registry import get_all, get_default_engine, list_engines, register

# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


class AlwaysAvailableEngine(OCREngine):
    @property
    def name(self) -> str:
        return "always-available"

    def is_available(self) -> bool:
        return True

    def run(self, image, **kwargs) -> OCRResult:
        return OCRResult(text="test text", engine=self.name)


class NeverAvailableEngine(OCREngine):
    @property
    def name(self) -> str:
        return "never-available"

    def is_available(self) -> bool:
        return False

    def run(self, image, **kwargs) -> OCRResult:
        return OCRResult(text="", engine=self.name, error="not installed")


def test_ocr_result_succeeded():
    r = OCRResult(text="hello", engine="test")
    assert r.succeeded is True


def test_ocr_result_failed_empty():
    r = OCRResult(text="", engine="test")
    assert r.succeeded is False


def test_ocr_result_engine_missing():
    r = OCRResult(text="", engine="test", error="engine_missing")
    assert r.engine_missing is True


def test_always_available_engine():
    e = AlwaysAvailableEngine()
    assert e.is_available() is True
    assert e.name == "always-available"


def test_never_available_engine():
    e = NeverAvailableEngine()
    assert e.is_available() is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Each test gets an isolated registry."""
    import services.ocr.registry as reg

    monkeypatch.setattr(reg, "_registry", [])


def test_register_and_get_default():
    register(AlwaysAvailableEngine())
    engine = get_default_engine()
    assert engine.name == "always-available"


def test_get_default_skips_unavailable():
    register(NeverAvailableEngine())
    register(AlwaysAvailableEngine())
    engine = get_default_engine()
    assert engine.name == "always-available"


def test_get_default_raises_if_none_available():
    register(NeverAvailableEngine())
    with pytest.raises(RuntimeError, match="No OCR engine"):
        get_default_engine()


def test_list_engines():
    register(AlwaysAvailableEngine())
    register(NeverAvailableEngine())
    result = list_engines()
    assert ("always-available", True) in result
    assert ("never-available", False) in result

"""Tests for core/transform.py"""

from core.transform import preprocess_text, remove_page_numbers


def test_preprocess_removes_hyphenation():
    assert "word" in preprocess_text("wo-\nrd")


def test_preprocess_collapses_whitespace():
    result = preprocess_text("hello   world")
    assert "  " not in result


def test_preprocess_removes_bullets():
    result = preprocess_text("• item one ● item two")
    assert "•" not in result
    assert "●" not in result


def test_preprocess_returns_string():
    assert isinstance(preprocess_text("some text"), str)


def test_remove_page_numbers_standalone_digit():
    # Standalone numbers like "42" should be removed
    result = remove_page_numbers("some text page 42 more text")
    assert "42" not in result


def test_preprocess_empty_string():
    assert preprocess_text("") == ""

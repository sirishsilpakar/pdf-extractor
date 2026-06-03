"""Unit tests verifying behavior of control character stripping and list marker preservation"""

from core.transform import DocumentSanitizer, preprocess_text


def test_control_character_stripping_simple():
    """Verify that \x07 (bell) and other control characters are stripped from text."""
    input_text = "Hello\x07 World!\x01 This is a\x1f test."
    expected = "Hello World! This is a test."
    # Test via direct preprocess_text helper
    assert preprocess_text(input_text) == expected


def test_sanitizer_preserves_surrounding_text_on_control_chars():
    """Verify that DocumentSanitizer removes control characters but preserves valid line contents"""
    pages = [
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "bbox": [10, 45, 80, 55],
                            "text": "Valid text with \x07 bell character",
                        },
                    ],
                }
            ],
        }
    ]

    sanitizer = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": False,
            "debug_visualize": False,
        }
    )

    clean_pages = sanitizer.process_pages(pages)
    assert len(clean_pages) == 1
    assert clean_pages[0] == "Valid text with  bell character"


def test_list_markers_preserved_without_formatting():
    """Verify list markers (bullets, numbered lists) are kept when apply_text_formatting is False"""
    pages = [
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 20, 80, 30], "text": "• First item"},
                        {"bbox": [10, 35, 80, 45], "text": "1. Second item"},
                        {"bbox": [10, 50, 80, 60], "text": "- Third item"},
                    ],
                }
            ],
        }
    ]

    sanitizer = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": False,
            "debug_visualize": False,
        }
    )

    clean_pages = sanitizer.process_pages(pages)
    assert len(clean_pages) == 1
    text = clean_pages[0]
    assert "•" in text
    assert "1." in text
    assert "-" in text

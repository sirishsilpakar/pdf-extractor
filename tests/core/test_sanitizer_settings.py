"""Unit tests verifying behavior of DocumentSanitizer configuration options"""

from core.transform import DocumentSanitizer


def _create_mock_document():
    # Helper to generate a 3 page document with common headers, footers, and body text
    return [
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 5, 50, 15], "text": "Common Header Text"},
                        {
                            "bbox": [10, 45, 80, 55],
                            "text": "Body content on page 1 with number 12345.",
                        },
                        {"bbox": [10, 90, 50, 95], "text": "Common Footer Text"},
                    ],
                }
            ],
        },
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 5, 50, 15], "text": "Common Header Text"},
                        {
                            "bbox": [10, 45, 80, 55],
                            "text": "Body content on page 2 with number 67890.",
                        },
                        {"bbox": [10, 90, 50, 95], "text": "Common Footer Text"},
                    ],
                }
            ],
        },
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 5, 50, 15], "text": "Common Header Text"},
                        {
                            "bbox": [10, 45, 80, 55],
                            "text": "Body content on page 3 with number 11111.",
                        },
                        {"bbox": [10, 90, 50, 95], "text": "Common Footer Text"},
                    ],
                }
            ],
        },
    ]


def test_remove_header_only():
    """Verify remove_header=True and remove_footer=False only strips headers"""
    pages = _create_mock_document()
    sanitizer = DocumentSanitizer(
        config={
            "remove_header": True,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": False,
            "debug_visualize": False,
        }
    )

    clean_pages = sanitizer.process_pages(pages)
    text = "\n\n".join(clean_pages)

    assert "Common Header Text" not in text
    assert "Common Footer Text" in text


def test_remove_footer_only():
    """Verify remove_header=False and remove_footer=True only strips footers"""
    pages = _create_mock_document()
    sanitizer = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": True,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": False,
            "debug_visualize": False,
        }
    )

    clean_pages = sanitizer.process_pages(pages)
    text = "\n\n".join(clean_pages)

    assert "Common Header Text" in text
    assert "Common Footer Text" not in text


def test_remove_numeric_values():
    """Verify remove_numeric_values removes digits while keeping text"""
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
                            "text": "The year is 2026 and count is 42.",
                        },
                    ],
                }
            ],
        }
    ]

    # With remove_numeric_values=True
    sanitizer_true = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": True,
            "apply_text_formatting": True,
            "debug_visualize": False,
        }
    )
    clean_true = sanitizer_true.process_pages(pages)
    text_true = " ".join(clean_true)
    assert "2026" not in text_true
    assert "42" not in text_true

    # With remove_numeric_values=False
    sanitizer_false = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": True,
            "debug_visualize": False,
        }
    )
    clean_false = sanitizer_false.process_pages(pages)
    text_false = " ".join(clean_false)
    assert "2026" in text_false
    assert "42" in text_false


def test_apply_text_formatting_collapses_newlines():
    """Verify apply_text_formatting=True removes/collapses all newlines within and between pages"""
    pages = [
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 30, 80, 40], "text": "Line One"},
                        {"bbox": [10, 45, 80, 55], "text": "Line Two"},
                    ],
                }
            ],
        },
        {
            "height": 100,
            "width": 100,
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {"bbox": [10, 30, 80, 40], "text": "Line Three"},
                    ],
                }
            ],
        },
    ]

    # When apply_text_formatting is True
    sanitizer_format = DocumentSanitizer(
        config={
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": True,
            "debug_visualize": False,
        }
    )
    clean_format = sanitizer_format.process_pages(pages)

    # Joining with space (or checking join_char logic in transform.py / worker)
    # The DocumentSanitizer process_pages returns list[str], where each page_text is run through regex pipeline.
    # In regex pipeline, RE_WHITESP (which includes newlines) replaces newlines within pages.
    # This verifires that page strings returned have no newlines.
    for p in clean_format:
        assert "\n" not in p

    # Test our page joiner helper logic (using space when apply_text_formatting is enabled)
    join_char = (
        " " if sanitizer_format._get_bool_config("apply_text_formatting") else "\n\n"
    )
    final_text = join_char.join(clean_format)
    assert "\n" not in final_text

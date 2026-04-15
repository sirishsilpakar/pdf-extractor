import os
import sys
from unittest.mock import MagicMock, patch

# Mock pytesseract's binary not found error
mock_pytesseract = MagicMock()


class TesseractNotFoundError(Exception):
    pass


mock_pytesseract.TesseractNotFoundError = TesseractNotFoundError
mock_pytesseract.image_to_string.side_effect = TesseractNotFoundError(
    "tesseract binary not found"
)

from extractor import run_pipeline

with patch("extractor.pytesseract", mock_pytesseract):
    # This will trigger the new error handling logic
    run_pipeline(input_dir="test_input", force=True)

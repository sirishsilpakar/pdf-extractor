"""Services package.

Registers built-in OCR engines into the global registry at import time so
that both the CLI and API can call 'get_default_engine()' without any
manual wiring
"""

from config import (
    TESSERACT_LANG,
    TESSERACT_OEM,
    TESSERACT_PAGE_TIMEOUT_SECONDS,
    TESSERACT_PSM,
)
from services.ocr.registry import register
from services.ocr.tesseract import TesseractOCREngine

register(
    TesseractOCREngine(
        lang=TESSERACT_LANG,
        oem=TESSERACT_OEM,
        psm=TESSERACT_PSM,
        timeout=TESSERACT_PAGE_TIMEOUT_SECONDS,
    )
)

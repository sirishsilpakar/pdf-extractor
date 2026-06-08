"""Abstract OCR engine contract (Strategy pattern).

To add a new OCR backend:
  1. Subclass 'OCREngine'
  2. Implement 'name', 'is_available()', and 'run()'
  3. Call 'services.ocr.registry.register(MyEngine(...))' — typically in
     the engine's own module or in 'services/__init__.py'

All implementations MUST be picklable so they can be forwarded to
'multiprocessing.Pool' workers via the pool initialiser
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OCRResult:
    """Normalised result returned by any OCR engine"""

    text: str
    engine: str
    confidence: float | None = None
    error: str | None = None

    # The page_dict is a dictionary that contains the bounding boxes of the words
    # A dict[int, dict[int, dict[int, dict[str, list[int]]]]] where the keys are
    # block_num, par_num, line_num, and word_idx respectively
    # The value is a list of 4 integers representing the bounding box of the word
    # [x1, y1, x2, y2]
    # This needs to be implemented in each OCR engine to ensure that the
    # page dict is structured correctly and consistently
    page_dict: dict | None = None

    # Sentinel value emitted when the engine binary is not installed
    ENGINE_MISSING_SENTINEL: str = "__OCR_ENGINE_MISSING__"

    @property
    def succeeded(self) -> bool:
        """True when OCR produced text without a fatal error."""
        return self.error is None and bool(self.text)

    @property
    def engine_missing(self) -> bool:
        """True when the engine or its binary is not installed."""
        return self.error == "engine_missing"


class OCREngine(ABC):
    """Abstract base class for all OCR engine implementations.

    Implementations must be picklable (store only plain scalars, no open
    file handles or sockets) so they survive the 'multiprocessing'
    boundary
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human readable engine identifier, e.g. ''tesseract-lstm''"""

    @abstractmethod
    def is_available(self) -> bool:
        """Return 'True' only if the engine *and* all its dependencies are
        installed and functional.  Called once at startup"""

    @abstractmethod
    def run(self, image: "PIL.Image.Image", **kwargs) -> OCRResult:
        """Run OCR on a greyscale PIL Image.

        Args:
            image: Single-page greyscale PIL Image
            **kwargs: Engine-specific runtime overrides ('lang', 'timeout')

        Returns:
            'OCRResult' with '.text' (may be empty) and '.engine' set
            Never raises, errors are captured in 'OCRResult.error'
        """

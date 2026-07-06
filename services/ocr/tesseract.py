"""Tesseract OCR engine implementation of the OCREngine strategy.

All configuration is stored as plain picklable scalars so instances survive
the 'multiprocessing.Pool' initialiser round-trip without issues
"""

from __future__ import annotations

import logging
import os

from services.ocr.base import OCREngine, OCRResult

logger = logging.getLogger(__name__)


class TesseractOCREngine(OCREngine):
    """OCR engine backed by Tesseract via 'pytesseract'

    Attributes:
        lang:    Tesseract language string, e.g. 'eng+deu'
        oem:     OCR Engine Mode (0=legacy, 1=LSTM, 3=auto)
        psm:     Page Segmentation Mode (6 = single uniform block)
        timeout: Per-page timeout in seconds
    """

    def __init__(
        self,
        lang: str = "eng+deu",
        oem: int = 1,
        psm: int = 6,
        timeout: int = 30,
    ) -> None:
        self._lang = lang
        self._oem = oem
        self._psm = psm
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"tesseract-oem{self._oem}-psm{self._psm}"

    def is_available(self) -> bool:
        try:
            import pytesseract  # noqa: F401

            custom_cmd = os.getenv("TESSERACT_CMD")
            if custom_cmd:
                logger.debug("Using custom Tesseract command: %s", custom_cmd)
                pytesseract.pytesseract.tesseract_cmd = custom_cmd

            version = pytesseract.get_tesseract_version()
            logger.info("Tesseract engine available (version: %s)", version)
            return True
        except Exception as exc:
            logger.error("Tesseract engine check failed: %s", exc)
            return False

    def run(self, image: "PIL.Image.Image", **kwargs) -> OCRResult:
        lang = kwargs.get("lang", self._lang)
        timeout = kwargs.get("timeout", self._timeout)
        config = f"--oem {self._oem} --psm {self._psm}"

        try:
            import pytesseract

            custom_cmd = os.getenv("TESSERACT_CMD")
            if custom_cmd:
                pytesseract.pytesseract.tesseract_cmd = custom_cmd

            data = pytesseract.image_to_data(
                image,
                lang=lang,
                config=config,
                timeout=timeout,
                output_type=pytesseract.Output.DICT,
            )

            words = []
            confidences = []
            blocks_map = {}

            for i, word in enumerate(data.get("text", [])):
                word = word.strip()
                if not word:
                    continue

                conf = data["conf"][i]
                if conf != "-1":
                    words.append(word)
                    try:
                        confidences.append(float(conf) / 100.0)
                    except ValueError:
                        pass

                    b_num = data["block_num"][i]

                    # Unique line identifier within block
                    l_num = (data["par_num"][i], data["line_num"][i])

                    left = data["left"][i]
                    top = data["top"][i]
                    right = left + data["width"][i]
                    bottom = top + data["height"][i]

                    if b_num not in blocks_map:
                        blocks_map[b_num] = {}

                    # Build the lines and group them by paragraph and line number
                    # so that it matches the expected format of "blocks" in the OCRResult
                    if l_num not in blocks_map[b_num]:
                        blocks_map[b_num][l_num] = {
                            "words": [],
                            "x0": left,
                            "y0": top,
                            "x1": right,
                            "y1": bottom,
                        }
                    else:
                        l_data = blocks_map[b_num][l_num]
                        l_data["x0"] = min(l_data["x0"], left)
                        l_data["y0"] = min(l_data["y0"], top)
                        l_data["x1"] = max(l_data["x1"], right)
                        l_data["y1"] = max(l_data["y1"], bottom)

                    blocks_map[b_num][l_num]["words"].append(word)

            page_blocks = []
            for b_num in sorted(blocks_map.keys()):
                block_lines = []
                # Sort lines by paragraph and line number
                for l_num in sorted(blocks_map[b_num].keys()):
                    l_data = blocks_map[b_num][l_num]
                    block_lines.append(
                        {
                            "bbox": (
                                l_data["x0"],
                                l_data["y0"],
                                l_data["x1"],
                                l_data["y1"],
                            ),
                            "text": " ".join(l_data["words"]),
                        }
                    )
                page_blocks.append({"type": 0, "lines": block_lines})

            page_dict = {
                "width": float(image.width),
                "height": float(image.height),
                "blocks": page_blocks,
            }

            text = " ".join(words)
            avg_conf = sum(confidences) / len(confidences) if confidences else None

            return OCRResult(
                text=text, engine=self.name, confidence=avg_conf, page_dict=page_dict
            )

        except ImportError as exc:
            logger.error("pytesseract not installed: %s", exc)
            return OCRResult(text="", engine=self.name, error="engine_missing")

        except Exception as exc:
            err_str = str(exc).lower()
            type_name = type(exc).__name__.lower()
            if (
                "tesseractnotfounderror" in type_name
                or "tesseract is not installed" in err_str
                or ("not found" in err_str and "tesseract" in err_str)
            ):
                logger.error("Tesseract binary missing: %s", exc)
                return OCRResult(text="", engine=self.name, error="engine_missing")

            logger.error("Tesseract OCR failed on page: %s", exc)
            return OCRResult(text="", engine=self.name, error=str(exc))

    # ------------------------------------------------------------------
    # Pickle support (required for multiprocessing.Pool)
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict:
        return {
            "lang": self._lang,
            "oem": self._oem,
            "psm": self._psm,
            "timeout": self._timeout,
        }

    def __setstate__(self, state: dict) -> None:
        self._lang = state["lang"]
        self._oem = state["oem"]
        self._psm = state["psm"]
        self._timeout = state["timeout"]

    def __repr__(self) -> str:
        return (
            f"TesseractOCREngine(lang={self._lang!r}, oem={self._oem}, "
            f"psm={self._psm}, timeout={self._timeout})"
        )

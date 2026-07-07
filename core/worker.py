"""Per-file and per-page PDF processing worker.

This module is executed inside 'multiprocessing.Pool' worker processes.
It has NO dependency on FastAPI, the CLI, or the database, it processes
one PDF and returns a 'FileResult' that the pipeline (main process)
persists and broadcasts.

Module-level globals ('_WORKER_QUEUE', '_OCR_ENGINE') are set once
per worker process via '_pool_init'.  This avoids pickling them into
every task call while keeping them accessible inside worker functions
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from functools import partial
from typing import Optional

import pymupdf
from PIL import Image

from config import (
    APPLY_TEXT_FORMATTING,
    DEBUG_POST_PROCESS_FILE,
    ENABLE_PAGE_LEVEL_OCR_THREADS,
    OCR_DPI,
    OCR_LOW_TEXT_LENGTH_THRESHOLD,
    OCR_ON_IMAGE_AREA_THRESHOLD,
    PAGE_LEVEL_OCR_MAX_WORKERS,
    REMOVE_ALL_NUMBERS,
    REMOVE_FOOTERS,
    REMOVE_HEADERS,
    REMOVE_PAGE_NUMBERS,
)
from core.events import (
    ExtractionMethod,
    FileResult,
    FileStartedEvent,
    OcrEngineMissingEvent,
    PageDoneEvent,
    PipelineOutcome,
)
from services.hasher import compute_file_hash
from services.ocr.base import OCREngine

pymupdf.TOOLS.mupdf_display_errors(False)

logger = logging.getLogger(__name__)


class FileValidationError(Exception):
    """Raised when a file fails pre-validation checks (e.g. empty, protected)"""

    pass


# Per-worker-process singletons set once by _pool_init, never pickled per-task
_WORKER_QUEUE: Optional["multiprocessing.Queue"] = None  # type: ignore[name-defined]
_OCR_ENGINE: Optional[OCREngine] = None


# Pool initialiser
def pool_init(queue, ocr_engine: Optional[OCREngine]) -> None:
    """Initialise module-level globals in each worker process.

    Called once per process when the 'multiprocessing.Pool' is created
    """
    global _WORKER_QUEUE, _OCR_ENGINE
    _WORKER_QUEUE = queue
    _OCR_ENGINE = ocr_engine


# Public task wrapper (top-level for pickling)
def tracked_process_file(
    file_path: str, input_dir: str, output_dir: str, settings: Optional[dict] = None
) -> FileResult:
    """Picklable wrapper submitted to the pool via 'imap_unordered'.

    Emits a 'FileStartedEvent' before delegating to 'process_file'.
    Module-level globals injected by 'pool_init' are used so there is no
    need to pickle the queue or OCR engine into every task call
    """
    if _WORKER_QUEUE is not None:
        _WORKER_QUEUE.put_nowait(
            asdict(
                FileStartedEvent(
                    file=os.path.basename(file_path),
                    file_path=file_path,
                    pid=os.getpid(),
                )
            )
        )
    return process_file(
        file_path=file_path,
        input_dir_root=input_dir,
        output_dir_root=output_dir,
        ocr_engine=_OCR_ENGINE,
        event_queue=_WORKER_QUEUE,
        settings=settings,
    )


def make_task_fn(input_dir: str, output_dir: str, settings: Optional[dict] = None):
    """Return a 'partial' of 'tracked_process_file' bound to dirs.

    Using 'partial' rather than a lambda preserves picklability
    """
    return partial(
        tracked_process_file,
        input_dir=input_dir,
        output_dir=output_dir,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Page-level helper functions
# ---------------------------------------------------------------------------


def _get_page_image_ratio(page) -> float:
    """Return fraction of page area covered by embedded images (0.0-1.0)"""
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return 0.0
        total_image_area = sum(
            (img["bbox"][2] - img["bbox"][0]) * (img["bbox"][3] - img["bbox"][1])
            for img in page.get_image_info()
        )
        return total_image_area / page_area
    except Exception:
        return 0.0


def _render_grey_image(page) -> Image.Image:
    """Render page as a greyscale PIL Image at OCR_DPI"""
    pix = page.get_pixmap(dpi=OCR_DPI)
    grey = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    img = Image.frombytes("L", [grey.width, grey.height], grey.samples)
    # Release pixmap resources explicitly to bound peak memory per page
    pix = grey = None  # noqa: F841
    return img


def process_page(
    file_path: str,
    page_number: int,
    total_pages: int = 0,
    ocr_engine: Optional[OCREngine] = None,
    event_queue=None,
) -> tuple[int, str, str, dict]:
    """Process a single PDF page and return '(page_index, text, method, meta)'.

    Args:
        file_path:   Absolute path to the PDF.
        page_number: 0-indexed page number to process.
        total_pages: Total pages in the doc (for progress logging).
        ocr_engine:  OCR engine to use; 'None' disables OCR.
        event_queue: Optional multiprocessing queue for 'PageDoneEvent'.

    Returns:
        '(page_number, extracted_text, method_str, metadata_dict)'
    """
    method = ExtractionMethod.DIRECT
    text = ""
    meta: dict = {}
    start = time.time()
    basename = os.path.basename(file_path)
    pid = os.getpid()

    try:
        with pymupdf.open(file_path) as doc:
            page = doc.load_page(page_number)

            img_ratio = _get_page_image_ratio(page)
            meta["image_ratio"] = round(img_ratio, 4)

            disable_ocr = os.getenv("DISABLE_OCR") == "true"
            should_ocr = (img_ratio > OCR_ON_IMAGE_AREA_THRESHOLD) and not disable_ocr

            if should_ocr:
                meta["reason"] = "high_image_ratio"
            else:
                # Get the text content along with the page dict for post processing
                # sort=True is used to get the text content in the correct order
                text = page.get_text("text", sort=True)
                meta["page_dict"] = page.get_text("dict", sort=True)
                if (
                    len(text.strip()) < OCR_LOW_TEXT_LENGTH_THRESHOLD
                    and not disable_ocr
                ):
                    should_ocr = True
                    meta["reason"] = "low_text_length"

            if should_ocr and ocr_engine is not None:
                method = ExtractionMethod.OCR
                img = _render_grey_image(page)
                result = ocr_engine.run(img)
                img = None  # release memory

                if result.engine_missing:
                    method = ExtractionMethod.ERROR
                    text = ""
                    meta["error"] = "OCR engine or dependency is missing"
                    if event_queue is not None:
                        try:
                            event_queue.put_nowait(asdict(OcrEngineMissingEvent()))
                        except Exception:
                            pass
                else:
                    text = result.text
                    if result.confidence is not None:
                        meta["confidence"] = result.confidence
                    if hasattr(result, "page_dict") and result.page_dict:
                        meta["page_dict"] = result.page_dict

            meta["char_count"] = len(text)

            # Sub-heuristics
            if text:
                valid_chars = sum(
                    1
                    for c in text
                    if c.isalnum()
                    or c.isspace()
                    or c in ".,;:!?'\"()[]{}<>/-+*=%%&@_´`^~"
                )
                meta["encoding_ratio"] = valid_chars / len(text)
            else:
                meta["encoding_ratio"] = 1.0

    except Exception as exc:
        method = ExtractionMethod.ERROR
        text = ""
        meta["error"] = str(exc)

    duration = round(time.time() - start, 3)
    meta["seconds"] = duration

    page_1indexed = page_number + 1
    total_label = total_pages or "?"
    logger.debug(
        "[W%d] %s  pg %s/%s  %s  %.2fs",
        pid,
        basename,
        page_1indexed,
        total_label,
        method.value.upper(),
        duration,
    )

    if event_queue is not None:
        try:
            event_queue.put_nowait(
                asdict(
                    PageDoneEvent(
                        file=basename,
                        file_path=file_path,
                        page=page_1indexed,
                        total_pages=total_pages,
                        method=method.value,
                        pid=pid,
                        seconds=duration,
                        confidence=meta.get("confidence")
                        or (1.0 if not text else meta.get("encoding_ratio", 1.0)),
                    )
                )
            )
        except Exception:
            pass

    return page_number, text, method.value, meta


# ---------------------------------------------------------------------------
# File-level processor (called by tracked_process_file)
# ---------------------------------------------------------------------------


def process_file(
    file_path: str,
    input_dir_root: str,
    output_dir_root: str = "extracted_files",
    ocr_engine: Optional[OCREngine] = None,
    event_queue=None,
    settings: Optional[dict] = None,
) -> FileResult:
    """Process a single PDF and return a 'FileResult'.

    All DB writes are handled by the pipeline (main process) after receiving
    this result workers never touch the database directly.

    Args:
        file_path:       Absolute path to the PDF.
        input_dir_root:  Root of the input directory tree (for rel_path computation).
        output_dir_root: Root of the output directory tree.
        ocr_engine:      OCR engine to use; 'None' disables OCR entirely.
        event_queue:     Multiprocessing queue for page-done events.
        settings:        Optional configuration overrides for processing.

    Returns:
        'FileResult' dataclass (always returned, never raises)
    """
    import core.transform as transform

    start = time.time()
    rel_path = ""
    try:
        from common.fs.paths import to_posix_path

        rel_path = to_posix_path(os.path.relpath(file_path, input_dir_root))
        base_name_no_ext = os.path.splitext(rel_path)[0]

        # Pre-validation checks
        if not os.path.exists(file_path):
            raise FileValidationError("File does not exist or is inaccessible")
        if not os.access(file_path, os.R_OK):
            raise FileValidationError("Permission denied: file is inaccessible")
        if os.path.getsize(file_path) == 0:
            raise FileValidationError("Empty file")

        with pymupdf.open(file_path) as doc:
            if doc.is_encrypted:
                raise FileValidationError("File is password-protected or encrypted")
            num_pages = doc.page_count

        basename = os.path.basename(file_path)
        pid = os.getpid()
        logger.info("[W%d] START %s (%d pages)", pid, basename, num_pages)

        # Page processing: parallel or sequential
        results: list[tuple[str, str, dict] | None] = [None] * num_pages

        if (
            ENABLE_PAGE_LEVEL_OCR_THREADS
            and PAGE_LEVEL_OCR_MAX_WORKERS > 1
            and num_pages > 1
        ):
            with ThreadPoolExecutor(max_workers=PAGE_LEVEL_OCR_MAX_WORKERS) as pool:
                futures = {
                    pool.submit(
                        process_page, file_path, i, num_pages, ocr_engine, event_queue
                    ): i
                    for i in range(num_pages)
                }
                for future in as_completed(futures):
                    p_num, txt, method, meta = future.result()
                    results[p_num] = (txt, method, meta)
        else:
            for i in range(num_pages):
                p_num, txt, method, meta = process_page(
                    file_path, i, num_pages, ocr_engine, event_queue
                )
                results[i] = (txt, method, meta)

        # Aggregate
        full_text_parts: list[str] = []
        metadata_report: list[dict] = []
        page_dicts: list[dict] = []
        ocr_count = direct_count = 0

        for i, res in enumerate(results):
            if res is None:
                continue
            txt, method, meta = res

            page_dict = meta.pop("page_dict", None)
            if page_dict:
                page_dicts.append(page_dict)
            else:
                # In case the page_dict is not available, we append a default page_dict
                # This can happen if the OCR engine is not able to extract the page_dict
                page_dicts.append(
                    {
                        "height": 0,
                        "blocks": [
                            {"type": 0, "lines": [{"bbox": [0, 0, 0, 0], "text": txt}]}
                        ],
                    }
                )

            full_text_parts.append(txt)
            metadata_report.append({"page": i + 1, "method": method, "metadata": meta})
            if method == ExtractionMethod.OCR.value:
                ocr_count += 1
            elif method == ExtractionMethod.DIRECT.value:
                direct_count += 1

        overall_method = (
            ExtractionMethod.OCR
            if ocr_count > direct_count
            else ExtractionMethod.DIRECT
        )
        outcome = (
            PipelineOutcome.SUCCESS_OCR
            if overall_method == ExtractionMethod.OCR
            else PipelineOutcome.SUCCESS_DIRECT
        )
        subfolder = overall_method.value  # "ocr" | "direct"

        # Heuristics & Confidence Aggregation
        flags = []
        overall_confidence: Optional[float] = None

        total_chars = sum(res[2].get("char_count", 0) for res in results if res)
        chars_per_page = total_chars / num_pages if num_pages > 0 else 0
        if chars_per_page < 50:
            flags.append("low_density")

        pdf_size = os.path.getsize(file_path)
        # 1 MB pdf yielding < 100 chars
        if pdf_size > 1024 * 1024 and total_chars < 100:
            flags.append("large_pdf_small_text")

        encoding_ratios = [res[2].get("encoding_ratio", 1.0) for res in results if res]
        avg_enc_ratio = (
            sum(encoding_ratios) / len(encoding_ratios) if encoding_ratios else 1.0
        )
        if avg_enc_ratio < 0.85:
            flags.append("bad_encoding")

        page_confs = []
        for i, res in enumerate(results):
            if not res:
                continue
            txt, mth, mdata = res

            if mth == ExtractionMethod.OCR.value:
                if not txt.strip():
                    page_confs.append(1.0)
                    flags.append(f"empty_page_{i+1}")
                else:
                    conf = mdata.get("confidence")
                    page_confs.append(conf if conf is not None else 0.5)
            else:
                ratio = mdata.get("encoding_ratio", 1.0)
                page_confs.append(ratio if txt.strip() else 1.0)

        if page_confs:
            overall_confidence = sum(page_confs) / len(page_confs)
            if "low_density" in flags:
                overall_confidence -= 0.2
            if "large_pdf_small_text" in flags:
                overall_confidence -= 0.3
            overall_confidence = max(0.0, min(overall_confidence, 1.0))
        else:
            overall_confidence = 0.0

        # Write output files
        out_dir = os.path.join(output_dir_root, subfolder)
        out_txt = os.path.join(out_dir, base_name_no_ext + ".txt")
        out_meta = os.path.join(out_dir, base_name_no_ext + ".meta.json")

        os.makedirs(os.path.dirname(out_txt), exist_ok=True)

        # For post processing set the default config from env var
        sanitizer_config = {
            "remove_header": REMOVE_HEADERS,
            "remove_footer": REMOVE_FOOTERS,
            "remove_page_numbers": REMOVE_PAGE_NUMBERS,
            "remove_numeric_values": REMOVE_ALL_NUMBERS,
            "apply_text_formatting": APPLY_TEXT_FORMATTING,
            "debug_visualize": DEBUG_POST_PROCESS_FILE,
            "debug_filename": os.path.join(out_dir, base_name_no_ext + "_debug.html"),
        }
        if settings:
            sanitizer_config.update(settings)

        sanitizer = transform.DocumentSanitizer(config=sanitizer_config)

        # Sanitize / post process the page based on the page_dict
        # Which uses the bbox information of the text elements to remove headers, footers and page numbers
        # Then join the final cleaned pages for the final extracted text
        clean_pages = sanitizer.process_pages(page_dicts)
        join_char = (
            " " if sanitizer._get_bool_config("apply_text_formatting") else "\n\n"
        )
        final_text = join_char.join(clean_pages)

        with open(out_txt, "w", encoding="utf-8") as fh:
            fh.write(final_text)

        import json

        final_metadata = {
            "overall_confidence": overall_confidence,
            "flags": flags,
            "pages": metadata_report,
        }
        with open(out_meta, "w", encoding="utf-8") as fh:
            json.dump(final_metadata, fh, indent=2)

        elapsed = time.time() - start
        content_hash = compute_file_hash(file_path)
        txt_hash = compute_file_hash(out_txt)

        logger.info(
            "[W%d] DONE %s | %s | %.1fs | %d chars",
            pid,
            basename,
            outcome.value,
            elapsed,
            len(final_text),
        )

        return FileResult(
            file_path=file_path,
            outcome=outcome,
            message=f"Pages: {num_pages} OCR: {ocr_count} Direct: {direct_count}",
            elapsed=elapsed,
            char_count=len(final_text),
            method=overall_method,
            page_count=num_pages,
            rel_path=rel_path,
            txt_path=out_txt,
            content_hash=content_hash,
            txt_hash=txt_hash,
            confidence=overall_confidence,
            flags=flags,
        )

    except FileValidationError as exc:
        elapsed = time.time() - start
        logger.warning("File extraction failed for %s: %s", file_path, exc)

        exc_msg = str(exc).lower()
        flags = []
        if "empty file" in exc_msg:
            flags.append("empty_file")
        elif "password-protected" in exc_msg or "encrypted" in exc_msg:
            flags.append("protected_file")
        elif (
            "does not exist" in exc_msg
            or "permission denied" in exc_msg
            or "inaccessible" in exc_msg
        ):
            flags.append("inaccessible_file")
        else:
            flags.append("error")

        return FileResult(
            file_path=file_path,
            outcome=PipelineOutcome.FAILURE,
            message=f"File extraction failed: {exc}",
            elapsed=elapsed,
            char_count=0,
            method=ExtractionMethod.ERROR,
            page_count=0,
            rel_path=rel_path or os.path.basename(file_path),
            txt_path="",
            content_hash="",
            txt_hash="",
            confidence=0.0,
            flags=flags,
        )
    except Exception as exc:
        elapsed = time.time() - start
        logger.exception("process_file crashed for %s: %s", file_path, exc)

        exc_msg = str(exc).lower()
        flags = ["error"]

        return FileResult(
            file_path=file_path,
            outcome=PipelineOutcome.FAILURE,
            message=f"File extraction crashed: {exc}",
            elapsed=elapsed,
            char_count=0,
            method=ExtractionMethod.ERROR,
            page_count=0,
            rel_path=rel_path or os.path.basename(file_path),
            txt_path="",
            content_hash="",
            txt_hash="",
            confidence=0.0,
            flags=flags,
        )

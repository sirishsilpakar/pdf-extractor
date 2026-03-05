import os
import time
import json
import pymupdf
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    OCR_DPI,
    TESSERACT_LANG,
    TESSERACT_OEM,
    TESSERACT_PSM,
    TESSERACT_PAGE_TIMEOUT_SECONDS,
    ENABLE_PAGE_LEVEL_OCR_THREADS,
    PAGE_LEVEL_OCR_MAX_WORKERS,
    OCR_ON_IMAGE_AREA_THRESHOLD,
    OCR_LOW_TEXT_LENGTH_THRESHOLD,
)

pymupdf.TOOLS.mupdf_display_errors(False)


def get_page_image_ratio(page):
    """Calculates the ratio of the total area of images on the page to the page's total area."""
    try:
        page_area = page.rect.width * page.rect.height
        if page_area <= 0:
            return 0.0

        total_image_area = 0.0
        # get_images returned list of (xref, smask, width, height, bpc, colorspace, alt. colorspace, name, filter, referencer)
        # Using get_image_info is better for area calculation as it gives bbox
        image_infos = page.get_image_info()
        for img in image_infos:
            bbox = img["bbox"]
            # bbox is [x0, y0, x1, y1]
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            total_image_area += w * h

        return total_image_area / page_area
    except Exception:
        return 0.0


def ocr_page(page, config_args):
    """Performs OCR on a single page using a library like Tesseract."""
    pix = gray_pix = img = None
    try:
        import logging
        import pytesseract

        pix = page.get_pixmap(dpi=OCR_DPI)
        gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
        img = Image.frombytes("L", [gray_pix.width, gray_pix.height], gray_pix.samples)

        text = pytesseract.image_to_string(
            img,
            lang=TESSERACT_LANG,
            config=config_args,
            timeout=TESSERACT_PAGE_TIMEOUT_SECONDS,
        )
        return text
    except (ImportError, Exception) as e:
        import logging

        err_str = str(e).lower()
        # Detect if the OCR engine/dependency is missing
        if (
            isinstance(e, ImportError)
            or "tesseractnotfounderror" in type(e).__name__.lower()
            or "tesseract is not installed" in err_str
        ):
            logging.error(f"OCR engine or library missing: {e}")
            return "__OCR_ENGINE_MISSING__"

        logging.error(f"OCR processing failed: {e}")
        return ""
    finally:
        pix = gray_pix = img = None


def process_page(file_path, page_number, total_pages=0, event_queue=None):
    """Process a single page of a PDF.
    Returns: (page_number, text, method, metadata_dict)
    """
    method = "direct"
    text = ""
    meta = {}
    start_time = time.time()
    basename = os.path.basename(file_path)
    pid = os.getpid()

    try:
        # Re-open doc for thread safety if called in parallel
        with pymupdf.open(file_path) as doc:
            page = doc.load_page(page_number)

            # Metric 1: Image Area Ratio
            img_ratio = get_page_image_ratio(page)
            meta["image_ratio"] = round(img_ratio, 4)

            should_ocr = False

            # Check image ratio
            if img_ratio > OCR_ON_IMAGE_AREA_THRESHOLD:
                should_ocr = True
                meta["reason"] = "high_image_ratio"

            # Feature: Disable OCR override
            if os.getenv("DISABLE_OCR") == "true":
                should_ocr = False
                meta["reason"] = "ocr_disabled_by_user"

            if not should_ocr:
                # Try direct extraction
                text = page.get_text("text")
                clean_text = text.strip()

                # Metric 2: Text Length fallback
                if len(clean_text) < OCR_LOW_TEXT_LENGTH_THRESHOLD:
                    if os.getenv("DISABLE_OCR") != "true":
                        should_ocr = True
                        meta["reason"] = "low_text_length"
                    else:
                        meta["reason"] = "low_text_length_but_ocr_disabled"

            if should_ocr:
                method = "ocr"
                config_args = f"--oem {TESSERACT_OEM} --psm {TESSERACT_PSM}"
                text = ocr_page(page, config_args)

                if text == "__OCR_ENGINE_MISSING__":
                    method = "error"
                    text = ""
                    meta["error"] = "OCR engine or dependency is missing"
                    if event_queue is not None:
                        # This is where the worker process signals the main process that the OCR engine is missing by putting a specific message in the event queue
                        try:
                            event_queue.put_nowait({"type": "ocr_engine_missing"})
                        except Exception:
                            pass

            meta["char_count"] = len(text)

    except Exception as e:
        method = "error"
        text = ""
        meta["error"] = str(e)

    duration = time.time() - start_time
    meta["seconds"] = round(duration, 3)

    page_num_1 = page_number + 1  # 1-indexed for display
    total_label = total_pages if total_pages else "?"
    print(
        f"  [W{pid}] {basename}  page {page_num_1}/{total_label}  "
        f"{method.upper():<6}  {duration:.2f}s"
    )

    if event_queue is not None:
        # Event added to signal the main process that the page is done
        try:
            event_queue.put_nowait(
                {
                    "type": "page_done",
                    "file": basename,
                    "file_path": file_path,
                    "page": page_num_1,
                    "total_pages": total_pages,
                    "method": method,
                    "pid": pid,
                    "seconds": round(duration, 3),
                }
            )
        except Exception:
            pass

    return page_number, text, method, meta


def process_file(
    file_path, input_dir_root, output_dir_root="extracted_files", event_queue=None
):
    """Main entry point for processing a single PDF file."""
    start_time = time.time()

    try:
        rel_path = os.path.relpath(file_path, input_dir_root)
        base_name = os.path.splitext(rel_path)[0]

        with pymupdf.open(file_path) as doc:
            num_pages = doc.page_count

        basename = os.path.basename(file_path)
        pid = os.getpid()
        print(f"\n  [W{pid}] START {basename}  ({num_pages} pages)")

        results = [None] * num_pages

        # Parallel page processing if enabled and useful
        if (
            ENABLE_PAGE_LEVEL_OCR_THREADS
            and PAGE_LEVEL_OCR_MAX_WORKERS > 1
            and num_pages > 1
        ):
            with ThreadPoolExecutor(max_workers=PAGE_LEVEL_OCR_MAX_WORKERS) as pool:
                futures = [
                    pool.submit(process_page, file_path, i, num_pages, event_queue)
                    for i in range(num_pages)
                ]
                for future in as_completed(futures):
                    p_num, txt, method, meta = future.result()
                    results[p_num] = (txt, method, meta)
        else:
            # Sequential processing
            for i in range(num_pages):
                p_num, txt, method, meta = process_page(
                    file_path, i, num_pages, event_queue
                )
                results[i] = (txt, method, meta)

        # Aggregate results
        full_text = []
        metadata_report = []

        ocr_count = 0
        direct_count = 0

        for i, res in enumerate(results):
            if res is None:  # Should not happen
                continue
            txt, method, meta = res
            full_text.append(txt)
            metadata_report.append({"page": i + 1, "method": method, "metadata": meta})
            if method == "ocr":
                ocr_count += 1
            elif method == "direct":
                direct_count += 1

        # Determine overall status and subfolder based on majority method
        if ocr_count > direct_count:
            status = "SUCCESS_OCR"  # Mostly OCR
            subfolder = "ocr"
        else:
            status = "SUCCESS_DIRECT"  # Mostly Direct
            subfolder = "direct"

        # Write outputs to specific subfolder
        output_dir = os.path.join(output_dir_root, subfolder)
        out_txt_path = os.path.join(output_dir, base_name + ".txt")
        out_meta_path = os.path.join(output_dir, base_name + ".meta.json")

        os.makedirs(os.path.dirname(out_txt_path), exist_ok=True)

        final_text = "".join(full_text)
        with open(out_txt_path, "w", encoding="utf-8") as f:
            f.write(final_text)

        with open(out_meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata_report, f, indent=2)

        elapsed = time.time() - start_time
        char_count = len(final_text)

        # Persist extraction record in DB (content stays on disk)
        try:
            import database

            database.save_extracted_text(
                source_path=file_path,
                filename=os.path.basename(file_path),
                rel_path=rel_path,  # e.g. "subfolder/file.pdf"
                txt_path=out_txt_path,  # absolute path to the .txt
                method=subfolder,  # 'ocr' | 'direct'
                char_count=char_count,
                page_count=num_pages,
            )
        except Exception as _db_err:
            print(f"  [DB] Warning: could not save extraction record: {_db_err}")

        message = f"Pages: {num_pages} (OCR: {ocr_count}, Direct: {direct_count})"

        return file_path, status, message, elapsed, char_count

    except Exception as e:
        elapsed = time.time() - start_time
        return file_path, "FAILURE", f"File extraction failed: {str(e)}", elapsed, 0

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
import transform as t

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
    """Performs OCR on a single page using Tesseract."""
    try:
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
    except Exception:
        return ""
    finally:
        pix = gray_pix = img = None


def process_page(file_path, page_number):
    """Process a single page of a PDF.
    Returns: (page_number, text, method, metadata_dict)
    """
    method = "direct"
    text = ""
    meta = {}
    start_time = time.time()

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

            meta["char_count"] = len(text)

    except Exception as e:
        method = "error"
        text = ""
        meta["error"] = str(e)

    duration = time.time() - start_time
    meta["seconds"] = round(duration, 3)

    return page_number, text, method, meta


def process_file(file_path, input_dir_root, output_dir_root="extracted_files"):
    """Main entry point for processing a single PDF file."""
    start_time = time.time()

    try:
        rel_path = os.path.relpath(file_path, input_dir_root)
        base_name = os.path.splitext(rel_path)[0]

        with pymupdf.open(file_path) as doc:
            num_pages = doc.page_count

        results = [None] * num_pages

        # Parallel page processing if enabled and useful
        if (
            ENABLE_PAGE_LEVEL_OCR_THREADS
            and PAGE_LEVEL_OCR_MAX_WORKERS > 1
            and num_pages > 1
        ):
            with ThreadPoolExecutor(max_workers=PAGE_LEVEL_OCR_MAX_WORKERS) as pool:
                futures = [
                    pool.submit(process_page, file_path, i) for i in range(num_pages)
                ]
                for future in as_completed(futures):
                    p_num, txt, method, meta = future.result()
                    results[p_num] = (txt, method, meta)
        else:
            # Sequential processing
            for i in range(num_pages):
                p_num, txt, method, meta = process_page(file_path, i)
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
            f.write(t.preprocess_text(final_text))

        with open(out_meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata_report, f, indent=2)

        elapsed = time.time() - start_time
        char_count = len(final_text)

        message = f"Pages: {num_pages} (OCR: {ocr_count}, Direct: {direct_count})"

        return file_path, status, message, elapsed, char_count

    except Exception as e:
        elapsed = time.time() - start_time
        return file_path, "FAILURE", f"File extraction failed: {str(e)}", elapsed, 0

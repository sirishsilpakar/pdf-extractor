import os
import time

import pymupdf

from PIL import Image

from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    OCR_DPI,
    TEXT_CHARACTER_THRESHOLD,
    PAGES_TO_CHECK_FOR_OCR,
    TESSERACT_LANG,
    TESSERACT_OEM,
    TESSERACT_PSM,
    TESSERACT_PAGE_TIMEOUT_SECONDS,
    ENABLE_PAGE_LEVEL_OCR_THREADS,
    PAGE_LEVEL_OCR_MAX_WORKERS,
)

pymupdf.TOOLS.mupdf_display_errors(False)

def process_file(file_path, input_dir_root):
    """Process file found at the file path with either text based extraction or
    OCR based extraction.
    """
    try:
        start_time = time.time()
        does_file_need_ocr = False
        with pymupdf.open(file_path) as doc:
            if doc.page_count > 0:
                text_sample = ""
                pages_checked = 0
                for page in doc:
                    if pages_checked >= PAGES_TO_CHECK_FOR_OCR:
                        break
                    text_sample += page.get_text("text").strip()
                    pages_checked += 1

                avg_chars = len(text_sample) / pages_checked if pages_checked > 0 else 0
                if avg_chars < TEXT_CHARACTER_THRESHOLD:
                    does_file_need_ocr = True

        if does_file_need_ocr:
            file_path_r, status, message, char_count = extract_text_with_ocr(file_path, input_dir_root)
        else:
            file_path_r, status, message, char_count = extract_text_direct(file_path, input_dir_root)

        elapsed = time.time() - start_time
        return file_path_r, status, message, elapsed, char_count

    except Exception as e:
        elapsed = time.time() - start_time if 'start_time' in locals() else 0.0
        return file_path, "FAILURE", f"Document check failed: {e}", elapsed, 0


def extract_text_direct(file_path, input_dir_root):
    output_dir = "extracted_files"
    try:
        rel_path = os.path.relpath(file_path, input_dir_root)
        out_path = os.path.join(output_dir, os.path.splitext(rel_path)[0] + ".txt")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with pymupdf.open(file_path) as doc, open(
            out_path, "w", encoding="utf-8"
        ) as f_out:
            char_count = 0
            for page in doc:
                text = page.get_text()
                f_out.write(text)
                char_count += len(text)
        return file_path, "SUCCESS_DIRECT", os.path.basename(file_path), char_count
    except Exception as e:
        return file_path, "FAILURE", f"Direct extraction failed: {e}", 0


def extract_text_with_ocr(file_path: str, input_dir_root: str):
    output_dir = "output_ocr"
    try:
        import pytesseract

        rel_path = os.path.relpath(file_path, input_dir_root)
        out_path = os.path.join(output_dir, os.path.splitext(rel_path)[0] + ".txt")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        # Determine number of pages first (outside threading path)
        with pymupdf.open(file_path) as doc_info:
            num_pages = doc_info.page_count

        config_args = f"--oem {TESSERACT_OEM} --psm {TESSERACT_PSM}"

        def ocr_single_page(page_index: int):
            # Render and OCR a single page; re-open the document locally for thread safety
            with pymupdf.open(file_path) as doc_local:
                page = doc_local.load_page(page_index)
                pix = page.get_pixmap(dpi=OCR_DPI)
                gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                img = Image.frombytes(
                    "L", [gray_pix.width, gray_pix.height], gray_pix.samples
                )

            try:
                text = pytesseract.image_to_string(
                    img,
                    lang=TESSERACT_LANG,
                    config=config_args,
                    timeout=TESSERACT_PAGE_TIMEOUT_SECONDS,
                )
            except Exception:
                text = ""
            finally:
                pix = gray_pix = img = None
            return page_index, text

        use_threading = ENABLE_PAGE_LEVEL_OCR_THREADS and PAGE_LEVEL_OCR_MAX_WORKERS > 1

        with open(out_path, "w", encoding="utf-8") as f_out:
            if not use_threading:
                # Sequential path (lower memory footprint)
                with pymupdf.open(file_path) as doc:
                    page_texts = []
                    char_count = 0
                    for page in doc:
                        pix = page.get_pixmap(dpi=OCR_DPI)
                        gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                        img = Image.frombytes(
                            "L", [gray_pix.width, gray_pix.height], gray_pix.samples
                        )
                        try:
                            text = pytesseract.image_to_string(
                                img,
                                lang=TESSERACT_LANG,
                                config=config_args,
                                timeout=TESSERACT_PAGE_TIMEOUT_SECONDS,
                            )
                        except Exception:
                            text = ""
                        page_texts.append(text)
                        char_count += len(text)
                        pix = gray_pix = img = None
                    f_out.write("".join(page_texts))
            else:
                # Threaded path (faster for image-heavy PDFs; uses more resources)
                results = [""] * num_pages
                with ThreadPoolExecutor(max_workers=PAGE_LEVEL_OCR_MAX_WORKERS) as pool:
                    futures = [pool.submit(ocr_single_page, i) for i in range(num_pages)]
                    for future in as_completed(futures):
                        idx, text = future.result()
                        results[idx] = text
                final_text = "".join(results)
                f_out.write(final_text)
                char_count = len(final_text)
        return file_path, "SUCCESS_OCR", os.path.basename(file_path), char_count
    except Exception as e:
        return file_path, "FAILURE", f"OCR extraction failed: {e}", 0

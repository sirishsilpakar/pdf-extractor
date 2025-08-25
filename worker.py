import os

import pymupdf

from PIL import Image

from config import OCR_DPI, TEXT_CHARACTER_THRESHOLD, PAGES_TO_CHECK_FOR_OCR

pymupdf.TOOLS.mupdf_display_errors(False)

def process_file(file_path, input_dir_root):
    """Process file found at the file path with either text based extraction or
    OCR based extraction.
    """
    try:
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
            return extract_text_with_ocr(file_path, input_dir_root)
        else:
            return extract_text_direct(file_path, input_dir_root)

    except Exception as e:
        return file_path, "FAILURE", f"Document check failed: {e}"


def extract_text_direct(file_path, input_dir_root):
    output_dir = "output_direct_extract"
    try:
        rel_path = os.path.relpath(file_path, input_dir_root)
        out_path = os.path.join(output_dir, os.path.splitext(rel_path)[0] + ".txt")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with pymupdf.open(file_path) as doc, open(
            out_path, "w", encoding="utf-8"
        ) as f_out:
            for page in doc:
                text = page.get_text()
                f_out.write(text)
        return file_path, "SUCCESS_DIRECT", os.path.basename(file_path)
    except Exception as e:
        return file_path, "FAILURE", f"Direct extraction failed: {e}"


def extract_text_with_ocr(file_path: str, input_dir_root: str):
    output_dir = "output_ocr"
    try:
        import pytesseract

        rel_path = os.path.relpath(file_path, input_dir_root)
        out_path = os.path.join(output_dir, os.path.splitext(rel_path)[0] + ".txt")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with pymupdf.open(file_path) as doc, open(
            out_path, "w", encoding="utf-8"
        ) as f_out:
            for page in doc:
                # Get the image representation of the page from the document
                # at a specific resolution (DPI)
                # and transform it to grayscale for faster processing
                # then extract the actual text from the image
                pix = page.get_pixmap(dpi=OCR_DPI)
                gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)

                img = Image.frombytes(
                    "L", [gray_pix.width, gray_pix.height], gray_pix.samples
                )

                text = pytesseract.image_to_string(img)
                f_out.write(text)

                pix = gray_pix = img = None  # Explicit memory release

        return file_path, "SUCCESS_OCR", os.path.basename(file_path)
    except Exception as e:
        return file_path, "FAILURE", f"OCR extraction failed: {e}"

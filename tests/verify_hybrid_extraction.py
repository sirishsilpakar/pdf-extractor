import os
import shutil
import pymupdf
from PIL import Image, ImageDraw
import worker
import json


def create_test_pdf(filename="test_hybrid.pdf"):
    doc = pymupdf.open()

    # Page 1: Pure Text
    page1 = doc.new_page()
    page1.insert_text(
        (50, 50),
        "This is page 1. It is pure text and should be extracted directly.",
        fontsize=12,
    )

    # Page 2: Full page image (simulating a scan)
    # Create a simple image with text "This is page 2, an image."
    img = Image.new("RGB", (600, 800), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((50, 50), "This is page 2, an image.", fill=(0, 0, 0))
    img.save("temp_page2.png")

    page2 = doc.new_page()
    page2.insert_image(page2.rect, filename="temp_page2.png")

    doc.save(filename)
    doc.close()
    if os.path.exists("temp_page2.png"):
        os.remove("temp_page2.png")
    return filename


def verify():
    pdf_path = create_test_pdf()
    root_dir = os.getcwd()
    abs_pdf_path = os.path.join(root_dir, pdf_path)

    print(f"Created test PDF at {abs_pdf_path}")

    # Ensure output dir exists or is clean
    if os.path.exists("extracted_files"):
        shutil.rmtree("extracted_files")
    os.makedirs("extracted_files")

    print("Running worker.process_file...")
    res = worker.process_file(abs_pdf_path, root_dir)
    print(f"Result: {res}")

    # Check outputs
    txt_path = "extracted_files/direct/test_hybrid.txt"
    meta_path = "extracted_files/direct/test_hybrid.meta.json"

    if not os.path.exists(txt_path):
        print("FAIL: Text file not created.")
        return

    if not os.path.exists(meta_path):
        print("FAIL: Meta file not created.")
        return

    with open(txt_path, "r") as f:
        content = f.read()
        print(f"\nExtracted Text:\n---\n{content}\n---")

    with open(meta_path, "r") as f:
        meta = json.load(f)
        print(f"\nMetadata:\n{json.dumps(meta, indent=2)}")

    # Assertions
    p1 = meta[0]
    p2 = meta[1]

    if p1["method"] == "direct":
        print("PASS: Page 1 used DIRECT extraction.")
    else:
        print(f"FAIL: Page 1 extraction was {p1['method']}")

    if p2["method"] == "ocr":
        print("PASS: Page 2 used OCR extraction.")
    else:
        print(f"FAIL: Page 2 extraction was {p2['method']}")


if __name__ == "__main__":
    verify()

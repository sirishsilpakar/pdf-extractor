"""Script to generate test PDFs for the extraction pipeline"""

import json
import os

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def generate():
    out_dir = os.path.join(os.path.dirname(__file__), "ground_truth")
    os.makedirs(out_dir, exist_ok=True)
    expected_texts = {}

    # 1. Plain text PDF
    p1 = "test1_plain.pdf"
    c = canvas.Canvas(os.path.join(out_dir, p1), pagesize=letter)
    text1 = "This is a strictly plain text document.\nIt should be 100% extractable with high confidence."
    c.drawString(100, 750, "This is a strictly plain text document.")
    c.drawString(100, 730, "It should be 100% extractable with high confidence.")
    c.save()
    expected_texts[p1] = text1

    # 2. Plain text structured
    p2 = "test2_structured.pdf"
    c = canvas.Canvas(os.path.join(out_dir, p2), pagesize=letter)
    text2 = "Quarterly Financial Report Q1 2026\nRevenue: e1,000,000\nExpenses: e800,000\nProfit: e200,000"
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 750, "Quarterly Financial Report Q1 2026")
    c.setFont("Helvetica", 12)
    c.line(100, 740, 500, 740)
    c.drawString(100, 720, "Revenue: e1,000,000")
    c.drawString(100, 700, "Expenses: e800,000")
    c.drawString(100, 680, "Profit: e200,000")
    c.save()
    expected_texts[p2] = text2

    # 3. Scanned Image (Simple)
    p3 = "test3_scanned_simple.pdf"
    img = Image.new("RGB", (800, 1000), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    text3 = "This is a scanned image with simple layout.\nTesseract OCR should detect this perfectly."
    d.text((50, 50), "This is a scanned image with simple layout.", fill=(0, 0, 0))
    d.text((50, 80), "Tesseract OCR should detect this perfectly.", fill=(0, 0, 0))
    img.save(os.path.join(out_dir, p3), "PDF", resolution=100.0)
    expected_texts[p3] = text3

    # 4. Scanned Image (Complex)
    p4 = "test4_scanned_complex.pdf"
    img = Image.new("RGB", (800, 1000), color=(230, 230, 230))
    d = ImageDraw.Draw(img)
    text4 = "FINANCIAL STATEMENT MATRIX\nAsset          |    Value\nLiquid Cash    |    $50k\nReal Estate    |    $2M"
    d.rectangle([40, 40, 760, 200], fill=(200, 200, 200), outline=(0, 0, 0))
    d.text((50, 50), "FINANCIAL STATEMENT MATRIX", fill=(0, 0, 0))
    d.line((50, 80, 750, 80), fill=(0, 0, 0), width=2)
    d.text((50, 100), "Asset          |    Value", fill=(0, 0, 0))
    d.line((50, 120, 750, 120), fill=(0, 0, 0), width=1)
    d.text((50, 140), "Liquid Cash    |    $50k", fill=(0, 0, 0))
    d.text((50, 160), "Real Estate    |    $2M", fill=(0, 0, 0))
    img.save(os.path.join(out_dir, p4), "PDF", resolution=100.0)
    expected_texts[p4] = text4

    with open(os.path.join(out_dir, "expected.json"), "w") as fh:
        json.dump(expected_texts, fh, indent=2)

    print(f"Generated {len(expected_texts)} dummy PDFs in {out_dir}")


if __name__ == "__main__":
    generate()

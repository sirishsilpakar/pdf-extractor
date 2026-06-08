import logging
import os
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

logger = logging.getLogger(__name__)

PAGE_COUNT = 80
IMAGE_SIZE = (2000, 2000)


def create_noise_image(image_path: Path, image_size: tuple[int, int]) -> None:
    """Create a random-noise PNG image."""

    arr = np.random.randint(
        0,
        256,
        (image_size[1], image_size[0], 3),
        dtype=np.uint8,
    )

    Image.fromarray(arr).save(image_path)


def generate_test_images(
    output_dir: Path,
    page_count: int,
    image_size: tuple[int, int],
) -> list[Path]:
    """Generate image files used by the PDF."""

    output_dir.mkdir(parents=True, exist_ok=True)

    image_files = []

    for idx in range(page_count):
        image_path = output_dir / f"page_{idx}.png"

        create_noise_image(
            image_path=image_path,
            image_size=image_size,
        )

        image_files.append(image_path)

    return image_files


def build_large_pdf(
    pdf_path: Path,
    image_files: list[Path],
) -> None:
    """Build PDF using generated images."""

    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
    )

    elements = []

    body_text = """
    This PDF was generated for OCR and upload testing.

    It contains:
    - repeated headers
    - repeated footers
    - paragraph content
    - large embedded images
    """

    total_pages = len(image_files)

    for page_num, image_file in enumerate(image_files, start=1):

        elements.append(
            Paragraph(
                f"<b>CONFIDENTIAL REPORT — PAGE {page_num}</b>",
                styles["Heading2"],
            )
        )

        elements.append(Spacer(1, 10))

        elements.append(
            Paragraph(
                body_text,
                styles["BodyText"],
            )
        )

        elements.append(Spacer(1, 10))

        image = RLImage(str(image_file))

        image.drawWidth = 450
        image.drawHeight = 450

        elements.append(image)

        elements.append(Spacer(1, 10))

        elements.append(
            Paragraph(
                f"Page {page_num} of {total_pages}",
                styles["BodyText"],
            )
        )

        if page_num < total_pages:
            elements.append(PageBreak())

    doc.build(elements)


def create_large_test_pdf(
    pdf_path: str = "large_test_pdf.pdf",
) -> None:
    """Create a large PDF for OCR and performance testing."""

    image_dir = Path("temp_images")

    logger.info("Generating images")

    image_files = generate_test_images(
        output_dir=image_dir,
        page_count=PAGE_COUNT,
        image_size=IMAGE_SIZE,
    )

    logger.info("Building PDF")

    build_large_pdf(
        pdf_path=Path(pdf_path),
        image_files=image_files,
    )

    size_mb = os.path.getsize(pdf_path) / (1024 * 1024)

    logger.info(
        "Created %s (%.2f MB)",
        pdf_path,
        size_mb,
    )

    print(f"Created: {pdf_path}")
    print(f"Size: {size_mb:.2f} MB")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    create_large_test_pdf()
import csv
from pathlib import Path

import chardet
from tqdm import tqdm

MIN_TXT_SIZE_BYTES = 50
VALID_ENCODINGS = {"utf-8", "ascii"}


def save_list_to_csv(file_list: list[str], output_file: Path) -> Path:
    """Save a list of file paths to a CSV report."""
    with output_file.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["File Path"])

        for file_path in file_list:
            writer.writerow([file_path])

    return output_file


def qa_check_with_report(
    input_pdf_dir: str,
    output_txt_dir: str,
    report_dir: str = "qa_reports",
) -> None:
    """
    Validate PDF-to-TXT extraction results and generate QA reports.

    Reports generated:
    - Missing TXT files
    - Empty TXT files
    - Encoding issues
    """
    input_pdf_path = Path(input_pdf_dir)
    output_txt_path = Path(output_txt_dir)
    report_path = Path(report_dir)

    report_path.mkdir(parents=True, exist_ok=True)

    pdf_files = list(input_pdf_path.rglob("*.pdf"))
    txt_files = list(output_txt_path.rglob("*.txt"))

    pdf_basenames = {
        str(pdf.relative_to(input_pdf_path).with_suffix(""))
        for pdf in pdf_files
    }

    txt_basenames = {
        str(txt.relative_to(output_txt_path).with_suffix(""))
        for txt in txt_files
    }

    missing_txt = sorted(pdf_basenames - txt_basenames)

    empty_files: list[str] = []
    encoding_issues: list[str] = []

    for txt_file in tqdm(txt_files, desc="Checking TXT files"):
        relative_path = str(txt_file.relative_to(output_txt_path))

        try:
            if txt_file.stat().st_size < MIN_TXT_SIZE_BYTES:
                empty_files.append(relative_path)
                continue

            with txt_file.open("rb") as file_handle:
                detected = chardet.detect(file_handle.read(10_000))
                encoding = (detected.get("encoding") or "").lower()

                if encoding not in VALID_ENCODINGS:
                    encoding_issues.append(relative_path)

        except Exception as exc:
            print(f"[ERROR] {txt_file} -> {exc}")

    empty_files = sorted(set(empty_files))
    encoding_issues = sorted(set(encoding_issues) - set(empty_files))

    missing_csv = save_list_to_csv(
        missing_txt,
        report_path / "missing_txt_files.csv",
    )

    empty_csv = save_list_to_csv(
        empty_files,
        report_path / "empty_txt_files.csv",
    )

    encoding_csv = save_list_to_csv(
        encoding_issues,
        report_path / "encoding_issues.csv",
    )

    print("\n=== PDF Extraction QA Report ===")
    print(f"Total input PDFs: {len(pdf_files)}")
    print(f"Total output TXT files: {len(txt_files)}")
    print(f"Missing TXT files: {len(missing_txt)}")
    print(f"Empty TXT files (<{MIN_TXT_SIZE_BYTES} bytes): {len(empty_files)}")
    print(f"Encoding issues: {len(encoding_issues)}")

    print("\nReports saved to:")
    print(f"- Missing TXT: {missing_csv}")
    print(f"- Empty TXT: {empty_csv}")
    print(f"- Encoding Issues: {encoding_csv}")


if __name__ == "__main__":
    print("=== PDF Extraction QA Check with CSV Report ===")

    input_pdf_dir = input("Enter input PDF folder path: ").strip()
    output_txt_dir = input("Enter output TXT folder path: ").strip()

    qa_check_with_report(
        input_pdf_dir=input_pdf_dir,
        output_txt_dir=output_txt_dir,
    )
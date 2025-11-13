# PDF Extraction QA Checker

This repository contains a **Quality Assurance (QA)** tool for verifying and auditing the results of large-scale PDF text extraction.  
It ensures that every PDF file in a given input directory has a corresponding extracted text (`.txt`) file, and identifies issues such as **missing**, **empty**, or **encoding-corrupted** text files.

---

## Features

- Recursively scans nested folders for `.pdf` and `.txt` files.
- Preserves folder structure in outputs.
- Detects:
  - Missing TXT files (no extraction output).
  - Empty TXT files (< 50 bytes, likely image-only PDFs).
  - Encoding problems (non-UTF-8 or unreadable text).
- Ensures each problematic file appears in only one report.
- Generates CSV reports for easy review and reprocessing.

---

## 📂 Project Structure

```
📂 your_project/
├── qa_check_with_report.py        # Main QA script
├── qa_reports/                    # Folder containing QA CSV results
│   ├── missing_txt_files.csv      # PDFs without text output
│   ├── empty_txt_files.csv        # Empty or image-based TXT files
│   ├── encoding_issues.csv        # Files with encoding issues
├── README.md                      # This documentation file
```

> This structure renders cleanly in GitHub and VS Code previews.

---

## Installation

1. Clone this repository:

```bash
git clone https://github.com/yourusername/pdf-extraction-qa.git
cd pdf-extraction-qa
```

2. Install required dependencies:

```bash
pip install tqdm chardet
```

(Use a virtual environment like `venv` or `conda` if preferred.)

---

## Usage

Run the QA script from the terminal or inside VS Code:

```bash
python qa_check_with_report.py
```

You will be prompted for:

- **Input PDF folder path** — directory containing the `.pdf` files.
- **Output TXT folder path** — directory containing the extracted `.txt` files.

Example interaction:

```
=== PDF Extraction QA Check with CSV Report ===
Enter input PDF folder path: C:\Users\luthr\Documents\reports
Enter output TXT folder path: C:\Users\luthr\Documents\output-pdfs
```

---

## Example output

After execution you will see a summary, e.g.:

```
=== PDF Extraction QA Report ===
Total input PDFs: 16994
Total output TXT files: 16988
Missing TXT files: 6
Empty TXT files (<50 bytes): 540
Encoding issues: 825

Reports saved to:
- qa_reports/missing_txt_files.csv
- qa_reports/empty_txt_files.csv
- qa_reports/encoding_issues.csv
```

---

## CSV report format

Each CSV contains a single column:

```
File Path
folder/subfolder/file.txt
...
```

Use these CSVs to filter, review, and reprocess problem files (e.g., with `ocrmypdf` or `pytesseract`).

---

## Recommended next steps

1. Review CSV reports in `qa_reports/`.
2. Apply OCR to empty/image PDFs and re-run extraction where appropriate.
3. Re-run the QA script to confirm improvements.
4. Optionally integrate QA into an automation pipeline or dashboard.

---

## Current status (example)

| Area | Status | Notes |
|------|--------|-------|
| PDF text extraction | ✅ Complete | 16,988 / 16,994 successfully processed |
| QA verification | ✅ Complete | Results validated with detailed counts |
| CSV reporting | ✅ Complete | Outputs generated for follow-up action |
| OCR for image PDFs | ⏳ Pending | Recommended next step for 540 empty files |
| Encoding cleanup | ⏳ Pending | Optional improvement for 825 files |

---

## Contributing

Contributions, issues, and feature requests are welcome. Please open a GitHub issue or submit a pull request.

---



---

 
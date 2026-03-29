# PDF Extractor Pipeline

A robust PDF extraction tool that automatically switches between direct text extraction and OCR based on the page content.

## Features

- **Hybrid Extraction**: Automatically detects image-heavy pages and applies OCR, while using fast direct extraction for text pages.
- **Auto-Sorting**: Results are automatically sorted into `ocr` (image-heavy) and `direct` (text-heavy) folders.
- **Reliability**: Tracks processed files in `state.db` to allow resuming interrupted jobs.
- **Detailed Logging**: Records success, time, and metrics in `pipeline.log`.
- **Configurable**: Metadata file (`.meta.json`) generated for every PDF.

## Installation

### Prerequisites
- Python 3.8+
- Tesseract OCR (`brew install tesseract` or `sudo apt-get install tesseract-ocr`)

### Setup
1.  **Install Dependencies & Pre-commit Hooks**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    pip install -e .[dev]
    pre-commit install
    ```

2.  **Build** (Optional, to create a wheel):
    ```bash
    pip install build
    python3 -m build
    ```

## Usage

### Basic Run
Process all PDFs in `input_data`. Results go to `extracted_files/`.
```bash
python3 main.py ./input_data
```

### Options

| Flag | Description |
| :--- | :--- |
| `--output-dir DIR` | Directory to save extracted files (default: `extracted_files`). |
| `--no-ocr` | Disable OCR completely (forces direct extraction). |
| `--force` | Ignore history and re-process ALL files. |
| `--workers N` | Override number of parallel worker processes. |

### Examples

**Specify Output Directory:**
```bash
python3 main.py ./input_data --output-dir my_results
```
Output structure:
- `my_results/ocr/`: Documents processed primarily via OCR.
- `my_results/direct/`: Documents processed primarily via direct extraction.

**Force Re-run:**
```bash
python3 main.py ./input_data --force
```

**Fast Mode:**
Enable optimizations for speed (lower quality OCR):
```bash
python3 main.py ./input_data --fast
```

**Install and Run as Command:**
If you installed the wheel (`pip install dist/pdf_extractor-*.whl`):
```bash
pdf-extractor ./input_data --output-dir results
```

## Logs & Metadata

- **Logs** (`pipeline.log`):
    `INFO - PROCESSED: /path/to/doc.pdf | Method: ocr | Time: 1.2s | Chars: 1500`

- **Metadata** (`.meta.json`):
    Contains detailed per-page extraction method, execution time, and character counts.
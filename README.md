# PDF Extractor

This project provides a robust, high-performance, and scalable pipeline for extracting text from large collections of PDF documents.

Its core feature is an intelligent **document-level triage system**. Before performing any heavy processing, the pipeline rapidly analyzes every document to classify it as either text-based (for direct extraction) or image-based (requiring OCR). This ensures that each document is processed using the most efficient and appropriate method, saving significant time and computational resources.

## Key Features

-   **Intelligent Document Triage:** A fast, parallel pre-processing step that analyzes and classifies every PDF as either `direct` or `ocr`.
-   **High Concurrency:** Fully utilizes multi-core CPUs by processing documents in parallel within each phase of the pipeline.
-   **Resilient and Robust:** An intelligent timeout system prevents a single corrupt or extremely complex file from stalling the entire process.
-   **Memory Aware:** Optimized to handle large files and avoid system memory exhaustion, a common issue with parallel OCR.
-   **Separated Outputs:** Automatically organizes the results into `output_direct_extract` and `output_ocr` directories, preserving the original folder structure.
-   **Easily Tunable:** Key performance parameters (worker count, OCR quality, timeouts) are centralized in a `config.py` file for easy adjustment.

## Architectural Workflow

The pipeline operates in distinct, sequential phases to maximize efficiency. When you run the script, you will see multiple progress bars corresponding to these phases:

1.  **File Discovery:** The `Orchestrator` scans the input directory and creates a job list of all PDF files found.
2.  **Phase 1: Triage (Parallel):** The pipeline's first major action is to analyze every document. It runs a fast, parallel process to classify each PDF as either `direct` or `ocr`. The output of this phase is two lists: one for text-based files and one for image-based files.
3.  **Phase 2: Direct Text Extraction (Parallel):** A worker pool is created to process only the files from the `direct` list. The text from these files is extracted quickly and saved to the `output_direct_extract` directory.
4.  **Phase 3: OCR Extraction (Parallel):** A new worker pool is created to process the files from the `ocr` list. This is the most computationally intensive phase. Pages are rendered to images, and Tesseract extracts the text. Results are saved to the `output)ocr` directory.
5.  **Completion and Reporting:** The Orchestrator tracks the status of all jobs and presents a final summary of how many files were processed by each method.

## Setup Instructions

### 1. Prerequisites

You must have the following installed on your system *before* running the application:

-   **Python 3.10+**
-   **Tesseract OCR Engine:** This is a system-level program, not just a Python library.
    -   **On macOS (using Homebrew):**
        ```bash
        brew install tesseract
        ```
    -   **On Ubuntu/Debian Linux:**
        ```bash
        sudo apt update && sudo apt install tesseract-ocr
        ```
    -   **On Windows:**
        1.  Download and run an installer from the official [Tesseract at UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) page.
        2.  **Crucially**, ensure that you add the Tesseract installation directory to your system's `PATH` environment variable so the script can find it.

### 2. Installation

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd pdf-extractor
    ```

2.  **Install Python Dependencies:**
    It is highly recommended to use a virtual environment.
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

## How to Run

The pipeline is executed from the command line via the `main.py` script. You must provide the path to the directory containing your PDF files.

```bash
python main.py /path/to/your/input_pdfs
```

The script will start, display the configured number of workers, and show a series of real-time progress bars for the Triage, Direct Extraction, and OCR Extraction phases.

## Understanding the Output

The pipeline creates two top-level directories for the extracted text. Within each, the original folder structure from your input directory is preserved.

### `output_direct_extract/`

This directory contains `.txt` files for all documents that were identified as **text-based**. The extraction is a direct copy of the text content embedded in the PDF.

-   **Example:** If your input was `input/reports/2023/annual.pdf`, the output will be `output_direct_extract/reports/2023/annual.txt`.

### `output_ocr/`

This directory contains `.txt` files for all documents that were identified as **image-based** or did not contain sufficient embedded text.

-   **Example:** If your input was `input/scans/receipts/invoice-123.pdf`, the output will be `output_ocr/scans/receipts/invoice-123.txt`.

## Performance Tuning

For optimal performance, especially on large datasets, you should tune the settings in the **`config.py`** file.

-   **`WORKERS`**: This is the most important setting. It controls the number of parallel processes.
    -   **Problem:** Setting this too high on a machine without enough RAM will cause severe slowdowns as the system "thrashes" by swapping memory to disk. OCR is very memory-intensive.
    -   **Recommendation:** Start with `multiprocessing.cpu_count() // 2` (half your available cores). If performance is still slow or your system becomes unresponsive, **reduce this number further**.

-   **`OCR_DPI`**: Controls the resolution of images fed to Tesseract.
    -   `150`: Fastest, uses least memory, but may miss small or unclear text.
    -   `200`: A great balance of speed, memory usage, and quality. **(Default)**
    -   `300`: Slower, uses more memory, but provides the highest quality. Use this if accuracy is paramount and you are seeing poor results at lower DPIs.

-   **`JOB_TIMEOUT_SECONDS`**: The maximum time a single document is allowed to take.
    -   **Recommendation:** The default of `180` seconds (3 minutes) is generous. If you have extremely large documents (e.g., 1000+ pages) that are primarily images, you may need to increase this value to prevent them from being marked as failures.

## Project File Structure

```plaintext
.
├── main.py                 # Main entry point to run the pipeline
├── config.py               # Central configuration for performance tuning
├── extractor.py            # Manages the multi-phase process and worker pools
├── worker.py               # Contains the core document-level extraction logic
├── requirements.txt        # Python dependencies for pip
└── README.md               # This documentation file
```
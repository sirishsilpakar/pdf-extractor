# QA Test Scripts

This directory contains utility scripts and test assets used for QA validation of the PDF Text Extractor application.

## Prerequisites

Install the required dependencies:

```bash
pip install reportlab PyPDF2 numpy Pillow pyautogui
```

---

## 1. create_large_test_pdf.py

### Purpose

Generates a large, image-heavy PDF for performance, OCR, and upload testing.

### Features

* Creates random-noise images using NumPy and Pillow
* Generates a multi-page PDF using ReportLab
* Includes headers, body text, page numbering, and embedded images
* Produces large PDF files suitable for stress testing

### Usage

```bash
python create_large_test_pdf.py
```

### Output

```text
large_test_pdf.pdf
```

---

## 2. create_password_protected.py

### Purpose

Creates a password-protected PDF for testing encrypted PDF handling.

### Features

* Generates a simple PDF using ReportLab
* Encrypts the PDF using PyPDF2
* Applies a password to restrict access

### Usage

```bash
python create_password_protected.py
```

### Output

```text
password_protected.pdf
```

### Default Password

```text
test123
```

---

## 3. import_file_button_test.py

### Purpose

Automates PDF import and processing using UI automation.

### Features

* Launches the PDF Text Extractor application
* Clicks the Import File button
* Uploads a test PDF
* Clicks the Start button
* Captures screenshots when UI elements are not found

### Required Files

The following files must be present in the same directory:

```text
PDF Text extractor tool 1.0.0.exe
Import_file.png
start_button.png
1. Handwritten_Image.pdf
```

### Usage

```bash
python import_file_button_test.py
```

### Notes

* Screen resolution and scaling settings may affect image recognition.
* Ensure the application window is visible during execution.

---

## 4. Empty Report Test

### Purpose

Validates application behavior when processing an empty or blank PDF.

### Test Objective

Verify that:

* The application does not crash.
* Appropriate validation or error messages are displayed.
* Empty reports are handled gracefully.

### Expected Result

The application should notify the user that no extractable content exists or generate an empty result without failure.

---

## Notes

These scripts are intended for QA, testing, and validation purposes only and are not part of the production application.

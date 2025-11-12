import os
import csv
import chardet
from tqdm import tqdm

def qa_check_with_report(input_pdf_dir, output_txt_dir, report_dir="qa_reports"):
    # Prepare report directory
    os.makedirs(report_dir, exist_ok=True)

    # Collect all PDFs and TXT files
    pdf_files = []
    for root, _, files in os.walk(input_pdf_dir):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, f))

    txt_files = []
    for root, _, files in os.walk(output_txt_dir):
        for f in files:
            if f.lower().endswith(".txt"):
                txt_files.append(os.path.join(root, f))

    # Build sets of base names (to compare folder-relative structure)
    pdf_basenames = {os.path.splitext(os.path.relpath(p, input_pdf_dir))[0] for p in pdf_files}
    txt_basenames = {os.path.splitext(os.path.relpath(t, output_txt_dir))[0] for t in txt_files}

    # Detect missing TXT files
    missing_txt = sorted(list(pdf_basenames - txt_basenames))

    # Check for empty and encoding-issue files (with deduplication)
    empty_files = []
    encoding_issues = []

    for txt_path in tqdm(txt_files, desc="Checking TXT files"):
        rel_path = os.path.relpath(txt_path, output_txt_dir)
        try:
            size = os.path.getsize(txt_path)
            if size < 50:
                empty_files.append(rel_path)
                continue  # Skip encoding check for empty files

            # Only check encoding for non-empty files
            with open(txt_path, 'rb') as fb:
                result = chardet.detect(fb.read(10000))
                if result['encoding'] not in ['utf-8', 'ascii']:
                    encoding_issues.append(rel_path)

        except Exception as e:
            print(f"[ERROR] {txt_path} → {e}")

    # Remove duplicates just in case
    empty_files = sorted(set(empty_files))
    encoding_issues = sorted(set(encoding_issues) - set(empty_files))

    # Helper to save lists to CSV
    def save_list_to_csv(file_list, filename):
        csv_path = os.path.join(report_dir, filename)
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["File Path"])
            for f in file_list:
                writer.writerow([f])
        return csv_path

    # Save all results
    missing_csv = save_list_to_csv(missing_txt, "missing_txt_files.csv")
    empty_csv = save_list_to_csv(empty_files, "empty_txt_files.csv")
    encoding_csv = save_list_to_csv(encoding_issues, "encoding_issues.csv")

    # Print summary
    print("\n=== PDF Extraction QA Report ===")
    print(f"Total input PDFs: {len(pdf_files)}")
    print(f"Total output TXT files: {len(txt_files)}")
    print(f"Missing TXT files: {len(missing_txt)}")
    print(f"Empty TXT files (<50 bytes): {len(empty_files)}")
    print(f"Encoding issues: {len(encoding_issues)}")

    print("\nReports saved to:")
    print(f"- Missing TXT: {missing_csv}")
    print(f"- Empty TXT: {empty_csv}")
    print(f"- Encoding Issues: {encoding_csv}")

if __name__ == "__main__":
    print("=== PDF Extraction QA Check with CSV Report ===")
    input_pdf_dir = input("Enter input PDF folder path: ").strip()
    output_txt_dir = input("Enter output TXT folder path: ").strip()
    qa_check_with_report(input_pdf_dir, output_txt_dir)

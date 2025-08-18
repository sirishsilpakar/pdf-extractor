from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
import multiprocessing
import os, time
import sys
import pymupdf

# to see error / warnings from pymupdf
pymupdf.TOOLS.mupdf_display_errors(False) 

def extract_text(pdf_path):
    try:
      start = time.time()
      pid = os.getpid()

      # preserve folder strucutre
      rel_path = os.path.relpath(pdf_path, sys.argv[1])
      rel_no_ext = os.path.splitext(rel_path)[0]  # remove .pdf
      out_path = os.path.join('output-pdfs', rel_no_ext + ".txt")
      os.makedirs(os.path.dirname(out_path), exist_ok=True)

      with pymupdf.open(pdf_path) as doc, open(out_path, "w", encoding="utf-8") as f_out:
          for page_num, page in enumerate(doc):
              text = page.get_text()
              f_out.write(text)
      elapsed = time.time() - start
      # How long each worker took to finish
      # print(f"[Worker {pid}] Processed {os.path.basename(pdf_path)} in {elapsed:.2f}s \n")
      return os.path.basename(pdf_path), text
    except Exception as e:
      print(f"[ERROR] {pdf_path} → {e}")
        

def process_batch(batch):
    # Process a batch of PDFs
    return {f: extract_text(f) for f in batch}

def process_pdfs(pdf_dir, workers=4, batch_size=5):
    print(f"pdf_dir: {pdf_dir}, workers: {workers}, batch_size: {batch_size} ")
    # For nested directories PDFs
    pdf_files = []
    for root, _, files in os.walk(pdf_dir):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, f))
    results = {}

    # Chunk files for efficiency
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = []
        for batch in chunks(pdf_files, batch_size):
            futures.append(executor.submit(process_batch, batch))

        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing PDFs"):
            results.update(future.result())

    return results

if __name__ == "__main__":
    try:
        if len(sys.argv) < 2:
            print("Usage: python script.py <filename>")
            sys.exit(1)
        file_dir = sys.argv[1]
        data = process_pdfs(file_dir, workers=6, batch_size=10)
        workers = multiprocessing.cpu_count()
        print(f"Number of Cores available: {workers}")
        print(f"Processed {len(data)} PDFs")
    except FileNotFoundError:
        print("File not found")

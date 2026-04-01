import os, sys
import time
import re
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

RE_HYPHEN = re.compile(r"-\n")
RE_WHITESP = re.compile(r"\s+")
RE_BULLET = re.compile(r'[•●▪◆▶►■□➢→-]+')
RE_UNICODE = re.compile(r'[\u2022\u25CF\u25AA\u25C6\u25BA\u25B6\u25A0\u25A1\u279A\u2192]')
RE_REPEAT = re.compile(r'[.•●▪◆▶►■□➢→▲\-]{2,}')
RE_NON_ALPHA = re.compile(r'[^a-zA-Z0-9äöüÄÖÜß.,!?()\[\]{}:;\'" \n-]')
RE_PAGE_WHITELIST = [
    re.compile(r'\b\d{1,3}(?:,\d{3})+\b'),
    re.compile(r'\b\d+,\b'),
    re.compile(r'\b\d{1,2}:\d{2}(?:\s*[APap][Mm])?\b')
]
RE_PAGE_PATTERN = [
    re.compile(r'\b[Pp]age\s*\d+\b'),
    re.compile(r'\b\d+\s*[-–]\s*\d+\b'),
    re.compile(r'\b\d+\s+[oO][fF]\s+\d+\b'),
    re.compile(r'(?<!\d)\b[ivxlcdm]+\b(?!\d)'),
    re.compile(r'\b(?:[1-9]|[1-9][0-9]|[1-3]00)\b'),
    re.compile(r'\b\d+\.\b'),
    re.compile(r'\b\d+\.\d+\b'),
]


def preprocess_text(text):
    # Normalize whitespace & fix hyphenation
    text = RE_HYPHEN.sub("", text)      # remove line-break hyphenation
    text = RE_WHITESP.sub(" ", text)     # collapse whitespace
    text = RE_BULLET.sub(' ', text)  # common bullets/arrows
    text = RE_UNICODE.sub(' ', text)  # unicode bullets
    text = RE_REPEAT.sub('', text) # remove repetitive spc. char
    
    # optional: strip stray non-alphanumeric symbols (except .,!? and spaces)
    text = RE_NON_ALPHA.sub(' ', text)

    # Replace fancy quotes with normal
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")

    text = remove_page_numbers(text)
    text = re.sub(r'__KEEP\d+__(.*?)__KEEP\d+__', r'\1', text)
    return text


def remove_page_numbers(text):
  for i, pattern in enumerate(RE_PAGE_WHITELIST):
    text = pattern.sub(lambda m: f"__KEEP{i}__{m.group(0)}__KEEP{i}__", text)
  for pattern in RE_PAGE_PATTERN:
    text = pattern.sub('', text)
  return re.sub(r'\s+', ' ', text).strip()

def clean_page_text(text, header_candidates, footer_candidates):
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # Skip headers/footers
        if stripped in header_candidates or stripped in footer_candidates:
            continue

        # Remove page numbers (standalone digits)
        if re.fullmatch(r"\d+", stripped):
            continue

        # Drop tables (heuristic: lots of spaces or tabs in a line)
        if len(re.findall(r"\s{2,}", stripped)) > 2:
            continue

        cleaned_lines.append(stripped)

    return "\n".join(cleaned_lines)


def wait_for_file(path, timeout=30, interval=0.5):
  prev, waited = -1, 0
  while waited < timeout:
      size = os.path.getsize(path)
      if size == prev and size > 0:
          return True
      prev = size
      time.sleep(interval)
      waited += interval
  return False

class TextFileHandler(FileSystemEventHandler):
    def __init__(self, input_dir, output_dir):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)

        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Only process .txt files
        if file_path.suffix.lower() == ".txt":
            print(f"\nNew .txt file detected: {file_path.name}")
            self.process_file(file_path)

    def process_file(self, file_path):
        """
        Process the text file - customize this method based on your needs
        """
        try:
          # Adding delay so that the extraction processing is completed
          if not wait_for_file(file_path):
            print(f"Timeout waiting for {file_path}")
            return
          print("Start processing files...")
          # preserve folder strucutre
          rel_path = os.path.relpath(file_path, self.input_dir)
          rel_no_ext = os.path.splitext(rel_path)[0]  # remove .pdf
          out_path = os.path.join(self.output_dir, rel_no_ext + ".txt")
          os.makedirs(os.path.dirname(out_path), exist_ok=True)
          
          # Read the input file
          with open(file_path, "r", encoding="utf-8") as f_in, open(out_path, "w", encoding="utf-8") as f_out: 
            text = f_in.read() 
            f_out.write(preprocess_text(text))
            print(f"Processed file saved: {out_path}")
          return os.path.basename(file_path), text
            
        except Exception as e:
            print(f"Error processing {file_path}: {e}")


def main():
    # Configuration
    INPUT_DIR = "./extracted_files"      # Directory to watch
    OUTPUT_DIR = "./processed_files" # Directory for processed files
    
    # Create input directory if it doesn't exist
    Path(INPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Set up file system watcher
    event_handler = TextFileHandler(INPUT_DIR, OUTPUT_DIR)
    observer = Observer()
    observer.schedule(event_handler, INPUT_DIR, recursive=True)

    # Start monitoring
    observer.start()
    print(f"Monitoring directory: {INPUT_DIR}")
    print(f"Processed files will be saved to: {OUTPUT_DIR}")
    print("Press Ctrl+C to stop...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nStopping file watcher...")

    observer.join()
    print("File watcher stopped.")


if __name__ == "__main__":
    main()

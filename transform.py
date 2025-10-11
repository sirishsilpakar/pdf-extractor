import os
import time
import re
import pymupdf
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import spacy
from collections import Counter

# load spacy model once (do this outside function for efficiency)
# nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])

def preprocess_text(text):
    # Normalize whitespace & fix hyphenation
    text = re.sub(r"-\n", "", text)      # remove line-break hyphenation
    text = re.sub(r"\s+", " ", text)     # collapse whitespace
    text = re.sub(r'[•●▪◆▶►■□➢→-]+', ' ', text)  # common bullets/arrows
    text = re.sub(r'[\u2022\u25CF\u25AA\u25C6\u25BA\u25B6\u25A0\u25A1\u279A\u2192]', ' ', text)  # unicode bullets
    text = re.sub(r'[.•●▪◆▶►■□➢→▲\-]{2,}', '', text) # remove repetitive spc. char
    
    # optional: strip stray non-alphanumeric symbols (except .,!? and spaces)
    text = re.sub(r'[^a-zA-Z0-9äöüÄÖÜß.,!?()\[\]{}:;\'" \n-]', ' ', text)

    # Replace fancy quotes with normal
    text = text.replace("“", "\"").replace("”", "\"").replace("’", "'")

    text = remove_page_numbers(text)
    text = re.sub(r'__KEEP\d+__(.*?)__KEEP\d+__', r'\1', text)
    # Lemmatization & stop word removal (For later)
    # doc = nlp(text)
    # tokens = [token.lemma_ for token in doc if not token.is_stop and token.is_alpha]
    # return " ".join(tokens)
    return text

def remove_page_numbers(text):
  whitelist = [
        r'\b\d{1,3}(?:,\d{3})+\b',       # numbers with commas e.g. 1,000 / 2,000
        r'\b\d+,\b',                     # numbers ending with comma e.g. "1,"
        r'\b\d{1,2}:\d{2}(?:\s*[APap][Mm])?\b',  # time formats: 14:09 / 10:20 PM
    ]
  for i, pattern in enumerate(whitelist):
        text = re.sub(pattern, lambda m: f"__KEEP{i}__{m.group(0)}__KEEP{i}__", text)
  patterns = [
        r'\b[Pp]age\s*\d+\b',          # "Page 1" / "page 12"
        r'\b\d+\s*[-–]\s*\d+\b',       # "1-5"
        r'\b\d+\s+[oO][fF]\s+\d+\b',   # "1 of 10"
        r'(?<!\d)\b[ivxlcdm]+\b(?!\d)', # roman numerals like iv, x, vii (not part of a word/number)
  
        # standalone small numbers (likely page numbers, not years)
        r'\b(?:[1-9]|[1-9][0-9]|[1-3]00)\b',
        r'\b\d+\.\b',                     # numbers ending with a dot ("1.")
        r'\b\d+\.\d+\b',                  # decimals / section numbers ("1.1", "2.0")
    ]
  for pattern in patterns:
    text = re.sub(pattern, '', text)
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
        if file_path.suffix.lower() == '.txt':
            print(f"\nNew .txt file detected: {file_path.name}")
            self.process_file(file_path)

    def process_file(self, file_path):
        """
        Process the text file - customize this method based on your needs
        """
        try:
          # Adding delay so that the extraction processing is completed
          time.sleep(3)
          output_file = self.output_dir / file_path.name  
          # Read the input file
          with pymupdf.open(file_path) as doc, open(output_file, "w", encoding="utf-8") as f_out:
            for page_num, page in enumerate(doc):
              text = page.get_text()
              processed_content = preprocess_text(text)
              f_out.write(processed_content)
            print(f"Processed file saved: {output_file}")
            
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
    observer.schedule(event_handler, INPUT_DIR, recursive=False)
    
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
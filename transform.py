import os, sys
import time
import re
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class DocProfiler:
    def __init__(self, threshold=0.75):
        # 75% threshold means if it appears on 3/4 of the pages, it's a header/footer
        self.threshold = threshold
        self.noise_lines = set()

    def profile_document(self, doc):
        """
        Analyzes the PyMuPDF document to find repeating headers/footers and page numbers.
        """
        line_counts = {}
        total_pages = len(doc)
        if total_pages == 0:
            return

        for page in doc:
            page_height = page.rect.height
            blocks = page.get_text("blocks")
            
            # Use a set to prevent counting the same header twice on a single page
            seen_on_page = set()
            
            for b in blocks:
                # b[1] is the top Y-coordinate, b[3] is the bottom Y-coordinate
                y_pos = b[1]
                
                # OPTIONAL SAFEGUARD: Only profile text in the top 15% or bottom 15% of the page
                is_header_footer = (y_pos < page_height * 0.15) or (b[3] > page_height * 0.85)
                if not is_header_footer:
                    continue

                text = b[4].strip()
                if not text: continue
                
                # Process line by line instead of block by block
                for line in text.splitlines():
                    line = line.strip()
                    if not line: continue
                    
                    # Normalize text: remove digits to catch shifting page numbers (e.g. "Page 1", "Page 2")
                    norm_line = re.sub(r'\d+', '', line).strip()
                    
                    if norm_line and norm_line not in seen_on_page:
                        seen_on_page.add(norm_line)
                        line_counts[norm_line] = line_counts.get(norm_line, 0) + 1

        # Save lines that meet the frequency threshold
        self.noise_lines = {
            text for text, count in line_counts.items() 
            if count >= (total_pages * self.threshold)
        }
        
RE_HYPHEN = re.compile(r"-\n")
RE_WHITESP = re.compile(r"\s+")
RE_BULLET = re.compile(r'[•●▪◆▶►■□➢→-]+')
RE_UNICODE = re.compile(r'[\u2022\u25CF\u25AA\u25C6\u25BA\u25B6\u25A0\u25A1\u279A\u2192]')
RE_REPEAT = re.compile(r'[.•●▪◆▶►■□➢→▲\-]{2,}')
RE_NON_ALPHA = re.compile(r'[^a-zA-Z0-9äöüÄÖÜß.,!?()\[\]{}:;\'" \n-]')
RE_ANY_NUMBER = re.compile(r'[\d][\d,.:/-]*')
RE_PAGE_PATTERN = [
    # Page 1
    re.compile(r'\bpage\s*\d+\b', re.I),
    # Page 1 of 10
    re.compile(r'\bpage\s*\d+\s*(?:of|/)\s*\d+\b', re.I),
    # 1 of 10
    re.compile(r'\b\d+\s*(?:of|/)\s*\d+\b', re.I),
    # - 3 -
    re.compile(r'^\s*[-–—]\s*\d+\s*[-–—]\s*$', re.M),
    # (3)
    re.compile(r'^\s*\(\d+\)\s*$', re.M),
    # [3]
    re.compile(r'^\s*\[\d+\]\s*$', re.M),
    # p. 3 / pg 3
    re.compile(r'\b(?:p|pg)\.?\s*\d+\b', re.I),
    # Roman numeral page numbers
    re.compile(r'^\s*[ivxlcdm]{1,7}\s*$', re.I | re.M),
]


def preprocess_text(text, noise_list=None):
    ## Header footer noise
    if noise_list:
        lines = text.splitlines()
        # Remove line if its normalized version is in our noise list
        text = "\n".join([l for l in lines if re.sub(r'\d+', '', l).strip() not in noise_list])

    text = remove_page_numbers(text)
    text = RE_ANY_NUMBER.sub('', text)

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
 # Remove ALL numbers (intentional by user)
    
    text = re.sub(r'__KEEP\d+__(.*?)__KEEP\d+__', r'\1', text)
    return text


def remove_page_numbers(text):
    """
    Removes various page number formats.
    """
    for pattern in RE_PAGE_PATTERN:
        text = pattern.sub('', text)
    return text
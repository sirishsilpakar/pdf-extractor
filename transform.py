import os, sys
import time
import re
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

RE_HYPHEN = re.compile(r"-\n")
RE_WHITESP = re.compile(r"\s+")
RE_BULLET = re.compile(r"[•●▪◆▶►■□➢→-]+")
RE_UNICODE = re.compile(
    r"[\u2022\u25CF\u25AA\u25C6\u25BA\u25B6\u25A0\u25A1\u279A\u2192]"
)
RE_REPEAT = re.compile(r"[.•●▪◆▶►■□➢→▲\-]{2,}")
RE_NON_ALPHA = re.compile(r'[^a-zA-Z0-9äöüÄÖÜß.,!?()\[\]{}:;\'" \n-]')
RE_PAGE_WHITELIST = [
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),
    re.compile(r"\b\d+,\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?:\s*[APap][Mm])?\b"),
]
RE_PAGE_PATTERN = [
    re.compile(r"\b[Pp]age\s*\d+\b"),
    re.compile(r"\b\d+\s*[-–]\s*\d+\b"),
    re.compile(r"\b\d+\s+[oO][fF]\s+\d+\b"),
    re.compile(r"(?<!\d)\b[ivxlcdm]+\b(?!\d)"),
    re.compile(r"\b(?:[1-9]|[1-9][0-9]|[1-3]00)\b"),
    re.compile(r"\b\d+\.\b"),
    re.compile(r"\b\d+\.\d+\b"),
]


def preprocess_text(text):
    # Normalize whitespace & fix hyphenation
    text = RE_HYPHEN.sub("", text)  # remove line-break hyphenation
    text = RE_WHITESP.sub(" ", text)  # collapse whitespace
    text = RE_BULLET.sub(" ", text)  # common bullets/arrows
    text = RE_UNICODE.sub(" ", text)  # unicode bullets
    text = RE_REPEAT.sub("", text)  # remove repetitive spc. char

    # optional: strip stray non-alphanumeric symbols (except .,!? and spaces)
    text = RE_NON_ALPHA.sub(" ", text)

    # Replace fancy quotes with normal
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")

    text = remove_page_numbers(text)
    text = re.sub(r"__KEEP\d+__(.*?)__KEEP\d+__", r"\1", text)
    return text


def remove_page_numbers(text):
    for i, pattern in enumerate(RE_PAGE_WHITELIST):
        text = pattern.sub(lambda m: f"__KEEP{i}__{m.group(0)}__KEEP{i}__", text)
    for pattern in RE_PAGE_PATTERN:
        text = pattern.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


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

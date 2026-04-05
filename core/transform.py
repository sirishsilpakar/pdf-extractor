"""Text post-processing utilities.

Moved from 'transform.py' to 'core/transform.py' so it
lives inside the core domain layer.  The root 'transform.py' is kept as a
backward-compatibility moduel
"""

import os
import re
import time
from pathlib import Path

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


def preprocess_text(text: str) -> str:
    """Apply the full normalisation pipeline to extracted text."""
    text = RE_HYPHEN.sub("", text)
    text = RE_WHITESP.sub(" ", text)
    text = RE_BULLET.sub(" ", text)
    text = RE_UNICODE.sub(" ", text)
    text = RE_REPEAT.sub("", text)
    text = RE_NON_ALPHA.sub(" ", text)
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    text = remove_page_numbers(text)
    text = re.sub(r"__KEEP\d+__(.*?)__KEEP\d+__", r"\1", text)
    return text


def remove_page_numbers(text: str) -> str:
    for i, pattern in enumerate(RE_PAGE_WHITELIST):
        text = pattern.sub(
            lambda m, _i=i: f"__KEEP{_i}__{m.group(0)}__KEEP{_i}__", text
        )
    for pattern in RE_PAGE_PATTERN:
        text = pattern.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_page_text(text: str, header_candidates: set, footer_candidates: set) -> str:
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped in header_candidates or stripped in footer_candidates:
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        if len(re.findall(r"\s{2,}", stripped)) > 2:
            continue
        cleaned.append(stripped)
    return "\n".join(cleaned)


def wait_for_file(path: str, timeout: int = 30, interval: float = 0.5) -> bool:
    """Wait until *path* has a stable non-zero size."""
    prev, waited = -1, 0
    while waited < timeout:
        size = os.path.getsize(path)
        if size == prev and size > 0:
            return True
        prev = size
        time.sleep(interval)
        waited += interval
    return False

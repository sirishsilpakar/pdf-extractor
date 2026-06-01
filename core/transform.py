import re

from config import (
    APPLY_TEXT_FORMATTING,
    DEBUG_POST_PROCESS_FILE,
    REMOVE_ALL_NUMBERS,
    REMOVE_FOOTERS,
    REMOVE_HEADERS,
    REMOVE_PAGE_NUMBERS,
)

RE_HYPHEN = re.compile(r"-\n")
RE_WHITESP = re.compile(r"\s+")
RE_BULLET = re.compile(r"[•●▪◆▶►■□➢→-]+")
RE_UNICODE = re.compile(
    r"[\u2022\u25CF\u25AA\u25C6\u25BA\u25B6\u25A0\u25A1\u279A\u2192]"
)
RE_REPEAT = re.compile(r"[.•●▪◆▶►■□➢→▲\-]{2,}")
RE_NON_ALPHA = re.compile(r'[^a-zA-Z0-9äöüÄÖÜß.,!?()\[\]{}:;\'" \n-]')
RE_ANY_NUMBER = re.compile(r"[\d][\d,.:/-]*")
RE_PAGE_PATTERN = [
    # Page 1
    re.compile(r"\bpage\s*\d+\b", re.I),
    # Page 1 of 10
    re.compile(r"\bpage\s*\d+\s*(?:of|/)\s*\d+\b", re.I),
    # 1 of 10
    re.compile(r"\b\d+\s*(?:of|/)\s*\d+\b", re.I),
    # - 3 -
    re.compile(r"^\s*[-–—]\s*\d+\s*[-–—]\s*$", re.M),
    # (3)
    re.compile(r"^\s*\(\d+\)\s*$", re.M),
    # [3]
    re.compile(r"^\s*\[\d+\]\s*$", re.M),
    # p. 3 / pg 3
    re.compile(r"\b(?:p|pg)\.?\s*\d+\b", re.I),
    # Roman numeral page numbers
    re.compile(r"^\s*[ivxlcdm]{1,7}\s*$", re.I | re.M),
]


class DocumentSanitizer:
    DEFAULT_CONFIG = {
        "remove_header": REMOVE_HEADERS,
        "remove_footer": REMOVE_FOOTERS,
        "remove_page_numbers": REMOVE_PAGE_NUMBERS,
        "remove_numeric_values": REMOVE_ALL_NUMBERS,
        "apply_text_formatting": APPLY_TEXT_FORMATTING,
        "debug_visualize": DEBUG_POST_PROCESS_FILE,
        "debug_filename": None,
    }

    def __init__(self, config=None, threshold_ratio=0.75):
        self.threshold_ratio = threshold_ratio
        self.config = config or self.DEFAULT_CONFIG.copy()

    def _get_bool_config(self, key, default=True):
        """Return bool value for config"""
        val = self.config.get(key, default)
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes", "on")
        return bool(val)

def preprocess_text(text, noise_list=None):
    # Strip non-printable ASCII control characters (specifically \x07)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    # Header footer noise
    if noise_list:
        lines = text.splitlines()
        # Remove line if its normalized version is in our noise list
        text = "\n".join(
            [
                line
                for line in lines
                if re.sub(r"\d+", "", line).strip() not in noise_list
            ]
        )

    text = remove_page_numbers(text)
    text = RE_ANY_NUMBER.sub("", text)

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

    text = re.sub(r"__KEEP\d+__(.*?)__KEEP\d+__", r"\1", text)
    return text


def remove_page_numbers(text):
    """Removes various page number formats"""
    for pattern in RE_PAGE_PATTERN:
        text = pattern.sub("", text)
    return text

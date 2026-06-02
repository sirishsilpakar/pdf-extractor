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

    def _iter_text_lines(self, page_dict):
        """Generates tuples of (text, bbox, y_rel, h_rel) for printable lines in page_dict"""
        page_height = page_dict.get("height", 0)
        if page_height == 0:
            return

        # Get all lines from the page and extract the text and its bbox
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                bbox = line.get("bbox", [0, 0, 0, 0])

                # bbox is [x0, y0, x1, y1] where (x0, y0) is the top-left corner and (x1, y1) is the bottom-right corner
                y0 = bbox[1]
                h = bbox[3] - bbox[1]

                # Get the relative y position and height of the line
                y_rel = round(y0 / page_height, 2)
                h_rel = round(h / page_height, 2)

                text = line.get(
                    "text",
                    "".join([span.get("text", "") for span in line.get("spans", [])]),
                ).strip()

                # Strip non-printable ASCII control characters (specifically \x07)
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
                if not text:
                    continue

                yield text, bbox, y_rel, h_rel

    def _build_debug_box(self, text, bbox, is_noise):
        """Builds an HTML div block representing a bounding box for layout visualization"""
        color = "red" if is_noise else "green"
        return (
            f'<div style="position: absolute; left: {bbox[0]}px; top: {bbox[1]}px; '
            f"width: {bbox[2]-bbox[0]}px; height: {bbox[3]-bbox[1]}px; "
            f'border: 1px solid {color}; color: {color}; font-size: 10px; overflow: hidden;" title="{text}">{text}</div>'
        )

    def _profile_layout(self, pages):
        """Analyzes pages spatially to identify repeating header and footer coordinates"""
        noise_y_coords = set()
        if not (
            self._get_bool_config("remove_header")
            or self._get_bool_config("remove_footer")
        ):
            return noise_y_coords

        y_frequencies = {}
        for page_dict in pages:
            seen_y = set()
            for _, _, y_rel, h_rel in self._iter_text_lines(page_dict):
                # Trench is a tuple of (y_rel, h_rel) representing the relative y position and height of the line
                trench = (y_rel, h_rel)

                # Define header and footer regions as the top and bottom 20% of the page
                is_header = y_rel < 0.20
                is_footer = y_rel > 0.80

                if (is_header and self._get_bool_config("remove_header")) or (
                    is_footer and self._get_bool_config("remove_footer")
                ):
                    if trench not in seen_y:
                        seen_y.add(trench)
                        y_frequencies[trench] = y_frequencies.get(trench, 0) + 1

        total_pages = len(pages)

        # Header/footer must appear in at least 75% of pages (default) to be removed
        threshold_count = max(3, total_pages * self.threshold_ratio)
        for trench, count in y_frequencies.items():
            if count >= threshold_count:
                noise_y_coords.add(trench)

        return noise_y_coords

    def _sanitize_page(self, page_dict, noise_y_coords, html_debug_pages):
        """Sanitizes a single page dictionary and returns the sanitized text"""
        page_clean_lines = []
        page_height = page_dict.get("height", 0)
        page_width = page_dict.get("width", 0)

        html_boxes = []

        # Spatially extract the raw body text
        for text, bbox, y_rel, h_rel in self._iter_text_lines(page_dict):
            trench = (y_rel, h_rel)

            is_noise = False
            if trench in noise_y_coords:
                # If the line is in the top 20% and remove header is enabled mark as noise
                if y_rel < 0.20 and self._get_bool_config("remove_header"):
                    is_noise = True

                # If the line is in the bottom 20% and remove footer is enabled mark as noise
                elif y_rel > 0.80 and self._get_bool_config("remove_footer"):
                    is_noise = True

            if self._get_bool_config("debug_visualize", False):
                html_boxes.append(self._build_debug_box(text, bbox, is_noise))

            if is_noise:
                continue  # Skip known header/footer

            page_clean_lines.append(text)

        if self._get_bool_config("debug_visualize", False):
            html_debug_pages.append(
                f'<div style="position: relative; width: {page_width}px; height: {page_height}px; border: 2px solid black; margin-bottom: 20px; background: white;">'
                f'{"".join(html_boxes)}</div>'
            )

        # Reconstruct the raw page string
        raw_page_text = "\n".join(page_clean_lines)

        # Apply regex pipelines
        return self._apply_regex_pipeline(raw_page_text)

    def process_pages(self, pages: list[dict]):
        """Process pages and return the sanitized text"""
        total_pages = len(pages)
        if total_pages == 0:
            return []

        noise_y_coords = self._profile_layout(pages)

        # Extraction and Regex based cleaning
        clean_document_text = []
        html_debug_pages = []

        for page_dict in pages:
            sanitized_text = self._sanitize_page(
                page_dict, noise_y_coords, html_debug_pages
            )
            if sanitized_text.strip():
                clean_document_text.append(sanitized_text)

        # Output visual debug file if required
        if self._get_bool_config("debug_visualize", False) and self.config.get(
            "debug_filename"
        ):
            html_content = (
                "<html><body style='background-color: #e0e0e0; padding: 20px;'>"
                + "".join(html_debug_pages)
                + "</body></html>"
            )
            try:
                with open(
                    self.config.get("debug_filename"), "w", encoding="utf-8"
                ) as f:
                    f.write(html_content)
            except Exception as e:
                print(f"Failed to write debug HTML: {e}")

        return clean_document_text

    def _apply_regex_pipeline(self, text):
        """Executes the specific regexes on a block of extracted text"""
        if not text:
            return text

        # Strip non-printable ASCII control characters (specifically \x07)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

        # Remove Page Numbers
        if self._get_bool_config("remove_page_numbers"):
            text = remove_page_numbers(text)

        # Aggressive Number Removal (removes any type of numeric value)
        if self._get_bool_config("remove_numeric_values"):
            text = RE_ANY_NUMBER.sub("", text)

        # Formatting, Hyphenation, and Unicode
        if self._get_bool_config("apply_text_formatting"):
            text = RE_HYPHEN.sub("", text)  # remove line-break hyphenation
            text = RE_BULLET.sub(" ", text)  # common bullets/arrows
            text = RE_UNICODE.sub(" ", text)  # unicode bullets
            text = RE_REPEAT.sub("", text)  # remove repetitive spc char
            text = RE_NON_ALPHA.sub(" ", text)  # strip stray symbols

            # Replace fancy quotes
            text = text.replace("“", '"').replace("”", '"').replace("’", "'")

            # Handle specific KEEP tokens
            text = re.sub(r"__KEEP\d+__(.*?)__KEEP\d+__", r"\1", text)

            # Collapse whitespace last so it cleans up the spaces left by earlier regexes
            text = RE_WHITESP.sub(" ", text).strip()

        return text


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

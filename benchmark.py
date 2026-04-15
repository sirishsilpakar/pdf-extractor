import argparse
import csv
import importlib
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import pymupdf
from PIL import Image
from tqdm import tqdm


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def iter_pdf_files(root: str) -> List[str]:
    files: List[str] = []
    for r, _, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(".pdf"):
                files.append(os.path.join(r, f))
    return files


ExtractorFn = Callable[[str], str]


@dataclass
class Extractor:
    name: str
    func: ExtractorFn
    available: Callable[[], bool]
    description: str = ""


_REGISTRY: Dict[str, Extractor] = {}


def _has_module(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def register_extractor(
    name: str,
    available: Callable[[], bool] = lambda: True,
    description: str = "",
):
    def decorator(fn: ExtractorFn) -> ExtractorFn:
        _REGISTRY[name] = Extractor(
            name=name, func=fn, available=available, description=description
        )
        return fn

    return decorator


@register_extractor("pymupdf", description="Direct embedded text via PyMuPDF")
def extract_pymupdf_text(pdf_path: str) -> str:
    with pymupdf.open(pdf_path) as doc:
        texts: List[str] = []
        for page in doc:
            texts.append(page.get_text("text"))
        return "".join(texts)


@register_extractor(
    "tesseract",
    available=lambda: _has_module("pytesseract"),
    description="OCR by rendering pages to images then pytesseract (supports page-level threads)",
)
def extract_tesseract_text(pdf_path: str) -> str:
    try:
        import pytesseract  # lazy import
    except Exception:
        raise RuntimeError("pytesseract not installed")

    page_workers = int(os.environ.get("BENCH_TESS_PAGE_WORKERS", "1"))
    if page_workers <= 1:
        texts: List[str] = []
        with pymupdf.open(pdf_path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                img = Image.frombytes(
                    "L", [gray_pix.width, gray_pix.height], gray_pix.samples
                )
                try:
                    text = pytesseract.image_to_string(img)
                finally:
                    pix = gray_pix = img = None  # free memory
                texts.append(text)
        return "".join(texts)
    # Threaded per-page OCR
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with pymupdf.open(pdf_path) as doc_info:
        num_pages = doc_info.page_count

    def ocr_page(idx: int) -> Tuple[int, str]:
        with pymupdf.open(pdf_path) as doc_local:
            page = doc_local.load_page(idx)
            pix = page.get_pixmap(dpi=200)
            gray_pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
            img = Image.frombytes(
                "L", [gray_pix.width, gray_pix.height], gray_pix.samples
            )
        try:
            text = pytesseract.image_to_string(img)
        finally:
            pix = gray_pix = img = None
        return idx, text

    results_buf: List[str] = [""] * num_pages
    with ThreadPoolExecutor(max_workers=page_workers) as pool:
        futures = [pool.submit(ocr_page, i) for i in range(num_pages)]
        for fut in as_completed(futures):
            i, t = fut.result()
            results_buf[i] = t
    return "".join(results_buf)


@register_extractor(
    "pdfminer",
    available=lambda: _has_module("pdfminer.high_level"),
    description="Text extraction via pdfminer.six",
)
def extract_pdfminer_text(pdf_path: str) -> str:
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract_text
    except Exception:
        raise RuntimeError("pdfminer.six not installed")
    return pdfminer_extract_text(pdf_path) or ""


@register_extractor(
    "pdfplumber",
    available=lambda: _has_module("pdfplumber"),
    description="Text extraction via pdfplumber",
)
def extract_pdfplumber_text(pdf_path: str) -> str:
    try:
        import pdfplumber
    except Exception:
        raise RuntimeError("pdfplumber not installed")
    texts: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return "".join(texts)


@register_extractor(
    "pypdf2",
    available=lambda: _has_module("PyPDF2"),
    description="Text extraction via PyPDF2",
)
def extract_pypdf2_text(pdf_path: str) -> str:
    try:
        from PyPDF2 import PdfReader
    except Exception:
        raise RuntimeError("PyPDF2 not installed")
    reader = PdfReader(pdf_path)
    texts: List[str] = []
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return "".join(texts)


def try_import_easyocr():
    try:
        import easyocr

        return easyocr
    except Exception:
        return None


@register_extractor(
    "easyocr",
    available=lambda: _has_module("easyocr"),
    description="OCR via EasyOCR (basic English model)",
)
def extract_easyocr_text(pdf_path: str) -> str:
    easyocr = try_import_easyocr()
    if not easyocr:
        raise RuntimeError("easyocr not installed")
    reader = easyocr.Reader(["en"])
    texts: List[str] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_path = os.path.join(".tmp_bench", f"{time.time_ns()}.png")
            ensure_dir(os.path.dirname(img_path))
            pix.save(img_path)
            result = reader.readtext(img_path, detail=0, paragraph=True)
            texts.append("\n".join(result))
            try:
                os.remove(img_path)
            except Exception:
                pass
    return "".join(texts)


@register_extractor(
    "marker",
    available=lambda: _has_module("marker"),
    description="Marker auto-triage (OCR if tables detected, else standard)",
)
def extract_marker_text(pdf_path: str) -> str:
    def _marker_text_from_rendered(rendered) -> str:
        print(rendered)
        try:
            from marker.output import text_from_rendered as tfr

            txt, _, _ = tfr(rendered)
            print(rendered)
            print(txt)
            return txt if isinstance(txt, str) else ""
        except Exception as e:
            print("Error", e)
        if isinstance(rendered, dict):
            for key in ("text", "markdown", "md"):
                val = rendered.get(key)
                if isinstance(val, str) and val.strip():
                    return val
        return ""

    def _rendered_has_tables(obj) -> bool:
        try:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(k, str) and "table" in k.lower():
                        if (isinstance(v, (list, dict)) and len(v) != 0) or (
                            isinstance(v, str) and v.strip()
                        ):
                            return True
                    if _rendered_has_tables(v):
                        return True
            elif isinstance(obj, list):
                for item in obj:
                    if _rendered_has_tables(item):
                        return True
        except Exception:
            return False
        return False

    def _run_marker_ocr(p: str) -> str:
        from marker.converters.ocr import OCRConverter
        from marker.models import create_model_dict as create_fn

        ocr_converter = OCRConverter(artifact_dict=create_fn())
        rendered_ocr = ocr_converter(p)
        print("rendered_ocr", rendered_ocr)
        text_ocr = _marker_text_from_rendered(rendered_ocr)
        if not text_ocr.strip():
            raise RuntimeError("marker OCR returned empty text")
        return text_ocr

    # Try standard path first to detect tables quickly
    try:
        from marker.convert import TableConverter, create_model_dict

        converter = TableConverter(artifact_dict=create_model_dict())
        rendered = converter(pdf_path)
        # if _rendered_has_tables(rendered):
        # return _run_marker_ocr(pdf_path)
        # No tables detected → use standard text
        txt = _marker_text_from_rendered(rendered)
        if txt.strip():
            return txt
        # If empty, fallback to OCR
        return _run_marker_ocr(pdf_path)
    except Exception:
        # If standard path unavailable, try OCR directly
        return _run_marker_ocr(pdf_path)


def benchmark_extractors(
    input_root: str,
    out_root: str,
    extractors: Dict[str, ExtractorFn],
    per_job_timeout: float | None = None,
    parallel_workers: int = 1,
) -> List[Dict[str, str]]:
    pymupdf.TOOLS.mupdf_display_errors(False)
    ensure_dir(out_root)

    pdf_files = iter_pdf_files(input_root)
    results: List[Dict[str, str]] = []

    tasks = [(pdf_path, name) for pdf_path in pdf_files for name in extractors.keys()]

    def _exec_task(pdf_path: str, name: str) -> Dict[str, str]:
        rel_no_ext = os.path.splitext(os.path.relpath(pdf_path, input_root))[0]
        text_out_dir = os.path.join(out_root, name)
        ensure_dir(os.path.join(text_out_dir, os.path.dirname(rel_no_ext)))
        text_out_path = os.path.join(text_out_dir, f"{rel_no_ext}.txt")
        start = time.time()
        status = "ok"
        error_msg = ""
        text = ""
        try:
            text = extractors[name](pdf_path)
        except Exception as e:
            status = "error"
            error_msg = str(e)
        duration = time.time() - start
        if status == "ok":
            try:
                with open(text_out_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as io_e:
                status = "error"
                error_msg = f"write failed: {io_e}"
        return {
            "file": pdf_path,
            "extractor": name,
            "status": status,
            "seconds": f"{duration:.3f}",
            "chars": str(len(text)),
            "error": error_msg,
        }

    pbar = tqdm(total=len(tasks), desc="Extracting", unit="job")
    if parallel_workers and parallel_workers > 1:
        with mp.get_context("spawn").Pool(processes=parallel_workers) as pool:
            for res in pool.imap_unordered(lambda t: _exec_task(*t), tasks):
                results.append(res)
                pbar.set_postfix_str(
                    f"{res['extractor']} → {os.path.basename(res['file'])}",
                    refresh=False,
                )
                pbar.update(1)
    else:
        for pdf_path, name in tasks:
            pbar.set_postfix_str(
                f"{name} → {os.path.basename(pdf_path)}", refresh=False
            )
            res = _exec_task(pdf_path, name)
            results.append(res)
            pbar.update(1)
    pbar.close()

    return results


def write_summary(results: List[Dict[str, str]], out_root: str) -> None:
    # CSV
    csv_path = os.path.join(out_root, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["file", "extractor", "status", "seconds", "chars", "error"]
        )
        writer.writeheader()
        writer.writerows(results)

    # JSON
    json_path = os.path.join(out_root, "summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def default_extractors() -> Dict[str, ExtractorFn]:
    available: Dict[str, ExtractorFn] = {}
    for name, ex in _REGISTRY.items():
        if ex.available():
            available[name] = ex.func
    return available


def list_extractors() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for name, ex in sorted(_REGISTRY.items()):
        items.append(
            {
                "name": name,
                "available": str(ex.available()),
                "description": ex.description,
            }
        )
    return items


def _tokenize(text: str) -> List[str]:
    return [t for t in text.lower().split() if t]


def _jaccard(a: str, b: str) -> float:
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def _fuzzy_ratio(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.ratio(a, b)) / 100.0
    except Exception:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, a, b).ratio()


def _tfidf_cosine(a: str, b: str) -> str:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except Exception:
        return ""
    vec = TfidfVectorizer(min_df=1)
    mat = vec.fit_transform([a, b])
    sim = cosine_similarity(mat[0:1], mat[1:2])[0, 0]
    return f"{sim:.4f}"


def _read_text_if_exists(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def write_comparisons(
    input_root: str,
    out_root: str,
    extractors: List[str],
    baseline: str | None,
    write_diffs: bool,
) -> None:
    pdf_files = iter_pdf_files(input_root)
    rows: List[Dict[str, str]] = []

    # Prepare diff dir
    diff_dir = os.path.join(out_root, "diffs")
    if write_diffs:
        ensure_dir(diff_dir)

    for pdf_path in tqdm(pdf_files, desc="Comparing outputs", unit="file"):
        rel_no_ext = os.path.splitext(os.path.relpath(pdf_path, input_root))[0]

        texts = {
            name: _read_text_if_exists(
                os.path.join(out_root, name, f"{rel_no_ext}.txt")
            )
            for name in extractors
        }

        pairs: List[tuple[str, str]] = []
        if baseline and baseline in texts:
            pairs = [(baseline, name) for name in extractors if name != baseline]
        else:
            for i in range(len(extractors)):
                for j in range(i + 1, len(extractors)):
                    pairs.append((extractors[i], extractors[j]))

        for a, b in pairs:
            ta = texts.get(a, "")
            tb = texts.get(b, "")
            if not ta and not tb:
                continue
            row = {
                "file": pdf_path,
                "a": a,
                "b": b,
                "a_chars": str(len(ta)),
                "b_chars": str(len(tb)),
                "jaccard": f"{_jaccard(ta, tb):.4f}",
                "fuzzy_ratio": f"{_fuzzy_ratio(ta, tb):.4f}",
                "tfidf_cosine": _tfidf_cosine(ta, tb),
            }
            rows.append(row)

            if write_diffs:
                try:
                    from difflib import HtmlDiff

                    html = HtmlDiff().make_file(
                        ta.splitlines(),
                        tb.splitlines(),
                        fromdesc=a,
                        todesc=b,
                    )
                    html_path = os.path.join(
                        diff_dir,
                        f"{os.path.basename(rel_no_ext)}__{a}_vs_{b}.html",
                    )
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(html)
                except Exception:
                    pass

    # Write CSV
    csv_path = os.path.join(out_root, "comparisons.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "a",
                "b",
                "a_chars",
                "b_chars",
                "jaccard",
                "fuzzy_ratio",
                "tfidf_cosine",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    # Write JSON
    json_path = os.path.join(out_root, "comparisons.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Benchmark multiple PDF extractors/OCR engines"
    )
    parser.add_argument("pdf_root", help="Directory tree with PDFs")
    parser.add_argument(
        "out_dir",
        nargs="?",
        default="benchmark_output",
        help="Output directory (default: benchmark_output)",
    )
    parser.add_argument(
        "--extractors",
        help="Comma-separated list of extractor names to run (use --list to see options)",
        default=None,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available/registered extractors and exit",
    )
    parser.add_argument(
        "--baseline", help="Baseline extractor name for comparisons", default=None
    )
    parser.add_argument(
        "--no-compare", action="store_true", help="Skip pairwise text comparisons"
    )
    parser.add_argument(
        "--diffs",
        action="store_true",
        help="Also write HTML side-by-side diffs (baseline vs others if baseline provided, else all pairs)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per (file, extractor) timeout in seconds for the benchmark run (unused in parallel mode)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Process-level parallelism for benchmark jobs (file, extractor)",
    )
    parser.add_argument(
        "--tesseract-page-workers",
        type=int,
        default=1,
        help="Enable page-level threading inside the Tesseract extractor (per PDF)",
    )
    args = parser.parse_args()

    if args.list:
        rows = list_extractors()
        print("Registered extractors:")
        for row in rows:
            print(
                f"- {row['name']}: available={row['available']} — {row['description']}"
            )
        sys.exit(0)

    in_root = args.pdf_root
    out_root = args.out_dir

    all_extractors = default_extractors()
    if not all_extractors:
        print("No extractors available. Please install dependencies.")
        sys.exit(1)

    if args.extractors:
        selected = [x.strip() for x in args.extractors.split(",") if x.strip()]
        missing = [x for x in selected if x not in all_extractors]
        if missing:
            print(f"Unknown or unavailable extractors requested: {', '.join(missing)}")
            print("Use --list to inspect what's available.")
            sys.exit(1)
        run_map = {name: all_extractors[name] for name in selected}
    else:
        run_map = all_extractors

    # Threading control for Tesseract
    os.environ["BENCH_TESS_PAGE_WORKERS"] = str(
        max(1, int(args.tesseract_page_workers))
    )

    print(f"Running {len(run_map)} extractors: {', '.join(run_map.keys())}")
    results = benchmark_extractors(
        in_root,
        out_root,
        run_map,
        per_job_timeout=args.timeout,
        parallel_workers=max(1, int(args.parallel)),
    )
    write_summary(results, out_root)
    print(f"Wrote results to {out_root}/summary.csv and summary.json")

    if not args.no_compare:
        ex_order = list(run_map.keys())
        write_comparisons(
            input_root=in_root,
            out_root=out_root,
            extractors=ex_order,
            baseline=args.baseline,
            write_diffs=args.diffs,
        )
        print(
            f"Wrote pairwise comparisons to {out_root}/comparisons.csv and comparisons.json"
        )

import argparse
import sqlite3
from typing import List, Dict, Any
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import DB_PATH


def sample_db(sample_size: int):
    print(f"Sampling {sample_size} random extractions from {DB_PATH}...\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Grab totally random samples that have been successfully processed
    try:
        rows = conn.execute(
            """
            SELECT filename, method, confidence, flags, char_count
            FROM extracted_texts
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (sample_size,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Failed to query database. Has it been initialized? {e}")
        return

    if not rows:
        print("Database is empty or no extractions have been made.")
        return

    # Aggregate stats
    total = len(rows)
    high_conf = 0
    low_conf = 0
    flag_counts: Dict[str, int] = {}
    method_counts: Dict[str, int] = {"direct": 0, "ocr": 0, "error": 0}

    print(
        f"{'Filename':<30} | {'Method':<8} | {'Conf.':<6} | {'Flags':<20} | {'Chars'}"
    )
    print("-" * 80)
    for row in rows:
        fn = (
            (row["filename"][:27] + "...")
            if len(row["filename"]) > 30
            else row["filename"]
        )
        mth = row["method"] or "None"
        conf = row["confidence"]
        flags = row["flags"] or "[]"
        chars = row["char_count"]

        # update stats
        method_counts[mth] = method_counts.get(mth, 0) + 1

        if conf is not None:
            if conf >= 0.85:
                high_conf += 1
            else:
                low_conf += 1

        if flags and flags != "[]":
            # Very basic string split assuming comma separation
            flag_list = flags.split(",") if "," in flags else [flags]
            for f in flag_list:
                f = f.strip().strip("[]'\"")
                if f:
                    flag_counts[f] = flag_counts.get(f, 0) + 1

        conf_str = f"{conf:.2f}" if conf is not None else "N/A"
        print(f"{fn:<30} | {mth:<8} | {conf_str:<6} | {flags:<20} | {chars}")

    print("\n" + "=" * 80)
    print("MACRO-LEVEL HEALTH METRICS")
    print("=" * 80)
    print(f"Total Sample Size:     {total} documents")
    print(
        f"Extraction Methods:    DIRECT={method_counts.get('direct', 0)} | OCR={method_counts.get('ocr', 0)}"
    )

    conf_pct = (high_conf / total) * 100 if total > 0 else 0
    print(f"High Quality (>85%):   {high_conf} documents ({conf_pct:.1f}%)")

    if flag_counts:
        print("\nIdentified Flags across Sample:")
        for flag, count in flag_counts.items():
            print(f"  - {flag:<20} {count} files")
    else:
        print("\nIdentified Flags: None. (All files nominal).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test PDF extraction SLA quality by sampling DB."
    )
    parser.add_argument(
        "--size",
        type=int,
        default=100,
        help="Number of files to random sample from DB.",
    )
    args = parser.parse_args()
    sample_db(args.size)

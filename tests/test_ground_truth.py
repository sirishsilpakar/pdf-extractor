"""Test script to validate ground truth extraction"""

import os
import json
import difflib
import sqlite3
from config import DB_PATH


def test_accuracy():
    ground_truth_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "ground_truth")
    )
    expected_path = os.path.join(ground_truth_dir, "expected.json")

    if not os.path.exists(expected_path):
        print(
            f"File not found: {expected_path}. Please run generate_ground_truth.py first."
        )
        return

    with open(expected_path, "r") as fh:
        expected_texts = json.load(fh)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"\n{'File':<30} | {'Method':<8} | {'Conf.':<6} | {'Accuracy':<8}")
    print("-" * 65)

    total_accuracy = 0.0

    for filename, expected_text in expected_texts.items():
        # Fetch the latest extraction
        row = conn.execute(
            "SELECT txt_path, method, confidence, flags FROM extracted_texts WHERE filename=? ORDER BY processed_at DESC LIMIT 1",
            (filename,),
        ).fetchone()

        if not row:
            print(
                f"{filename:<30} | {'N/A':<8} | {'N/A':<6} | {'N/A':<8} (Not Extracted)"
            )
            continue

        txt_path = row["txt_path"]
        method = row["method"]
        conf = row["confidence"]

        try:
            with open(txt_path, "r") as f:
                actual_text = f.read()
        except Exception:
            actual_text = ""

        # Remove extra whitespace
        expected_clean = " ".join(expected_text.split())
        actual_clean = " ".join(actual_text.split())

        sm = difflib.SequenceMatcher(None, expected_clean, actual_clean)
        accuracy = sm.ratio()
        total_accuracy += accuracy

        conf_str = f"{conf:.2f}" if conf is not None else "N/A"
        acc_str = f"{accuracy*100:.1f}%"

        print(f"{filename:<30} | {method:<8} | {conf_str:<6} | {acc_str:<8}")

    print("-" * 65)
    overall = (total_accuracy / len(expected_texts)) * 100
    print(f"Overall Accuracy: {overall:.1f}%")


if __name__ == "__main__":
    test_accuracy()

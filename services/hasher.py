"""Content fingerprinting for PDF deduplication.

Strategy: SHA-256 of the **first 32 KB** concatenated with the **last 32 KB**
(total 64 KB sampled).  This catches:
- Header / metadata differences (first 32 KB)
- Trailing-content differences, e.g. two documents with a common template
  that differ only in their final pages (last 32 KB)
- Truncated files (tail is empty → hash differs from complete file)

Boundary behaviour
------------------
- file <= 32 KB    -> hash = SHA-256(entire file)   [tail read is empty]
- 32 KB < file <= 64 KB -> hash = SHA-256(entire file)   [tail starts at head end]
- file > 64 KB    -> hash = SHA-256(first 32 KB || last 32 KB)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# 32 KB head + 32 KB tail = 64 KB total sampled
HEAD_SAMPLE_BYTES: int = 32 * 1024
TAIL_SAMPLE_BYTES: int = 32 * 1024

# Kept for backward-compatibility imports (e.g. upload.py uses HASH_SAMPLE_BYTES
# to know how many bytes of a streaming upload to hash on-the-fly)
# It still equals 64 KB — the *total* bytes sampled
HASH_SAMPLE_BYTES: int = HEAD_SAMPLE_BYTES + TAIL_SAMPLE_BYTES


def compute_file_hash(path: str | Path) -> str:
    """Return hex-encoded SHA-256 of the first+last 32 KB of path

    For files <= 64 KB the entire content is hashed (see module docstring)
    Safe for files of any size no full load into RAM

    Args:
        path: Absolute or relative path to any file

    Returns:
        64-character lowercase hex string
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        # Head: up to 32 KB
        head = fh.read(HEAD_SAMPLE_BYTES)
        h.update(head)

        # Tail: last 32 KB, non-overlapping with head
        file_size = fh.seek(0, 2)  # seek to EOF -> returns file size
        tail_start = max(len(head), file_size - TAIL_SAMPLE_BYTES)
        if tail_start < file_size:  # there's something to read after head
            fh.seek(tail_start)
            tail = fh.read(TAIL_SAMPLE_BYTES)
            h.update(tail)

    return h.hexdigest()


def compute_bytes_hash(data: bytes) -> str:
    """Return hex-encoded SHA-256 of the first 'HEAD_SAMPLE_BYTES' of data

    Used for hashing the leading chunk of a streaming upload (the tail is not
    available until the full file has been written to disk)
    Produces the same result as 'compute_file_hash' **only** for files whose
    total size <= 'HEAD_SAMPLE_BYTES' (32 KB)
    """
    return hashlib.sha256(data[:HEAD_SAMPLE_BYTES]).hexdigest()

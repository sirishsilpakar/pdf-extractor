"""Tests for services/hasher.py"""

from pathlib import Path

import pytest

from services.hasher import (
    HASH_SAMPLE_BYTES,
    HEAD_SAMPLE_BYTES,
    TAIL_SAMPLE_BYTES,
    compute_bytes_hash,
    compute_file_hash,
)


def test_compute_bytes_hash_deterministic():
    data = b"hello world " * 1000
    h1 = compute_bytes_hash(data)
    h2 = compute_bytes_hash(data)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_bytes_hash_different_for_different_content():
    assert compute_bytes_hash(b"aaa") != compute_bytes_hash(b"bbb")


def test_compute_file_hash_file(tmp_path):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 " + b"X" * 200)
    h = compute_file_hash(f)
    assert len(h) == 64
    # Same content same hash
    assert compute_file_hash(f) == h


def test_hash_sample_bytes_is_total(tmp_path):
    """HASH_SAMPLE_BYTES must equal HEAD + TAIL for upload.py compatibility"""
    assert HASH_SAMPLE_BYTES == HEAD_SAMPLE_BYTES + TAIL_SAMPLE_BYTES


def test_identical_head_different_tail_yields_different_hash(tmp_path):
    """Files sharing the first 32 KB but differing in their last 32 KB must hash differently"""
    prefix = b"A" * HEAD_SAMPLE_BYTES  # same first 32 KB
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    # Pad to > 64 KB so tail region is actually different
    f1.write_bytes(prefix + b"X" * TAIL_SAMPLE_BYTES)
    f2.write_bytes(prefix + b"Y" * TAIL_SAMPLE_BYTES)
    assert compute_file_hash(f1) != compute_file_hash(f2)


def test_identical_prefix_and_tail_yields_same_hash(tmp_path):
    """Files with identical head+tail but different middle bytes should collide.

    This is expected behaviour, we only sample 64 KB total
    """
    head = b"H" * HEAD_SAMPLE_BYTES
    tail = b"T" * TAIL_SAMPLE_BYTES
    middle1 = b"M" * 1024
    middle2 = b"N" * 1024
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    f1.write_bytes(head + middle1 + tail)
    f2.write_bytes(head + middle2 + tail)
    assert compute_file_hash(f1) == compute_file_hash(f2)


def test_small_file_hashed_in_full(tmp_path):
    """A file smaller than 64 KB must be hashed completely (no tail seek needed)"""
    data = b"small" * 100  # well under HEAD_SAMPLE_BYTES
    f = tmp_path / "small.pdf"
    f.write_bytes(data)
    h = compute_file_hash(f)
    # Manually hash the full content — should match
    import hashlib

    expected = hashlib.sha256(data).hexdigest()
    assert h == expected


def test_compute_file_hash_differs_for_different_prefix(tmp_path):
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    f1.write_bytes(b"AAAA" + b"X" * 100)
    f2.write_bytes(b"BBBB" + b"X" * 100)
    assert compute_file_hash(f1) != compute_file_hash(f2)

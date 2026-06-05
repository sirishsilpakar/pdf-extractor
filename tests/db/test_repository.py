"""Tests for DatabaseRepository"""

from __future__ import annotations

import pytest

from db.repository import DatabaseRepository

# tmp_db fixture comes from conftest.py


def test_schema_init_is_idempotent(tmp_db):
    tmp_db.init_schema()
    tmp_db.init_schema()
    # Should not raise


def test_mark_started_and_completed(tmp_db):
    tmp_db.mark_started("/path/to/a.pdf", content_hash="abc123")
    tmp_db.mark_completed("/path/to/a.pdf")


def test_mark_failed(tmp_db):
    tmp_db.mark_started("/path/to/b.pdf")
    tmp_db.mark_failed("/path/to/b.pdf", "some error")


def test_get_processed_hashes_empty(tmp_db):
    assert tmp_db.get_processed_hashes() == set()


def test_save_and_retrieve(tmp_db):
    tmp_db.save_extracted_text(
        source_path="/path/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=1000,
        page_count=5,
        content_hash="deadbeef",
    )
    total, items = tmp_db.get_extracted_texts(page=1, size=50)
    assert total == 1
    assert items[0]["filename"] == "a.pdf"
    assert items[0]["content_hash"] == "deadbeef"


def test_get_processed_hashes_returns_saved(tmp_db):
    tmp_db.save_extracted_text(
        source_path="/path/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=10,
        page_count=1,
        content_hash="myhash",
    )
    hashes = tmp_db.get_processed_hashes()
    assert "myhash" in hashes


def test_get_by_hashes_bulk(tmp_db):
    for i in range(3):
        tmp_db.save_extracted_text(
            source_path=f"/path/{i}.pdf",
            filename=f"{i}.pdf",
            rel_path=f"{i}.pdf",
            txt_path=f"/out/{i}.txt",
            method="direct",
            char_count=10,
            page_count=1,
            content_hash=f"hash{i}",
        )
    result = tmp_db.get_by_hashes(["hash0", "hash2", "nonexistent"])
    assert "hash0" in result
    assert "hash2" in result
    assert "nonexistent" not in result


def test_pagination(tmp_db):
    for i in range(10):
        tmp_db.save_extracted_text(
            source_path=f"/path/{i}.pdf",
            filename=f"file{i}.pdf",
            rel_path=f"file{i}.pdf",
            txt_path=f"/out/file{i}.txt",
            method="direct",
            char_count=i * 100,
            page_count=1,
            content_hash=f"hash{i}",
        )
    total, page1 = tmp_db.get_extracted_texts(page=1, size=3)
    assert total == 10
    assert len(page1) == 3

    _, page4 = tmp_db.get_extracted_texts(page=4, size=3)
    assert len(page4) == 1  # 10 items, page 4 of size 3 = 1 item


def test_delete_by_id(tmp_db):
    tmp_db.save_extracted_text(
        source_path="/p.pdf",
        filename="p.pdf",
        rel_path="p.pdf",
        txt_path="/out/p.txt",
        method="ocr",
        char_count=50,
        page_count=2,
        content_hash="todelete",
    )
    total, items = tmp_db.get_extracted_texts()
    rec_id = items[0]["id"]
    tmp_db.delete_by_id(rec_id)
    total, items = tmp_db.get_extracted_texts()
    assert total == 0


def test_reset(tmp_db):
    tmp_db.save_extracted_text(
        source_path="/r.pdf",
        filename="r.pdf",
        rel_path="r.pdf",
        txt_path="/out/r.txt",
        method="direct",
        char_count=1,
        page_count=1,
        content_hash="resethash",
    )
    tmp_db.reset()
    total, _ = tmp_db.get_extracted_texts()
    assert total == 0


# ---------------------------------------------------------------------------
# Batched mark_started
# ---------------------------------------------------------------------------


def test_mark_started_batch(tmp_db):
    paths = [f"/path/{i}.pdf" for i in range(50)]
    tmp_db.mark_started_batch(paths)
    # All rows should now have status PROCESSING
    import sqlite3

    conn = sqlite3.connect(str(tmp_db._db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE status='PROCESSING'"
    ).fetchone()[0]
    conn.close()
    assert count == 50


# ---------------------------------------------------------------------------
# save_extracted_text returns ID
# ---------------------------------------------------------------------------


def test_save_returns_id(tmp_db):
    rid = tmp_db.save_extracted_text(
        source_path="/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=10,
        page_count=1,
        content_hash="h1",
    )
    assert isinstance(rid, int) and rid > 0
    # Upsert same filename - should return the existing row id
    rid2 = tmp_db.save_extracted_text(
        source_path="/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=20,
        page_count=1,
        content_hash="h1",
    )
    assert isinstance(rid2, int) and rid2 > 0


# ---------------------------------------------------------------------------
# Runs table
# ---------------------------------------------------------------------------


def test_create_and_get_run(tmp_db):
    tmp_db.create_run(run_id="run-abc", total_files=10, input_dir="/data")
    row = tmp_db.get_run("run-abc")
    assert row is not None
    assert row["status"] == "running"
    assert row["total_files"] == 10


def test_update_run_with_method_counts(tmp_db):
    tmp_db.create_run(run_id="run-xyz", total_files=5)
    tmp_db.update_run(
        run_id="run-xyz",
        status="done",
        done_files=4,
        failed_files=1,
        direct_files=3,
        ocr_files=1,
    )
    row = tmp_db.get_run("run-xyz")
    assert row["status"] == "done"
    assert row["direct_files"] == 3
    assert row["ocr_files"] == 1
    assert row["failed_files"] == 1


def test_get_run_files(tmp_db):
    tmp_db.create_run(run_id="run-1", total_files=2)
    for i in range(2):
        tmp_db.save_extracted_text(
            source_path=f"/{i}.pdf",
            filename=f"f{i}.pdf",
            rel_path=f"f{i}.pdf",
            txt_path=f"/out/f{i}.txt",
            method="direct",
            char_count=100,
            page_count=1,
            content_hash=f"h{i}",
            run_id="run-1",
        )
    total, items = tmp_db.get_run_files("run-1")
    assert total == 2
    assert all(r["filename"].startswith("f") for r in items)


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


def test_fts_index_and_search(tmp_db):
    rid = tmp_db.save_extracted_text(
        source_path="/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=100,
        page_count=2,
        content_hash="fts-hash",
    )
    tmp_db.fts_index_pages(
        doc_id=rid,
        filename="a.pdf",
        rel_path="a.pdf",
        page_texts=["The quick brown fox", "jumps over the lazy dog"],
    )
    total, rows = tmp_db.fts_search("fox")
    assert total == 1
    assert rows[0]["filename"] == "a.pdf"
    assert rows[0]["page_no"] == 1
    assert "fox" in rows[0]["snippet"].lower() or "<mark>" in rows[0]["snippet"]


def test_fts_search_returns_empty_for_unknown_term(tmp_db):
    total, rows = tmp_db.fts_search("xyznonexistent")
    assert total == 0
    assert rows == []


def test_fts_delete_doc(tmp_db):
    rid = tmp_db.save_extracted_text(
        source_path="/b.pdf",
        filename="b.pdf",
        rel_path="b.pdf",
        txt_path="/out/b.txt",
        method="ocr",
        char_count=50,
        page_count=1,
        content_hash="del-hash",
    )
    tmp_db.fts_index_pages(
        doc_id=rid,
        filename="b.pdf",
        rel_path="b.pdf",
        page_texts=["unique phrase that should be deleted"],
    )
    total, _ = tmp_db.fts_search("deleted")
    total_after = 0  # ensure it's deleted
    tmp_db.fts_delete_doc(rid)
    total_after, _ = tmp_db.fts_search("deleted")
    assert total_after == 0


def test_fts_is_empty_initially(tmp_db):
    assert tmp_db.fts_is_empty() is True


def test_fts_not_empty_after_index(tmp_db):
    rid = tmp_db.save_extracted_text(
        source_path="/c.pdf",
        filename="c.pdf",
        rel_path="c.pdf",
        txt_path="/out/c.txt",
        method="direct",
        char_count=10,
        page_count=1,
        content_hash="ne-hash",
    )
    tmp_db.fts_index_pages(
        doc_id=rid, filename="c.pdf", rel_path="c.pdf", page_texts=["some content"]
    )
    assert tmp_db.fts_is_empty() is False


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------


def test_schema_version_is_set(tmp_db):
    """After init_schema the user_version pragma must equal _SCHEMA_VERSION"""
    import sqlite3 as _sqlite3

    from db.repository import _SCHEMA_VERSION

    conn = _sqlite3.connect(str(tmp_db._db_path))
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == _SCHEMA_VERSION


def test_schema_migration_is_idempotent(tmp_db):
    """Running init_schema multiple times must not raise"""
    tmp_db.init_schema()
    tmp_db.init_schema()


def test_get_extracted_texts_directory_filtering(tmp_db):
    # Save a direct parent file
    tmp_db.save_extracted_text(
        source_path="/path/parent/a.pdf",
        filename="a.pdf",
        rel_path="parent/a.pdf",
        txt_path="/out/parent/a.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-parent",
    )
    # Save a nested subdirectory file
    tmp_db.save_extracted_text(
        source_path="/path/parent/sub/b.pdf",
        filename="b.pdf",
        rel_path="parent/sub/b.pdf",
        txt_path="/out/parent/sub/b.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-sub",
    )
    # Save a root file
    tmp_db.save_extracted_text(
        source_path="/path/c.pdf",
        filename="c.pdf",
        rel_path="c.pdf",
        txt_path="/out/c.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-root",
    )

    # Filter by empty string (root folder)
    total_root, items_root = tmp_db.get_extracted_texts(rel_path_prefix="")
    assert total_root == 1
    assert items_root[0]["filename"] == "c.pdf"

    # Filter by "parent" folder
    total_parent, items_parent = tmp_db.get_extracted_texts(rel_path_prefix="parent")
    assert total_parent == 1
    assert items_parent[0]["filename"] == "a.pdf"

    # Filter by "parent/sub" folder
    total_sub, items_sub = tmp_db.get_extracted_texts(rel_path_prefix="parent/sub")
    assert total_sub == 1
    assert items_sub[0]["filename"] == "b.pdf"


def test_has_duplicate_flag(tmp_db):
    # Save runs
    tmp_db.create_run(run_id="run-1", total_files=2)
    tmp_db.create_run(run_id="run-2", total_files=2)

    # File 1 is processed in both run 1 and run 2 (duplicate)
    tmp_db.save_extracted_text(
        source_path="/path1/dup.pdf",
        filename="dup.pdf",
        rel_path="dup.pdf",
        txt_path="/out1/dup.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-dup1",
        run_id="run-1",
    )
    tmp_db.save_extracted_text(
        source_path="/path2/dup.pdf",
        filename="dup.pdf",
        rel_path="dup.pdf",
        txt_path="/out2/dup.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-dup2",
        run_id="run-2",
    )

    # File 2 is processed only in run 1 (no duplicate)
    tmp_db.save_extracted_text(
        source_path="/path1/unique.pdf",
        filename="unique.pdf",
        rel_path="unique.pdf",
        txt_path="/out1/unique.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-uniq",
        run_id="run-1",
    )

    # Directory 1 is processed in both run 1 and run 2 (duplicate directory)
    tmp_db.save_extracted_text(
        source_path="/path1/dir/file_a.pdf",
        filename="file_a.pdf",
        rel_path="dir/file_a.pdf",
        txt_path="/out1/dir/file_a.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-dir1",
        run_id="run-1",
    )
    tmp_db.save_extracted_text(
        source_path="/path2/dir/file_b.pdf",
        filename="file_b.pdf",
        rel_path="dir/file_b.pdf",
        txt_path="/out2/dir/file_b.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-dir2",
        run_id="run-2",
    )

    # Directory 2 is processed only in run 1 (unique directory)
    tmp_db.save_extracted_text(
        source_path="/path1/unique_dir/file_c.pdf",
        filename="file_c.pdf",
        rel_path="unique_dir/file_c.pdf",
        txt_path="/out1/unique_dir/file_c.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="h-dir3",
        run_id="run-1",
    )

    # Assertions on get_extracted_texts
    _, items = tmp_db.get_extracted_texts(size=10)
    item_map = {item["filename"]: item for item in items}
    assert item_map["dup.pdf"]["has_duplicate"] == 1
    assert item_map["unique.pdf"]["has_duplicate"] == 0

    # Assertions on get_run_tree (folders and top-level files)
    tree = tmp_db.get_run_tree(size=10)

    dir_map = {d["path"]: d for d in tree["directories"]}
    assert dir_map["dir"]["has_duplicate"] == 1
    assert dir_map["unique_dir"]["has_duplicate"] == 0

    top_file_map = {f["filename"]: f for f in tree["top_level_files"]}
    assert top_file_map["dup.pdf"]["has_duplicate"] == 1
    assert top_file_map["unique.pdf"]["has_duplicate"] == 0

    # Assertions on get_by_id
    dup_id = item_map["dup.pdf"]["id"]
    dup_record = tmp_db.get_by_id(dup_id)
    assert dup_record["has_duplicate"] == 1

    uniq_id = item_map["unique.pdf"]["id"]
    uniq_record = tmp_db.get_by_id(uniq_id)
    assert uniq_record["has_duplicate"] == 0

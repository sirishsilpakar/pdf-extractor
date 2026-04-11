"""SQLite helpers for the PDF extraction pipeline.

Two tables:
  files           — pipeline state tracking (PROCESSING / COMPLETED / FAILED)
  extracted_texts — index of completed extractions; content lives on disk as .txt
"""

import sqlite3
import datetime
import os

from config import DB_PATH as _DEFAULT_DB


# Connection factory
def _connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or _DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Schema
def init_db(db_path=None):
    """Create tables if they don't exist."""
    conn = _connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            path           TEXT PRIMARY KEY,
            status         TEXT,
            last_processed TIMESTAMP,
            error_message  TEXT
        );

        CREATE TABLE IF NOT EXISTS extracted_texts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path  TEXT    NOT NULL,
            filename     TEXT    NOT NULL UNIQUE,    -- latest extraction per filename
            rel_path     TEXT    NOT NULL,           -- relative path from input root (preserves dir structure)
            txt_path     TEXT    NOT NULL,           -- absolute path to .txt on disk
            method       TEXT,                       -- 'ocr' | 'direct'
            char_count   INTEGER DEFAULT 0,
            page_count   INTEGER DEFAULT 0,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_et_filename ON extracted_texts(filename);
    """
    )
    conn.commit()
    conn.close()


# files table (pipeline state)
def mark_started(file_path, db_path=None):
    conn = _connect(db_path)
    now = datetime.datetime.now()
    conn.execute(
        """
        INSERT INTO files (path, status, last_processed, error_message)
        VALUES (?, 'PROCESSING', ?, NULL)
        ON CONFLICT(path) DO UPDATE
            SET status='PROCESSING', last_processed=?, error_message=NULL
    """,
        (file_path, now, now),
    )
    conn.commit()
    conn.close()


def mark_completed(file_path, db_path=None):
    conn = _connect(db_path)
    conn.execute(
        "UPDATE files SET status='COMPLETED', last_processed=? WHERE path=?",
        (datetime.datetime.now(), file_path),
    )
    conn.commit()
    conn.close()


def mark_failed(file_path, error_message, db_path=None):
    conn = _connect(db_path)
    conn.execute(
        "UPDATE files SET status='FAILED', last_processed=?, error_message=? WHERE path=?",
        (datetime.datetime.now(), error_message, file_path),
    )
    conn.commit()
    conn.close()


def get_processed_files(db_path=None):
    """Set of absolute paths marked COMPLETED (path-based, intra-run)."""
    conn = _connect(db_path)
    rows = conn.execute("SELECT path FROM files WHERE status='COMPLETED'").fetchall()
    conn.close()
    return {r[0] for r in rows}


def get_all_files(db_path=None):
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT path, status, last_processed, error_message FROM files ORDER BY last_processed DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_db(db_path=None):
    p = db_path or _DEFAULT_DB
    if os.path.exists(p):
        os.remove(p)
    init_db(p)


# extracted_texts table
def save_extracted_text(
    source_path: str,
    filename: str,
    rel_path: str,
    txt_path: str,
    method: str,
    char_count: int,
    page_count: int,
    db_path=None,
):
    """Upsert an extraction record (latest run wins per filename)."""
    conn = _connect(db_path)
    now = datetime.datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO extracted_texts
            (source_path, filename, rel_path, txt_path, method, char_count, page_count, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            source_path  = excluded.source_path,
            rel_path     = excluded.rel_path,
            txt_path     = excluded.txt_path,
            method       = excluded.method,
            char_count   = excluded.char_count,
            page_count   = excluded.page_count,
            processed_at = excluded.processed_at
    """,
        (
            source_path,
            filename,
            rel_path,
            txt_path,
            method,
            char_count,
            page_count,
            now,
        ),
    )
    conn.commit()
    conn.close()


def get_extracted_texts(limit: int = 500, offset: int = 0, db_path=None):
    """Return list of records (no content — content read from disk on demand)."""
    conn = _connect(db_path)
    rows = conn.execute(
        """
        SELECT id, source_path, filename, rel_path, txt_path,
               method, char_count, page_count, processed_at
        FROM extracted_texts
        ORDER BY processed_at DESC
        LIMIT ? OFFSET ?
    """,
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_extracted_text_by_id(record_id: int, db_path=None):
    """Return a single record by id. Caller reads txt_path from disk."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM extracted_texts WHERE id=?", (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_extracted_by_filename(filename: str, db_path=None):
    """Return the latest record for a filename, or None."""
    conn = _connect(db_path)
    row = conn.execute(
        """
        SELECT id, filename, rel_path, method, char_count, page_count, processed_at
        FROM extracted_texts WHERE filename=?
    """,
        (filename,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_processed_filenames(db_path=None):
    """Set of filenames present in extracted_texts (cross-run deduplication)."""
    conn = _connect(db_path)
    rows = conn.execute("SELECT filename FROM extracted_texts").fetchall()
    conn.close()
    return {r[0] for r in rows}


def delete_extracted_text(record_id: int, db_path=None):
    conn = _connect(db_path)
    conn.execute("DELETE FROM extracted_texts WHERE id=?", (record_id,))
    conn.commit()
    conn.close()

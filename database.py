
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
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            path           TEXT PRIMARY KEY,
            status         TEXT,
            last_processed TIMESTAMP,
            error_message  TEXT
        );

    """)
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

def reset_db(db_path=None):
    p = db_path or _DEFAULT_DB
    if os.path.exists(p):
        os.remove(p)
    init_db(p)


    )
    conn.commit()
    conn.close()


def get_processed_files(db_path=DB_NAME):
    """Return a set of file paths that have been successfully COMPLETED."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT path FROM files WHERE status='COMPLETED'")
    rows = c.fetchall()
    conn.close()
    return {row[0] for row in rows}


def reset_db(db_path=DB_NAME):
    """Clear all data from the database."""
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path)

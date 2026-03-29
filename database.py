import sqlite3
import datetime
import os

DB_NAME = "state.db"


def init_db(db_path=DB_NAME):
    """Initialize the SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            status TEXT,
            last_processed TIMESTAMP,
            error_message TEXT
        )
    """)
    conn.commit()
    conn.close()


def mark_started(file_path, db_path=DB_NAME):
    """Mark a file as PROCESSING."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = datetime.datetime.now()
    c.execute(
        """
        INSERT INTO files (path, status, last_processed, error_message)
        VALUES (?, 'PROCESSING', ?, NULL)
        ON CONFLICT(path) DO UPDATE SET
            status='PROCESSING',
            last_processed=?,
            error_message=NULL
    """,
        (file_path, now, now),
    )
    conn.commit()
    conn.close()


def mark_completed(file_path, db_path=DB_NAME):
    """Mark a file as COMPLETED."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = datetime.datetime.now()
    c.execute(
        """
        UPDATE files
        SET status='COMPLETED', last_processed=?
        WHERE path=?
    """,
        (now, file_path),
    )
    conn.commit()
    conn.close()


def mark_failed(file_path, error_message, db_path=DB_NAME):
    """Mark a file as FAILED with an error message."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = datetime.datetime.now()
    c.execute(
        """
        UPDATE files
        SET status='FAILED', last_processed=?, error_message=?
        WHERE path=?
    """,
        (now, error_message, file_path),
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

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
    c.execute("""
        CREATE TABLE IF NOT EXISTS FileLog (
            id INTEGER PRIMARY KEY,
            batch_id TEXT,
            file_name TEXT UNIQUE,
            method TEXT,
            status TEXT,
            size INTEGER,
            addedAt TIMESTAMP,
            deletedAt TIMESTAMP,
            error TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ActivityLog (
            id INTEGER PRIMARY KEY,
            timestamp TIMESTAMP,
            message TEXT,
            type TEXT
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

def insert_file_log(batch_id, file_name, method, status, size, error ,db_path=DB_NAME):
    """Save a processed file in SQLite"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = datetime.datetime.now()
    c.execute(
        """
        INSERT INTO FileLog (batch_id, file_name, method, status, size, error, addedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_name) DO UPDATE SET
            error=NULL
    """,
        (batch_id, file_name, method, status, size, error, now),
    )
    conn.commit()
    conn.close()

def insert_activity_log(log ,db_path=DB_NAME):
    """Save a processed file in SQLite"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    data = [(f.id ,f.timestamp, f.message, f.type) for f in log]
    c.executemany("INSERT INTO FileLog (id, timestamp, message, type) VALUES (?, ?, ?, ?)", data)
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

def get_logged_files(db_path=DB_NAME):
    """Return all file that have been logged."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM FileLog")
    rows = c.fetchall()
    result = [dict(row) for row in rows]
    conn.close()
    return result

def get_logs(db_path=DB_NAME):
    """Return all log of frontend."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM ActivityLog")
    rows = c.fetchall()
    result = [dict(row) for row in rows]
    conn.close()
    return result

def reset_db(db_path=DB_NAME):
    """Clear all data from the database."""
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path)

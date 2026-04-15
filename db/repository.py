"""DatabaseRepository single, thread-safe access point for all SQLite operations.

Schema versioning
-----------------
All DDL changes are gated behind 'PRAGMA user_version'.  Each integer version
maps to a list of idempotent SQL statements in '_MIGRATIONS'.  On startup
'init_schema' reads the current version and runs only the missing migrations.

Legacy DBs (created before versioning was introduced, 'user_version = 0')
are detected via '_detect_legacy_version' so existing data is never lost.

Current schema version: 5

Migration history
-----------------
1 Base schema: 'files', 'extracted_texts'
2 'content_hash' columns + unique index
3 'runs' table + 'run_id' FK on 'extracted_texts'
4 'direct_files' / 'ocr_files' columns on 'runs'
5 'fts_pages' FTS5 virtual table for per-page full-text search
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA_VERSION = 7

# Each value is a list of SQL statements for that migration step
# Statements are executed individually so we can catch "already exists" errors
_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS files (
            path           TEXT PRIMARY KEY,
            status         TEXT,
            content_hash   TEXT,
            last_processed TIMESTAMP,
            error_message  TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS extracted_texts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT,
            source_path  TEXT    NOT NULL,
            filename     TEXT    NOT NULL,
            rel_path     TEXT    NOT NULL,
            txt_path     TEXT    NOT NULL,
            method       TEXT,
            char_count   INTEGER DEFAULT 0,
            page_count   INTEGER DEFAULT 0,
            content_hash TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_et_filename ON extracted_texts(filename)",
        "CREATE INDEX  IF NOT EXISTS idx_et_processed ON extracted_texts(processed_at DESC)",
    ],
    2: [
        "ALTER TABLE files ADD COLUMN content_hash TEXT",
        "ALTER TABLE extracted_texts ADD COLUMN content_hash TEXT",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_et_hash
            ON extracted_texts(content_hash)
            WHERE content_hash IS NOT NULL
        """,
    ],
    3: [
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id       TEXT PRIMARY KEY,
            started_at   TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            status       TEXT NOT NULL DEFAULT 'running',
            total_files  INTEGER DEFAULT 0,
            done_files   INTEGER DEFAULT 0,
            failed_files INTEGER DEFAULT 0,
            input_dir    TEXT,
            settings     TEXT
        )
        """,
        "ALTER TABLE extracted_texts ADD COLUMN run_id TEXT REFERENCES runs(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_et_run ON extracted_texts(run_id)",
    ],
    4: [
        "ALTER TABLE runs ADD COLUMN direct_files INTEGER DEFAULT 0",
        "ALTER TABLE runs ADD COLUMN ocr_files    INTEGER DEFAULT 0",
    ],
    5: [
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_pages USING fts5(
            doc_id   UNINDEXED,
            filename UNINDEXED,
            rel_path UNINDEXED,
            page_no  UNINDEXED,
            content,
            tokenize='unicode61 remove_diacritics 1'
        )
        """,
    ],
    6: [
        "ALTER TABLE extracted_texts ADD COLUMN confidence REAL DEFAULT NULL",
        "ALTER TABLE extracted_texts ADD COLUMN flags TEXT",
    ],
    7: [
        "ALTER TABLE extracted_texts ADD COLUMN txt_hash TEXT",
    ],
}


def _detect_legacy_version(conn: sqlite3.Connection) -> int:
    """Infer the effective schema version for DBs created before user_version was used"""
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        )
    }
    if "files" not in tables:
        return 0

    files_cols = {row[1] for row in conn.execute("PRAGMA table_info(files)")}

    if "fts_pages" in tables:
        return 5  # Already at v5 (shouldn't reach here but be safe)

    if "runs" in tables:
        runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "ocr_files" in runs_cols:
            return 4
        return 3

    if "content_hash" in files_cols:
        return 2

    return 1


def _run_sql(conn: sqlite3.Connection, sql: str) -> None:
    """Execute a single SQL statement, ignoring harmless 'already exists' errors"""
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column name" in msg or "already exists" in msg:
            logger.debug(
                "Migration SQL skipped (already applied): %s", sql.strip()[:60]
            )
        else:
            raise


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class DatabaseRepository:
    """Thread-safe SQLite wrapper with versioned schema migrations.

    Args:
        db_path: Absolute path to the SQLite database file
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helper methods
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().isoformat()

    # ------------------------------------------------------------------
    # Schema management (versioned migrations)
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Run any pending schema migrations; idempotent on every startup."""
        with self._lock:
            conn = self._connect()
            try:
                current = conn.execute("PRAGMA user_version").fetchone()[0]

                if current == 0:
                    # Fresh DB _or_ legacy DB (created before user_version was used)
                    current = _detect_legacy_version(conn)
                    if current > 0:
                        logger.info(
                            "Legacy DB detected at inferred schema v%d; "
                            "will apply only missing migrations",
                            current,
                        )

                for version in range(current + 1, _SCHEMA_VERSION + 1):
                    logger.info("Applying schema migration v%d …", version)
                    for sql in _MIGRATIONS[version]:
                        _run_sql(conn, sql)
                    # Write version after all statements in this step succeed
                    conn.execute(f"PRAGMA user_version = {version}")
                    conn.commit()
                    logger.info("Schema migrated to v%d", version)

                if current == _SCHEMA_VERSION:
                    logger.debug(
                        "Schema already at v%d — no migrations needed", current
                    )

            finally:
                conn.close()

    def reset(self) -> None:
        """Delete all rows — used by the CLI 'reset' command and tests."""
        with self._lock:
            conn = self._connect()
            try:
                # FTS5 content table must be cleared before the main table
                conn.execute("DELETE FROM fts_pages")
                conn.execute("DELETE FROM extracted_texts")
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM runs")
                conn.commit()
                logger.info("Database reset at %s", self._db_path)
            except sqlite3.OperationalError:
                # fts_pages may not exist yet in very old DBs
                conn.executescript(
                    """
                    DELETE FROM extracted_texts;
                    DELETE FROM files;
                    DELETE FROM runs;
                """
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # runs table
    # ------------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        total_files: int,
        input_dir: str = "",
        settings: str = "",
    ) -> None:
        """Insert a new run record with status='running'"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO runs
                        (run_id, started_at, status, total_files, input_dir, settings)
                    VALUES (?, ?, 'running', ?, ?, ?)
                    """,
                    (run_id, self._now(), total_files, input_dir, settings),
                )
                conn.commit()
            finally:
                conn.close()

    def update_run(
        self,
        run_id: str,
        status: str,
        done_files: int,
        failed_files: int,
        direct_files: int = 0,
        ocr_files: int = 0,
    ) -> None:
        """Mark a run as completed/cancelled and record final counts"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE runs
                    SET completed_at = ?, status = ?,
                        done_files = ?, failed_files = ?,
                        direct_files = ?, ocr_files = ?
                    WHERE run_id = ?
                    """,
                    (
                        self._now(),
                        status,
                        done_files,
                        failed_files,
                        direct_files,
                        ocr_files,
                        run_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_runs(self, page: int = 1, size: int = 20) -> tuple[int, list[dict]]:
        """Paginated list of runs (newest first)"""
        offset = (max(page, 1) - 1) * size
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                rows = conn.execute(
                    """
                    SELECT run_id, started_at, completed_at, status,
                           total_files, done_files, failed_files,
                           direct_files, ocr_files, input_dir,
                           ROUND(
                               (JULIANDAY(completed_at) - JULIANDAY(started_at)) * 86400
                           ) AS elapsed_seconds
                    FROM runs
                    ORDER BY started_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (size, offset),
                ).fetchall()
                return total, [dict(r) for r in rows]
            finally:
                conn.close()

    def get_run(self, run_id: str) -> dict | None:
        """Single run record with elapsed_seconds"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT run_id, started_at, completed_at, status,
                           total_files, done_files, failed_files,
                           direct_files, ocr_files, input_dir,
                           ROUND(
                               (JULIANDAY(completed_at) - JULIANDAY(started_at)) * 86400
                           ) AS elapsed_seconds
                    FROM runs WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_run_files(
        self, run_id: str, page: int = 1, size: int = 50
    ) -> tuple[int, list[dict]]:
        """Paginated list of extracted_texts rows belonging to a run"""
        offset = (max(page, 1) - 1) * size
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM extracted_texts WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
                rows = conn.execute(
                    """
                    SELECT id, filename, rel_path, method, char_count,
                           page_count, content_hash, processed_at, confidence, flags
                    FROM extracted_texts
                    WHERE run_id = ?
                    ORDER BY processed_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (run_id, size, offset),
                ).fetchall()
                return total, [dict(r) for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # files table for pipeline state tracking
    # ------------------------------------------------------------------

    def mark_started(self, file_path: str, content_hash: str = "") -> None:
        now = self._now()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO files (path, status, content_hash, last_processed, error_message)
                    VALUES (?, 'PROCESSING', ?, ?, NULL)
                    ON CONFLICT(path) DO UPDATE SET
                        status         = 'PROCESSING',
                        content_hash   = excluded.content_hash,
                        last_processed = excluded.last_processed,
                        error_message  = NULL
                    """,
                    (file_path, content_hash or None, now),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_started_batch(self, file_paths: list[str]) -> None:
        """Single transaction bulk upsert"""
        now = self._now()
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO files (path, status, last_processed, error_message)
                    VALUES (?, 'PROCESSING', ?, NULL)
                    ON CONFLICT(path) DO UPDATE SET
                        status         = 'PROCESSING',
                        last_processed = excluded.last_processed,
                        error_message  = NULL
                    """,
                    [(fp, now) for fp in file_paths],
                )
                conn.commit()
            finally:
                conn.close()

    def mark_completed(self, file_path: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE files SET status='COMPLETED', last_processed=? WHERE path=?",
                    (self._now(), file_path),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_failed(self, file_path: str, error_message: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE files
                    SET status='FAILED', last_processed=?, error_message=?
                    WHERE path=?
                    """,
                    (self._now(), error_message, file_path),
                )
                conn.commit()
            finally:
                conn.close()

    def get_processed_hashes(self) -> set[str]:
        """Return content hashes of all already extracted files (O(1) dedup)"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT content_hash FROM extracted_texts WHERE content_hash IS NOT NULL"
                ).fetchall()
                return {r[0] for r in rows}
            finally:
                conn.close()

    def get_processed_filenames(self) -> set[str]:
        """Fallback filenames for legacy records with no hash yet"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT filename FROM extracted_texts WHERE content_hash IS NULL"
                ).fetchall()
                return {r[0] for r in rows}
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # extracted_texts table
    # ------------------------------------------------------------------

    def save_extracted_text(
        self,
        *,
        source_path: str,
        filename: str,
        rel_path: str,
        txt_path: str,
        method: str,
        char_count: int,
        page_count: int,
        content_hash: str = "",
        run_id: str | None = None,
        confidence: float | None = None,
        flags: list[str] | None = None,
        txt_hash: str | None = None,
    ) -> int:
        """Upsert an extraction record. Returns the record ID (new or existing)"""
        now = self._now()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO extracted_texts
                        (run_id, source_path, filename, rel_path, txt_path, method,
                         char_count, page_count, content_hash, processed_at, confidence, flags, txt_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(filename) DO UPDATE SET
                        run_id       = excluded.run_id,
                        source_path  = excluded.source_path,
                        rel_path     = excluded.rel_path,
                        txt_path     = excluded.txt_path,
                        method       = excluded.method,
                        char_count   = excluded.char_count,
                        page_count   = excluded.page_count,
                        content_hash = excluded.content_hash,
                        processed_at = excluded.processed_at,
                        confidence   = excluded.confidence,
                        flags        = excluded.flags,
                        txt_hash     = excluded.txt_hash
                    """,
                    (
                        run_id,
                        source_path,
                        filename,
                        rel_path,
                        txt_path,
                        method,
                        char_count,
                        page_count,
                        content_hash or None,
                        now,
                        confidence,
                        ",".join(flags) if flags else None,
                        txt_hash or None,
                    ),
                )
                conn.commit()
                # lastrowid = 0 for ON CONFLICT DO UPDATE; always fetch via filename
                row = conn.execute(
                    "SELECT id FROM extracted_texts WHERE filename = ?", (filename,)
                ).fetchone()
                return row[0] if row else 0
            finally:
                conn.close()

    def get_extracted_texts(
        self, page: int = 1, size: int = 50
    ) -> tuple[int, list[dict[str, Any]]]:
        """Return '(total_count, page_items)' ordered by most-recently processed"""
        offset = (max(page, 1) - 1) * size
        with self._lock:
            conn = self._connect()
            try:
                total: int = conn.execute(
                    "SELECT COUNT(*) FROM extracted_texts"
                ).fetchone()[0]
                rows = conn.execute(
                    """
                    SELECT e.id, e.run_id, e.source_path, e.filename, e.rel_path,
                           e.txt_path, e.method, e.char_count, e.page_count,
                           e.content_hash, e.processed_at, e.confidence, e.flags,
                           r.started_at AS run_started_at
                    FROM extracted_texts e
                    LEFT JOIN runs r ON r.run_id = e.run_id
                    ORDER BY e.processed_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (size, offset),
                ).fetchall()
                return total, [dict(r) for r in rows]
            finally:
                conn.close()

    def get_by_id(self, record_id: int) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM extracted_texts WHERE id=?", (record_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_by_filename(self, filename: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT id, filename, rel_path, method,
                           char_count, page_count, content_hash, processed_at,
                           confidence, flags
                    FROM extracted_texts WHERE filename=?
                    """,
                    (filename,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_by_hashes(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        """Bulk fetch by content_hash.  Returns '{hash: record_dict}'"""
        if not hashes:
            return {}
        placeholders = ",".join("?" * len(hashes))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"""
                    SELECT id, filename, rel_path, method,
                           char_count, page_count, content_hash, processed_at,
                           confidence, flags
                    FROM extracted_texts
                    WHERE content_hash IN ({placeholders})
                    """,
                    hashes,
                ).fetchall()
                return {r["content_hash"]: dict(r) for r in rows}
            finally:
                conn.close()

    def delete_by_id(self, record_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM extracted_texts WHERE id=?", (record_id,))
                conn.commit()
            finally:
                conn.close()

    def get_total_count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[
                    0
                ]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # FTS5 per-page full-text search
    # ------------------------------------------------------------------

    def fts_index_pages(
        self,
        doc_id: int,
        filename: str,
        rel_path: str,
        page_texts: list[str],
    ) -> None:
        """Insert (or replace) all pages for one document into the FTS index.

        Uses a single 'executemany' call so 200-page PDFs cost one DB
        round-trip regardless of page count

        Args:
            doc_id:     'extracted_texts.id' used as the FTS rowid group key
            filename:   Basename of the PDF (stored unindexed for display)
            rel_path:   Relative path for linking back to the result
            page_texts: List of per-page text strings (index = page number - 1)
        """
        if not page_texts:
            return
        # Remove any existing pages for this doc (re-extraction / force mode)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM fts_pages WHERE doc_id=?", (doc_id,))
                conn.executemany(
                    """
                    INSERT INTO fts_pages (doc_id, filename, rel_path, page_no, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (doc_id, filename, rel_path, i + 1, text)
                        for i, text in enumerate(page_texts)
                        if text.strip()  # skip blank pages
                    ],
                )
                conn.commit()
            finally:
                conn.close()

    def fts_search(
        self,
        query: str,
        page: int = 1,
        size: int = 20,
    ) -> tuple[int, list[dict[str, Any]]]:
        """BM25-ranked per-page FTS search, grouped to one result per document.

        Returns '(total_unique_docs, page_items)' where each item is the
        best-matching page for that document

        Args:
            query: FTS5 MATCH expression (simple terms or quoted phrases)
            page:  1-indexed page number
            size:  Results per page

        Returns:
            '(total, rows)' — rows contain doc_id, filename, rel_path,
            page_no (best match), snippet (HTML with '<mark>' tags), rank
        """
        # Sanitise query to wrap in quotes if it looks like plain text
        safe_q = query.strip()
        if not safe_q:
            return 0, []

        offset = (max(page, 1) - 1) * size

        with self._lock:
            conn = self._connect()
            try:
                # Fetch all page matches (capped at 5000 to bound memory)
                rows = conn.execute(
                    """
                    SELECT doc_id, filename, rel_path, page_no,
                           snippet(fts_pages, 4, '<mark>', '</mark>', ' … ', 24) AS snippet,
                           rank
                    FROM fts_pages
                    WHERE fts_pages MATCH ?
                    ORDER BY rank
                    LIMIT 5000
                    """,
                    (safe_q,),
                ).fetchall()

                # Group by doc_id, keeping the best ranked page per document
                seen: dict[int, dict] = {}
                for r in rows:
                    did = r["doc_id"]
                    if did not in seen:
                        seen[did] = dict(r)

                all_matches = list(seen.values())
                total = len(all_matches)
                return total, all_matches[offset : offset + size]

            except sqlite3.OperationalError as exc:
                # Bad FTS query syntax return empty instead of 500
                logger.warning("FTS search error for %r: %s", query, exc)
                return 0, []
            finally:
                conn.close()

    def fts_delete_doc(self, doc_id: int) -> None:
        """Remove all FTS page rows for a document (call before deleting the DB record)"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM fts_pages WHERE doc_id=?", (doc_id,))
                conn.commit()
            finally:
                conn.close()

    def fts_is_empty(self) -> bool:
        """Return True when the FTS index has no rows"""
        with self._lock:
            conn = self._connect()
            try:
                count = conn.execute("SELECT COUNT(*) FROM fts_pages").fetchone()[0]
                return count == 0
            except sqlite3.OperationalError:
                return True
            finally:
                conn.close()

    def fts_rebuild(self, progress_cb=None) -> int:
        """Rebuild the FTS index from all 'extracted_texts' records

        Reads each '.txt' file from disk, splits it into 'page_count' equal
        chunks (approximation when exact page texts are not stored), then
        re-indexes

        Args:
            progress_cb: Optional 'callable(done, total)' for progress reporting

        Returns:
            Number of documents re-indexed
        """
        import math
        from pathlib import Path

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM fts_pages")
                conn.commit()
            finally:
                conn.close()

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, filename, rel_path, txt_path, page_count FROM extracted_texts"
                ).fetchall()
            finally:
                conn.close()

        total = len(rows)
        done = 0

        for row in rows:
            doc_id = row["id"]
            txt_path = Path(row["txt_path"])
            page_count = max(1, row["page_count"] or 1)

            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                done += 1
                continue

            # Split into page_count equal chunks (approximation)
            chunk_size = math.ceil(len(text) / page_count)
            page_texts = [
                text[i : i + chunk_size] for i in range(0, len(text), chunk_size)
            ]

            self.fts_index_pages(
                doc_id=doc_id,
                filename=row["filename"],
                rel_path=row["rel_path"],
                page_texts=page_texts,
            )
            done += 1
            if progress_cb:
                progress_cb(done, total)

        return done

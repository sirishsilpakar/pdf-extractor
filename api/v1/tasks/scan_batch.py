"""Background task: scan a folder, hash PDFs, persist to batch_files

Flow
----
1.  Walk 'resolved_path' recursively for .pdf files
2.  Hash each file with 'compute_file_hash' (first+last 32 KB)
3.  Write rows to 'batch_files' in BATCH_SIZE chunks (avoids long DB locks)
4.  Cross reference 'extracted_texts.content_hash' to compute already_processed_count
5.  Update 'batches' row: scan_status=done, pdf_count, already_processed_count

Runs inside 'asyncio.get_event_loop().run_in_executor(None, ...)' does not
block the async event loop during filesystem I/O
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from api import sse as _sse
from api.v1.enums import ScanStatus
from db.repository import DatabaseRepository
from services.hasher import compute_file_hash

BATCH_SIZE: int = 500  # rows written per DB transaction

logger = logging.getLogger(__name__)


def _do_scan(
    batch_id: str,
    resolved_path: str,
    is_folder: bool,
    db: DatabaseRepository,
) -> None:
    """Synchronous scan executed inside a ThreadPoolExecutor."""
    try:
        root = Path(resolved_path)
        pdfs = list(root.rglob("*.pdf")) if is_folder else [root]
        db.clear_batch_files(batch_id)

        all_hashes: list[str] = []
        pending_batch: list[dict] = []

        for pdf in pdfs:
            try:
                h = compute_file_hash(str(pdf))
            except Exception:
                h = ""

            all_hashes.append(h)
            pending_batch.append(
                {
                    "name": pdf.name,
                    "rel_path": (
                        pdf.relative_to(root).as_posix() if is_folder else pdf.name
                    ),
                    "size_bytes": pdf.stat().st_size if pdf.exists() else 0,
                    "content_hash": h or None,
                    "is_processed": 0,
                }
            )

            if len(pending_batch) >= BATCH_SIZE:
                db.insert_batch_files(batch_id, pending_batch)

                pending_batch.clear()

                _sse.broadcast_batch(
                    {
                        "type": "batch_scan_update",
                        "batch_id": batch_id,
                        "scan_status": ScanStatus.SCANNING.value,
                        "pdf_count": len(pdfs),
                        "files_scanned": len(all_hashes),
                        "already_processed_count": None,
                        "error_message": None,
                    }
                )

        if pending_batch:
            db.insert_batch_files(batch_id, pending_batch)

        valid_hashes = [h for h in all_hashes if h]
        known_hashes: set[str] = db.get_processed_hash_set(valid_hashes)
        already_processed = sum(1 for h in all_hashes if h in known_hashes)

        # Correctly update the individual file records as processed before finalizing scan
        if known_hashes:
            db.update_batch_files_processed(batch_id, list(known_hashes))

        db.update_batch_scan(
            batch_id,
            pdf_count=len(pdfs),
            already_processed_count=already_processed,
            status=ScanStatus.DONE,
        )
        _sse.broadcast_batch(
            {
                "type": "batch_scan_update",
                "batch_id": batch_id,
                "scan_status": ScanStatus.DONE.value,
                "pdf_count": len(pdfs),
                "files_scanned": len(pdfs),
                "already_processed_count": already_processed,
                "error_message": None,
            }
        )
        logger.info(
            "Batch scan complete: batch_id=%s  files=%d  already_processed=%d",
            batch_id,
            len(pdfs),
            already_processed,
        )

    except Exception as exc:
        logger.exception("Batch scan failed: batch_id=%s  error=%s", batch_id, exc)
        db.update_batch_scan(
            batch_id,
            pdf_count=0,
            already_processed_count=0,
            status=ScanStatus.ERROR,
            error=str(exc),
        )
        _sse.broadcast_batch(
            {
                "type": "batch_scan_update",
                "batch_id": batch_id,
                "scan_status": ScanStatus.ERROR.value,
                "pdf_count": 0,
                "files_scanned": 0,
                "already_processed_count": None,
                "error_message": str(exc),
            }
        )


async def scan_batch_task(
    batch_id: str,
    resolved_path: str,
    is_folder: bool,
    db: DatabaseRepository,
) -> None:
    """Async entry point, runs the blocking scan in the default thread-pool executor."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_scan, batch_id, resolved_path, is_folder, db)

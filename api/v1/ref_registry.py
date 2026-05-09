"""In-memory registry mapping ref_ids to server-side filesystem paths.

File Reference Mode
-------------------
When the server is running on the same machine as the PDFs (desktop / local
deployment), there is no need to upload files.  Instead, the client calls
'POST /api/v1/upload/reference' with a local path, receives a 'ref_id',
then passes that 'ref_id' in 'StartJobRequest.file_ids' exactly like a
regular upload 'file_id'.

The registry is intentionally not persisted to disk.  Entries are valid
only for the lifetime of the server process.  A server restart requires the
client to re-register paths (which is a lightweight operation).

Thread safety
-------------
All mutations go through a `threading.Lock` so the registry is safe to use
from concurrent FastAPI async route handlers and background threads.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path


class _RefRegistry:
    """Thread safe in-memory store of ref_id to absolute resolved path."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, path: str) -> str:
        """Resolve path to an absolute path, store it, and return a new ref_id."""
        resolved = str(Path(path).resolve())
        ref_id = uuid.uuid4().hex
        with self._lock:
            self._store[ref_id] = resolved
        return ref_id

    def lookup(self, ref_id: str) -> str | None:
        """Return the stored absolute path for ref_id, or None if unknown."""
        with self._lock:
            return self._store.get(ref_id)

    def remove(self, ref_id: str) -> None:
        """Remove a registration (optional cleanup after a job starts)."""
        with self._lock:
            self._store.pop(ref_id, None)


# Process level singleton to be imported directly by upload.py and jobs.py
_registry = _RefRegistry()


def register(path: str) -> str:
    """Register a server-side path and return an opaque 'ref_id'."""
    return _registry.register(path)


def lookup(ref_id: str) -> str | None:
    """Return the absolute path for 'ref_id', or None if not registered."""
    return _registry.lookup(ref_id)


def remove(ref_id: str) -> None:
    """Remove a registration after the job has started (optional)."""
    _registry.remove(ref_id)

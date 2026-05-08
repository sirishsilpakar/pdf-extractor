from __future__ import annotations

from enum import Enum


class ScanStatus(str, Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    DONE = "done"
    ERROR = "error"

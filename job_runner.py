import asyncio
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class Job:
    job_id: str
    input_directory: str
    output_dir: str
    options: dict                         # stores all the toggle flags
    status: str = "queued"               # queued | running | completed | failed
    submitted_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    event_buffer: list = field(default_factory=list)  # layer 5 — replay buffer

# ── Global state ────────────────────────────────────────────────────────────
_job_queue: asyncio.Queue = asyncio.Queue()          # pending jobs, FIFO
_jobs: dict[str, Job] = {}                           # job_id → Job registry
_consumer_started: bool = False
BUFFER_SIZE = 200                                    # max buffered events per job
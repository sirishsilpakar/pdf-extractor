"""Runtime resource detection for memory aware worker cap.

Used by 'run_pipeline' to avoid spawning more worker processes than the
available system RAM can support.

'psutil' is an optional dependency (listed under '[project.optional-dependencies] perf').
If it is not installed the function gracefully degrades to the CPU-based cap
without any error or warning about the missing library
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import psutil as psutil  # noqa: PLC0414  (re-export so patch() can find it)
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def safe_worker_count(cpu_workers: int, ram_per_worker_mb: int) -> int:
    """Return the safe number of parallel workers given available RAM.

    Formula:
        ram_cap = floor(available_ram_mb / ram_per_worker_mb)
        result  = max(1, min(cpu_workers, ram_cap))

    If 'psutil' is not installed the function returns 'cpu_workers'
    unchanged and logs a debug message.

    Args:
        cpu_workers:       Upper bound from CPU affinity / config setting.
        ram_per_worker_mb: Estimated peak RAM per worker process in MB.

    Returns:
        Effective worker count — always >= 1
    """
    if ram_per_worker_mb <= 0:
        return max(1, cpu_workers)

    if psutil is None:
        logger.debug(
            "psutil not available — RAM cap skipped, using %d CPU-based workers",
            cpu_workers,
        )
        return max(1, cpu_workers)

    available_mb = psutil.virtual_memory().available // (1024 * 1024)
    ram_cap = max(1, available_mb // ram_per_worker_mb)

    if ram_cap < cpu_workers:
        logger.warning(
            "RAM cap applied: %d MB available / %d MB per worker"
            " -> reducing workers %d → %d",
            available_mb,
            ram_per_worker_mb,
            cpu_workers,
            ram_cap,
        )
    else:
        logger.debug(
            "RAM check OK: %d MB available, %d MB per worker, %d workers",
            available_mb,
            ram_per_worker_mb,
            cpu_workers,
        )

    return min(cpu_workers, ram_cap)

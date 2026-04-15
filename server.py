"""Server entry point, a thin uvicorn launcher.

Imports ``create_app`` from ``api.app`` (all application configuration is present there)
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent))

from api.app import create_app  # noqa: E402

app = create_app()


def start() -> None:
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    start()

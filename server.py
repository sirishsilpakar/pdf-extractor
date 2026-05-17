"""Server entry point, a thin uvicorn launcher.

Imports ``create_app`` from ``api.app`` (all application configuration is present there)

CLI flags
---------
--port N      Listen on port N (default: SERVER_PORT env var, fallback 8080)
--no-ui       Skip mounting the static React UI assets (API-only / headless mode)
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path

import uvicorn

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PDF TextExtract API server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (overrides SERVER_PORT env var)",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Start without serving the static React UI (API-only / headless mode)",
    )
    return parser.parse_args()


def start() -> None:
    args = _parse_args()

    # CLI flags win over env vars, env vars win over built-in defaults
    if args.no_ui:
        os.environ["SERVE_UI"] = "false"

    # Import config after env mutations so values are picked up correctly
    from config import SERVE_UI, SERVER_PORT  # noqa: E402 — intentional late import

    port = args.port if args.port is not None else SERVER_PORT

    mode = "headless (API only)" if not SERVE_UI else "full (UI + API)"
    print(f"[server] Starting in {mode} mode on port {port}")

    from api.app import create_app  # noqa: E402

    application = create_app()
    uvicorn.run(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    # Needs to be called at the very start for PyInstaller frozen applications
    multiprocessing.freeze_support()
    start()

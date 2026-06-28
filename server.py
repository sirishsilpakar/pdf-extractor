"""Server entry point, a thin uvicorn launcher.

Imports ``create_app`` from ``api.app`` (all application configuration is present there)

CLI flags
---------
--port N      Listen on port N (default: SERVER_PORT env var, fallback 8080)
--ui          Serve the static React UI assets alongside the API
--mode MODE   Environment mode: 'dev' (incremental ports) or 'prod' (OS-allocated port)
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import socket
import sys
from pathlib import Path

import uvicorn

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).parent))


def find_free_port(start_port: int, max_attempts: int = 100) -> int:
    """Finds an available TCP port starting from start_port incrementally"""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"Could not find a free port in range {start_port} to {start_port + max_attempts}"
    )


def get_os_allocated_port() -> int:
    """Asks the OS to allocate a free ephemeral port by binding to port 0"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        "--ui",
        action="store_true",
        help="Serve the static React UI (headless/no-ui by default)",
    )
    parser.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default=None,
        help="Environment mode: 'dev' (incremental ports) or 'prod' (OS allocated port)",
    )
    return parser.parse_args()


def start() -> None:
    args = _parse_args()

    # CLI flags win over env vars, env vars win over built-in defaults
    # By default, we do not serve the UI unless the --ui flag is passed or SERVE_UI is explicitly set to true
    if args.ui:
        os.environ["SERVE_UI"] = "true"
    else:
        # Default to false unless overridden by the env var already
        if "SERVE_UI" not in os.environ:
            os.environ["SERVE_UI"] = "false"

    # Determine dev vs prod mode
    # If mode is not specified via CLI, detect it from sys.frozen or APP_ENV
    mode_cli = args.mode
    is_prod = False
    if mode_cli == "prod":
        is_prod = True
    elif mode_cli == "dev":
        is_prod = False
    else:
        is_prod = getattr(sys, "frozen", False) or os.getenv("APP_ENV") == "production"

    default_port_env = int(os.getenv("SERVER_PORT", "8080"))
    default_port = args.port if args.port is not None else default_port_env

    # Find the port to bind to
    if is_prod:
        # Production uses an OS allocated port (port 0)
        try:
            port = get_os_allocated_port()
            print(f"[server] Production mode: OS-allocated port {port}")
        except RuntimeError as e:
            print(f"[server] Error finding OS-allocated port: {e}")
            sys.exit(1)
    else:
        # Development scans ports incrementally starting from default_port
        try:
            port = find_free_port(default_port)
            if port != default_port:
                print(
                    f"[server] Port {default_port} in use. Incrementing to find free port..."
                )
            print(f"[server] Development mode: Bound to port {port}")
        except RuntimeError as e:
            print(f"[server] Error finding free port: {e}")
            sys.exit(1)

    # Write the final resolved port back to environment variable so config and lifespan pick it up
    os.environ["SERVER_PORT"] = str(port)

    # Import config after env mutations so values are picked up correctly
    from config import SERVE_UI, SERVER_PORT  # noqa: E402 - intentional late import

    mode = "full (UI + API)" if SERVE_UI else "headless (API only)"
    print(f"[server] Starting in {mode} mode on port {SERVER_PORT}")

    from api.app import create_app  # noqa: E402

    application = create_app()

    port_file_env = os.getenv("PORT_FILE_PATH")
    if port_file_env:
        port_file = Path(port_file_env)
        # Ensure parent directory exists (e.g. if writing to a custom app directory)
        port_file.parent.mkdir(parents=True, exist_ok=True)
    elif is_prod:
        # Production env uses user configuration folder to avoid permissions/signing issues
        app_dir = Path.home() / ".pdf-extractor"
        app_dir.mkdir(parents=True, exist_ok=True)
        port_file = app_dir / "port.json"
    else:
        # Development env uses local directory
        port_file = Path(__file__).parent / "port.json"

    try:
        # Write port information to port.json for client discovery
        with open(port_file, "w") as f:
            json.dump({"port": port, "pid": os.getpid()}, f)

        uvicorn.run(
            application,
            host="0.0.0.0",
            port=port,
            reload=False,
            log_level="info",
        )
    finally:
        try:
            if port_file.exists():
                port_file.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    # Needs to be called at the very start for PyInstaller frozen applications
    multiprocessing.freeze_support()
    start()

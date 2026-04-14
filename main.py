"""Backward compatibility module for the entry point.

The CLI is now ``pdf-extract`` (``cli.main:cli``).
This module delegates to it so existing scripts that call ``main.main()``
or ``python main.py`` continue to work
"""

from __future__ import annotations

import sys


def main() -> None:
    """Delegate to the click-based CLI (cli.main:cli)"""
    from cli.main import cli

    cli(standalone_mode=True)


if __name__ == "__main__":
    main()

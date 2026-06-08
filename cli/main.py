"""PDF TextExtract command-line interface.

Uses the same 'core.pipeline.run_pipeline' function as the API layer, so
the pipeline is fully usable without a running server

Commands
--------
  pdf-extract run     Run the extraction pipeline on a directory
  pdf-extract status  Show DB stats (count of extracted / failed files)
  pdf-extract reset   Wipe pipeline state from the database
  pdf-extract engines List registered OCR engines and availability

Entry point configured in pyproject.toml:
  [project.scripts]
  pdf-extract = "cli.main:cli"
"""

from __future__ import annotations

import os
import sys

import click

from config import DB_PATH, OMP_THREAD_LIMIT


@click.group()
@click.version_option(package_name="pdf-extractor")
def cli():
    """PDF TextExtract to extract text from large PDF collections"""


@cli.command("run")
@click.argument(
    "input_dir", type=click.Path(exists=True, file_okay=False, resolve_path=True)
)
@click.option(
    "--output-dir",
    "-o",
    default="extracted_files",
    show_default=True,
    help="Root directory for extracted .txt outputs",
)
@click.option(
    "--workers",
    "-w",
    default=None,
    type=int,
    help="Override number of parallel worker processes",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Reprocess files already present in the database",
)
@click.option(
    "--no-ocr", is_flag=True, help="Disable OCR and use direct text extraction only"
)
@click.option(
    "--fast",
    is_flag=True,
    help="Fast mode: DPI=150, shorter timeouts, higher image threshold",
)
@click.option(
    "--dpi", default=None, type=int, help="Override OCR DPI (e.g. 150, 200, 300)"
)
@click.option(
    "--db", "db_path", default=None, help=f"Override database path (default: {DB_PATH})"
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress page level progress lines (file start/done still shown)",
)
@click.option(
    "--remove-headers/--no-remove-headers",
    default=False,
    show_default=True,
    envvar="REMOVE_HEADERS",
    help="Strip repeating page headers from top margin of pages",
)
@click.option(
    "--remove-footers/--no-remove-footers",
    default=False,
    show_default=True,
    envvar="REMOVE_FOOTERS",
    help="Strip repeating page footers from bottom margin of pages",
)
@click.option(
    "--remove-page-numbers/--no-remove-page-numbers",
    default=False,
    show_default=True,
    envvar="REMOVE_PAGE_NUMBERS",
    help="Remove page number strings (e.g., 'Page 1', Roman numerals) from text",
)
@click.option(
    "--remove-numeric-values/--no-remove-numeric-values",
    default=False,
    show_default=True,
    envvar="REMOVE_NUMERIC_VALUES",
    help="Remove all numeric digits and decimal patterns from text",
)
@click.option(
    "--apply-text-formatting/--no-apply-text-formatting",
    default=True,
    show_default=True,
    envvar="APPLY_TEXT_FORMATTING",
    help="Apply text normalization (e.g. hyphenation removal, space collapse)",
)
@click.option(
    "--debug-visualize/--no-debug-visualize",
    default=False,
    show_default=True,
    envvar="DEBUG_POST_PROCESS_FILE",
    help="Generate an HTML visual layout debugger outlining bounding boxes (red=removed, green=kept)",
)
def run_cmd(
    input_dir,
    output_dir,
    workers,
    force,
    no_ocr,
    fast,
    dpi,
    db_path,
    quiet,
    remove_headers,
    remove_footers,
    remove_page_numbers,
    remove_numeric_values,
    apply_text_formatting,
    debug_visualize,
):
    """Run the extraction pipeline on INPUT_DIR"""
    import multiprocessing

    # Apply env overrides before any imports that read config
    if no_ocr:
        os.environ["DISABLE_OCR"] = "true"
    if fast:
        os.environ.setdefault("OCR_DPI", "150")
        os.environ.setdefault("TESSERACT_PAGE_TIMEOUT_SECONDS", "15")
        os.environ.setdefault("OCR_ON_IMAGE_AREA_THRESHOLD", "0.30")
        click.echo("Fast mode: DPI=150, timeout=15s, image_threshold=0.30")
    if dpi:
        os.environ["OCR_DPI"] = str(dpi)
    if workers:
        os.environ["WORKERS_OVERRIDE"] = str(max(1, workers))

    os.environ.setdefault("OMP_THREAD_LIMIT", str(OMP_THREAD_LIMIT))
    os.environ.setdefault("OMP_NUM_THREADS", str(OMP_THREAD_LIMIT))

    # Imports after env setup (config.py reads env at import time)
    import services  # noqa: F401 _> registers OCR engines
    from core.pipeline import run_pipeline
    from db.repository import DatabaseRepository

    db = DatabaseRepository(db_path or DB_PATH)
    db.init_schema()

    ocr_engine = None
    if not no_ocr:
        try:
            from services.ocr.registry import get_default_engine

            ocr_engine = get_default_engine()
            click.echo(f"OCR engine : {ocr_engine.name}")
        except RuntimeError as exc:
            click.secho(f"WARNING: {exc}", fg="yellow")
            click.echo("Continuing with direct extraction only.")

    # ------------------------------------------------------------------
    # Progress callback detailed per-page, per-PID output
    # ------------------------------------------------------------------

    def _progress(event: dict) -> None:
        etype = event.get("type", "")

        if etype == "log":
            msg = event.get("message", "")
            level = event.get("level", "info")
            if not msg:
                return

            if level == "error":
                click.secho(msg, fg="red", err=True)
            elif level == "warning":
                click.secho(msg, fg="yellow")
            elif level == "success" or level == "ok":
                click.secho(msg, fg="bright_green")
            else:
                click.echo(msg)

        elif etype == "file_started":
            pid = event.get("pid", "?")
            name = event.get("file", "")
            click.secho(
                f"\n▶  W{pid}  {name}",
                fg="cyan",
                bold=True,
            )

        elif etype == "page_done" and not quiet:
            pid = event.get("pid", "?")
            name = event.get("file", "")
            page = event.get("page", 0)
            total = event.get("total_pages", 0)
            method = event.get("method", "")
            secs = event.get("seconds", 0.0)
            pct = int(page / total * 100) if total else 0

            # Visual progress bar (20 chars wide)
            filled = int(pct / 5)
            bar = ("█" * filled) + ("░" * (20 - filled))

            tag_color = "magenta" if method == "ocr" else "blue"
            tag = click.style(f"[{method.upper():6}]", fg=tag_color)

            click.echo(
                f"   W{pid}  {tag}  pg {page:>3}/{total:<3}  "
                f"|{bar}| {pct:>3}%  {secs:.2f}s  {name}"
            )

        elif etype == "file_done":
            name = event.get("file", "")
            method = event.get("method", "")
            elapsed = event.get("elapsed", 0.0)
            chars = event.get("char_count", 0)
            done = event.get("done", 0)
            total = event.get("total", 0)
            pct = event.get("progress_pct", 0)

            method_tag = click.style(
                f"[{method.upper():6}]",
                fg="magenta" if method == "ocr" else "blue",
            )
            click.secho(
                f"✓  {method_tag}  {name}  "
                f"{elapsed:.1f}s  {chars:,} chars  "
                f"({done}/{total}  {pct}%)",
                fg="green",
            )

        elif etype in ("file_failed", "file_timeout"):
            name = event.get("file", "")
            msg = event.get("message", "")
            done = event.get("done", 0)
            total = event.get("total", 0)
            label = "TIMEOUT" if etype == "file_timeout" else "FAILED"
            click.secho(
                f"✗  [{label}]  {name}  {msg}  ({done}/{total})",
                fg="red",
            )

        elif etype == "ocr_engine_missing":
            click.secho(
                "✗  OCR engine binary not found - install Tesseract or use --no-ocr.",
                fg="red",
                bold=True,
                err=True,
            )

        elif etype == "done":
            done = event.get("done", 0)
            total = event.get("total", 0)
            direct = event.get("direct", 0)
            ocr = event.get("ocr", 0)
            failed = event.get("failed", 0)
            timeouts = event.get("timeouts", 0)

            click.echo()
            click.secho("─" * 56, fg="bright_black")
            click.secho("  Pipeline complete", fg="bright_green", bold=True)
            click.secho("─" * 56, fg="bright_black")
            click.echo(f"  Processed : {done}/{total}")
            click.echo(f"  Direct    : {direct}")
            click.echo(f"  OCR       : {ocr}")
            click.secho(
                f"  Failed    : {failed}", fg="red" if failed else "bright_black"
            )
            click.secho(
                f"  Timeouts  : {timeouts}", fg="yellow" if timeouts else "bright_black"
            )
            click.secho("─" * 56, fg="bright_black")

    settings = {
        "remove_header": remove_headers,
        "remove_footer": remove_footers,
        "remove_page_numbers": remove_page_numbers,
        "remove_numeric_values": remove_numeric_values,
        "apply_text_formatting": apply_text_formatting,
        "debug_visualize": debug_visualize,
    }

    try:
        run_pipeline(
            input_dir=input_dir,
            output_dir=output_dir,
            force=force,
            progress_callback=_progress,
            ocr_engine=ocr_engine,
            db=db,
            settings=settings,
        )
    except KeyboardInterrupt:
        click.secho("\nInterrupted.", fg="yellow")
        sys.exit(1)
    except Exception as exc:
        click.secho(f"Fatal error: {exc}", fg="red", err=True)
        sys.exit(2)


@cli.command("status")
@click.option("--db", "db_path", default=None, help="Database path override.")
def status_cmd(db_path):
    """Show extraction statistics from the database"""
    from db.repository import DatabaseRepository

    db = DatabaseRepository(db_path or DB_PATH)
    db.init_schema()

    total, first_page = db.get_extracted_texts(page=1, size=1)
    click.echo(f"Extracted records: {total}")


@cli.command("reset")
@click.option("--db", "db_path", default=None, help="Database path override")
@click.confirmation_option(prompt="This will wipe all pipeline state. Continue?")
def reset_cmd(db_path):
    """Delete all rows from the pipeline database"""
    from db.repository import DatabaseRepository

    db = DatabaseRepository(db_path or DB_PATH)
    db.reset()
    click.secho("Database reset.", fg="green")


@cli.command("engines")
def engines_cmd():
    """List registered OCR engines and their availability"""
    import services  # noqa: F401
    from services.ocr.registry import list_engines

    engines = list_engines()
    if not engines:
        click.echo("No OCR engines registered.")
        return
    for name, available in engines:
        icon = click.style("✓", fg="green") if available else click.style("✗", fg="red")
        click.echo(f"  {icon}  {name}")


if __name__ == "__main__":
    cli()

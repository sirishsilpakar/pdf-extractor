"""Unit tests verifying behavior of DocumentSanitizer configuration options in CLI mode"""

from unittest.mock import patch

from click.testing import CliRunner

from cli.main import cli


def test_cli_run_passes_settings_defaults():
    """Verify that the CLI passes the correct default settings (all False except formatting)"""
    runner = CliRunner()
    with (
        patch("db.repository.DatabaseRepository"),
        patch("core.pipeline.run_pipeline") as mock_run,
    ):

        result = runner.invoke(cli, ["run", "tests/test_input", "--no-ocr"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        called_kwargs = mock_run.call_args[1]
        assert called_kwargs["settings"] == {
            "remove_header": False,
            "remove_footer": False,
            "remove_page_numbers": False,
            "remove_numeric_values": False,
            "apply_text_formatting": True,
            "debug_visualize": False,
        }


def test_cli_run_passes_settings_overrides():
    """Verify that command-line options override the defaults correctly"""
    runner = CliRunner()
    with (
        patch("db.repository.DatabaseRepository"),
        patch("core.pipeline.run_pipeline") as mock_run,
    ):

        result = runner.invoke(
            cli,
            [
                "run",
                "tests/test_input",
                "--no-ocr",
                "--remove-headers",
                "--remove-footers",
                "--remove-page-numbers",
                "--remove-numeric-values",
                "--no-apply-text-formatting",
                "--debug-visualize",
            ],
        )

        assert result.exit_code == 0
        mock_run.assert_called_once()
        called_kwargs = mock_run.call_args[1]
        assert called_kwargs["settings"] == {
            "remove_header": True,
            "remove_footer": True,
            "remove_page_numbers": True,
            "remove_numeric_values": True,
            "apply_text_formatting": False,
            "debug_visualize": True,
        }


def test_cli_run_settings_envvar():
    """Verify that options fallback to environment variables correctly"""
    runner = CliRunner()
    with (
        patch("db.repository.DatabaseRepository"),
        patch("core.pipeline.run_pipeline") as mock_run,
    ):

        result = runner.invoke(
            cli,
            ["run", "tests/test_input", "--no-ocr"],
            env={
                "REMOVE_HEADERS": "true",
                "REMOVE_FOOTERS": "true",
                "REMOVE_PAGE_NUMBERS": "true",
                "REMOVE_NUMERIC_VALUES": "true",
                "APPLY_TEXT_FORMATTING": "false",
                "DEBUG_POST_PROCESS_FILE": "true",
            },
        )

        assert result.exit_code == 0
        mock_run.assert_called_once()
        called_kwargs = mock_run.call_args[1]
        assert called_kwargs["settings"] == {
            "remove_header": True,
            "remove_footer": True,
            "remove_page_numbers": True,
            "remove_numeric_values": True,
            "apply_text_formatting": False,
            "debug_visualize": True,
        }

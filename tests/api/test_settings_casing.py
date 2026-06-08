"""Unit tests for settings camelCase to snake_case normalization"""

from api.routes import StartRequest
from api.v1.schemas import StartJobRequest


def test_start_job_request_camel_case():
    """Verify camelCase keys are converted to snake_case in StartJobRequest"""
    payload = {
        "file_ids": ["file_1"],
        "settings": {
            "removeHeader": False,
            "removeFooter": True,
            "removePageNumbers": True,
            "removeNumericValues": False,
            "applyTextFormatting": True,
            "debugVisualize": False,
        },
    }
    req = StartJobRequest(**payload)
    assert req.settings is not None
    assert req.settings.get("remove_header") is False
    assert req.settings.get("remove_footer") is True
    assert req.settings.get("remove_page_numbers") is True
    assert req.settings.get("remove_numeric_values") is False
    assert req.settings.get("apply_text_formatting") is True
    assert req.settings.get("debug_visualize") is False
    assert "removeHeader" not in req.settings
    assert "removeFooter" not in req.settings


def test_start_job_request_mixed_case():
    """Verify mixed camelCase and snake_case keys are converted in StartJobRequest"""
    payload = {
        "file_ids": ["file_1"],
        "settings": {
            "removeHeader": False,
            "remove_footer": True,
            "remove_page_numbers": True,
        },
    }
    req = StartJobRequest(**payload)
    assert req.settings is not None
    assert req.settings.get("remove_header") is False
    assert req.settings.get("remove_footer") is True
    assert req.settings.get("remove_page_numbers") is True
    assert "removeHeader" not in req.settings


def test_start_job_request_none_settings():
    """Verify None settings are handled gracefully in StartJobRequest"""
    payload = {
        "file_ids": ["file_1"],
        "settings": None,
    }
    req = StartJobRequest(**payload)
    assert req.settings is None


def test_start_request_camel_case():
    """Verify camelCase keys are converted to snake_case in legacy StartRequest"""
    payload = {
        "file_ids": ["file_1"],
        "settings": {
            "removeHeader": False,
            "removeFooter": True,
            "removePageNumbers": True,
            "removeNumericValues": False,
            "applyTextFormatting": True,
            "debugVisualize": False,
        },
    }
    req = StartRequest(**payload)
    assert req.settings is not None
    assert req.settings.get("remove_header") is False
    assert req.settings.get("remove_footer") is True
    assert req.settings.get("remove_page_numbers") is True
    assert req.settings.get("remove_numeric_values") is False
    assert req.settings.get("apply_text_formatting") is True
    assert req.settings.get("debug_visualize") is False
    assert "removeHeader" not in req.settings
    assert "removeFooter" not in req.settings

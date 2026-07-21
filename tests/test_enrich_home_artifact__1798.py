"""Tests for issue #1798: Log swallowed exceptions in _enrich_home_artifact

Verifies that exceptions in _enrich_home_artifact helper are logged but still caught
(graceful fallback with observability).
"""
import logging
from unittest.mock import patch, MagicMock

import pytest

from apps.dashboard.routers.brief import _enrich_home_artifact


@pytest.fixture
def caplog_fixture(caplog):
    """Fixture to capture log output."""
    with caplog.at_level(logging.WARNING):
        yield caplog


def test_enrich_home_artifact__load_projects_exception_logged(caplog_fixture):
    """AC: load_projects() exception is logged with warning level, not silent."""
    artifact = {
        "available": True,
        "brief": {
            "projects": [
                {"project": "test-proj"}
            ]
        },
        "date": "2026-07-22"
    }

    # Mock load_projects to raise an exception
    with patch("apps.dashboard.routers.brief._projects_module.load_projects") as mock_load:
        mock_load.side_effect = RuntimeError("Database connection failed")

        # Call should not raise, but should log the exception
        _enrich_home_artifact(artifact)

        # Verify warning was logged
        assert any(
            "load_projects failed" in record.message
            for record in caplog_fixture.records
            if record.levelname == "WARNING"
        ), f"Expected 'load_projects failed' in warnings, got: {[r.message for r in caplog_fixture.records]}"


def test_enrich_home_artifact__get_summary_exception_logged(caplog_fixture):
    """AC: get_or_create_project_summary() exception is logged with warning level, not silent."""
    artifact = {
        "available": True,
        "brief": {
            "projects": [
                {"project": "test-proj"}
            ]
        },
        "date": "2026-07-22"
    }

    # Mock load_projects to succeed, but get_or_create_project_summary to fail
    mock_projects = [{"repo": "org/test-proj", "name": "Test Project"}]

    with patch("apps.dashboard.routers.brief._projects_module.load_projects") as mock_load, \
         patch("apps.dashboard.routers.brief.brief_summary.get_or_create_project_summary") as mock_summary, \
         patch("apps.dashboard.routers.brief.config.brief_disabled") as mock_disabled:

        mock_load.return_value = mock_projects
        mock_summary.side_effect = RuntimeError("Service unavailable")
        mock_disabled.return_value = False

        # Call should not raise, but should log the exception
        _enrich_home_artifact(artifact)

        # Verify warning was logged for summary failure
        assert any(
            "get_or_create_project_summary failed" in record.message
            for record in caplog_fixture.records
            if record.levelname == "WARNING"
        ), f"Expected 'get_or_create_project_summary failed' in warnings, got: {[r.message for r in caplog_fixture.records]}"


def test_enrich_home_artifact__graceful_fallback_on_load_projects_error():
    """AC: Exception in load_projects is caught and gracefully handled (no propagation)."""
    artifact = {
        "available": True,
        "brief": {
            "projects": [
                {"project": "test-proj"}
            ]
        },
        "date": "2026-07-22"
    }

    with patch("apps.dashboard.routers.brief._projects_module.load_projects") as mock_load:
        mock_load.side_effect = RuntimeError("Database connection failed")

        # Should not raise an exception
        try:
            _enrich_home_artifact(artifact)
            exception_raised = False
        except Exception:
            exception_raised = True

        assert not exception_raised, "Exception should be caught and not propagated"
        # Verify the projects list was handled gracefully (empty dict fallback)
        assert artifact["brief"]["projects"][0].get("name") == "test-proj"  # slug becomes name


def test_enrich_home_artifact__graceful_fallback_on_summary_error():
    """AC: Exception in get_or_create_project_summary is caught and gracefully handled."""
    artifact = {
        "available": True,
        "brief": {
            "projects": [
                {"project": "test-proj"}
            ]
        },
        "date": "2026-07-22"
    }

    mock_projects = [{"repo": "org/test-proj", "name": "Test Project"}]

    with patch("apps.dashboard.routers.brief._projects_module.load_projects") as mock_load, \
         patch("apps.dashboard.routers.brief.brief_summary.get_or_create_project_summary") as mock_summary, \
         patch("apps.dashboard.routers.brief.config.brief_disabled") as mock_disabled:

        mock_load.return_value = mock_projects
        mock_summary.side_effect = RuntimeError("Service unavailable")
        mock_disabled.return_value = False

        # Should not raise an exception
        try:
            _enrich_home_artifact(artifact)
            exception_raised = False
        except Exception:
            exception_raised = True

        assert not exception_raised, "Exception should be caught and not propagated"
        # Verify briefSummary was set to empty string on failure
        assert artifact["brief"]["projects"][0].get("briefSummary") == ""

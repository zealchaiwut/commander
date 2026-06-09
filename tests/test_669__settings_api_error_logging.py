"""Tests for issue #669 — Add error logging to project resolution in settings API.

AC coverage:
  AC1  — When load_projects() raises in _resolve_project_slug, _slog.warn is
          called (exception not silently swallowed).
  AC2  — The logged message/fields include information about the error so
          the failure is diagnosable from logs.
  AC3  — The endpoint returns 404 gracefully (project not found) rather than
          propagating a 500 (load failure masked as missing project is worse
          than a clear warning).
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from fastapi.testclient import TestClient
import apps.dashboard.server as server_module


# ── AC1: _slog.warn is called when load_projects raises ──────────────────────

def test_load_projects_exception_is_logged_via_slog_warn():
    """When load_projects() raises inside _resolve_project_slug, _slog.warn
    must be called.  Silent swallowing hides infrastructure failures.
    """
    client = TestClient(server_module.app)

    with patch.object(server_module, "projects_module") as mock_pm:
        mock_pm.load_projects.side_effect = RuntimeError("DB connection failed")
        with patch.object(server_module, "_slog") as mock_slog:
            response = client.get("/api/projects/any-project/settings")

    mock_slog.warn.assert_called()


# ── AC2: Logged fields include error details ──────────────────────────────────

def test_logged_message_includes_error_details():
    """The _slog.warn call must include the exception string so the failure is
    diagnosable without correlating other log lines.
    """
    client = TestClient(server_module.app)

    error_msg = "DB connection failed: ECONNREFUSED"

    with patch.object(server_module, "projects_module") as mock_pm:
        mock_pm.load_projects.side_effect = RuntimeError(error_msg)
        with patch.object(server_module, "_slog") as mock_slog:
            response = client.get("/api/projects/any-project/settings")

    # Collect all string representations of warn call arguments
    all_text = ""
    for c in mock_slog.warn.call_args_list:
        all_text += " ".join(str(a) for a in c.args)
        all_text += " ".join(str(v) for v in c.kwargs.values())

    assert error_msg in all_text, (
        f"Error message not found in _slog.warn call. "
        f"Logged text: {all_text!r}"
    )


# ── AC3: Endpoint returns 404, not 500 ───────────────────────────────────────

def test_endpoint_returns_404_not_500_on_load_projects_failure():
    """When load_projects() raises, _resolve_project_slug must still return
    404 (project not found) rather than propagating a 500.  Callers expect
    a 404 for unknown slugs and should not receive an unhandled exception.
    """
    client = TestClient(server_module.app)

    with patch.object(server_module, "projects_module") as mock_pm:
        mock_pm.load_projects.side_effect = OSError("filesystem unavailable")
        with patch.object(server_module, "_slog"):
            response = client.get("/api/projects/nonexistent/settings")

    assert response.status_code == 404, (
        f"Expected 404 but got {response.status_code}. "
        "load_projects failure must fall back gracefully to project-not-found, not 500."
    )

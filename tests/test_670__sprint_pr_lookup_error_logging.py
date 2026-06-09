"""Tests for issue #670 — Add error logging to sprint PR lookup in finish sprint.

AC coverage:
  AC1  — When the gh pr list subprocess raises an exception in
          get_sprint_branch_status, _slog.warn is called (exception not
          silently swallowed).
  AC2  — The logged message/fields include error details so the failure is
          diagnosable from logs.
  AC3  — The endpoint still returns a valid JSON response (exists, branch,
          pr_url=None) — exception must not propagate as a 500.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from fastapi.testclient import TestClient
import apps.dashboard.server as server_module


def _client():
    return TestClient(server_module.app)


# ── AC1: _slog.warn is called when subprocess raises ─────────────────────────

def test_pr_lookup_exception_is_logged_via_slog():
    """When gh pr list raises inside get_sprint_branch_status, _slog must be
    called.  Silent swallowing hides gh auth/PATH failures during debugging.
    """
    client = _client()

    with patch.object(server_module, "github_client") as mock_gc:
        mock_gc.get_repo_for_operation.return_value = "owner/repo"
        with patch("subprocess.run") as mock_run:
            # First call (gh api for branch existence) succeeds
            success_result = MagicMock()
            success_result.returncode = 0
            # Second call (gh pr list) raises
            mock_run.side_effect = [success_result, OSError("gh: command not found")]
            with patch.object(server_module, "_slog") as mock_slog:
                response = client.get("/api/sprints/sprint-53/branch-status?project=test")

    mock_slog.warn.assert_called(), (
        "OSError from gh pr list was silently swallowed. "
        "_slog.warn must be called so the failure appears in logs."
    )


# ── AC2: Logged message includes error details ────────────────────────────────

def test_pr_lookup_logged_message_includes_error_details():
    """_slog.warn call must include the exception string so the failure is
    diagnosable without correlating other log lines.
    """
    client = _client()
    error_msg = "gh: command not found in PATH"

    with patch.object(server_module, "github_client") as mock_gc:
        mock_gc.get_repo_for_operation.return_value = "owner/repo"
        with patch("subprocess.run") as mock_run:
            success_result = MagicMock()
            success_result.returncode = 0
            mock_run.side_effect = [success_result, OSError(error_msg)]
            with patch.object(server_module, "_slog") as mock_slog:
                response = client.get("/api/sprints/sprint-53/branch-status?project=test")

    all_text = ""
    for c in mock_slog.warn.call_args_list:
        all_text += " ".join(str(a) for a in c.args)
        all_text += " ".join(str(v) for v in c.kwargs.values())

    assert error_msg in all_text, (
        f"Error message not found in _slog.warn call. Logged text: {all_text!r}"
    )


# ── AC3: Endpoint returns valid response, not 500 ────────────────────────────

def test_pr_lookup_exception_does_not_raise_500():
    """When gh pr list raises, the endpoint must still return 200 with
    pr_url=None (graceful degradation) rather than propagating a 500.
    """
    client = _client()

    with patch.object(server_module, "github_client") as mock_gc:
        mock_gc.get_repo_for_operation.return_value = "owner/repo"
        with patch("subprocess.run") as mock_run:
            success_result = MagicMock()
            success_result.returncode = 0
            mock_run.side_effect = [success_result, RuntimeError("auth failure")]
            with patch.object(server_module, "_slog"):
                response = client.get("/api/sprints/sprint-53/branch-status?project=test")

    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        "PR lookup failure must not propagate as a 500."
    )
    data = response.json()
    assert data["pr_url"] is None, (
        f"pr_url should be None when lookup fails, got {data['pr_url']!r}"
    )
    assert "branch" in data and "exists" in data, (
        "Response missing required fields: branch, exists"
    )

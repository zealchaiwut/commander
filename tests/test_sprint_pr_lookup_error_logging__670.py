"""Tests for issue #670 — Add error logging to sprint PR lookup in finish sprint.

AC coverage:
  AC1  — When the PR lookup subprocess raises, _slog.warn is called
          (exception not silently swallowed).
  AC2  — The logged message/fields include the branch name and the error
          string so the failure is diagnosable from logs.
  AC3  — The endpoint returns a valid JSON response (no 500) when the
          PR lookup fails, so the finish-sprint UI still renders.
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


# ── AC1: _slog.warn is called when PR lookup subprocess raises ────────────────

def test_pr_lookup_exception_is_logged_via_slog_warn():
    """When subprocess.run raises during PR lookup, _slog.warn must be called.
    Silent swallowing (bare `pass`) hides infrastructure failures from operators.
    """
    client = TestClient(server_module.app)

    # First subprocess.run (branch existence check) succeeds; second (PR lookup) raises.
    branch_ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(server_module, "subprocess") as mock_sp:
        mock_sp.run.side_effect = [branch_ok, RuntimeError("gh: command not found")]
        with patch.object(server_module, "_slog") as mock_slog:
            response = client.get(
                "/api/sprints/sprint-53/branch-status",
                params={"project": "zealchaiwut/commander"},
            )

    mock_slog.warn.assert_called()


# ── AC2: Logged fields include branch name and error string ──────────────────

def test_logged_message_includes_branch_and_error():
    """The _slog.warn call must include the branch name and the exception
    string so failures are diagnosable without correlating other log lines.
    """
    client = TestClient(server_module.app)

    branch_ok = MagicMock(returncode=0, stdout="", stderr="")
    error_msg = "gh auth token expired"

    with patch.object(server_module, "subprocess") as mock_sp:
        mock_sp.run.side_effect = [branch_ok, RuntimeError(error_msg)]
        with patch.object(server_module, "_slog") as mock_slog:
            response = client.get(
                "/api/sprints/sprint-53/branch-status",
                params={"project": "zealchaiwut/commander"},
            )

    all_text = ""
    for c in mock_slog.warn.call_args_list:
        all_text += " ".join(str(a) for a in c.args)
        all_text += " ".join(str(v) for v in c.kwargs.values())

    assert error_msg in all_text, (
        f"Error message not found in _slog.warn call. Logged text: {all_text!r}"
    )
    assert "sprint/sprint-53" in all_text, (
        f"Branch name not found in _slog.warn call. Logged text: {all_text!r}"
    )


# ── AC3: Endpoint returns valid JSON (no 500) when PR lookup fails ────────────

def test_endpoint_returns_valid_json_on_pr_lookup_failure():
    """When the PR lookup subprocess raises, the endpoint must still return 200
    with a valid response shape — not a 500.  The finish-sprint UI depends on
    this endpoint and must not crash when PR information is unavailable.
    """
    client = TestClient(server_module.app)

    branch_ok = MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(server_module, "subprocess") as mock_sp:
        mock_sp.run.side_effect = [branch_ok, OSError("network unreachable")]
        with patch.object(server_module, "_slog"):
            response = client.get(
                "/api/sprints/sprint-53/branch-status",
                params={"project": "zealchaiwut/commander"},
            )

    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        "PR lookup failure must not propagate as a 500."
    )
    data = response.json()
    assert "exists" in data, "Response must contain 'exists' key."
    assert "branch" in data, "Response must contain 'branch' key."
    # pr_url/pr_number/pr_title should be None when lookup fails
    assert data.get("pr_url") is None
    assert data.get("pr_number") is None
    assert data.get("pr_title") is None

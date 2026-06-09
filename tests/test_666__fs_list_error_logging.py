"""Tests for issue #666 — Add error logging for fs_list path resolution failures.

AC coverage:
  AC1  — PermissionError during fs_list directory iteration is logged at INFO level
          (not silently swallowed).
  AC2  — OSError (non-permission I/O failure) during fs_list directory iteration
          is also logged at INFO level and does not propagate as an unhandled 500.
  AC3  — The log message includes the target path so failures are diagnosable.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client():
    import importlib
    import apps.dashboard.server as server_module
    importlib.reload(server_module)
    from apps.dashboard.server import app
    return TestClient(app), server_module


# ── AC1: PermissionError is logged at INFO level ──────────────────────────────

def test_permission_error_is_logged_at_info(tmp_path):
    """When iterdir() raises PermissionError, logger.info must be called.

    The endpoint must still return 200 with empty entries (not a 500),
    and the exception must NOT be silently swallowed — it must be logged
    at INFO level.
    """
    import apps.dashboard.server as server_module

    client = TestClient(server_module.app)

    with patch.object(server_module, "_FS_BROWSE_ROOT", tmp_path):
        with patch.object(server_module, "logger") as mock_logger:
            with patch.object(Path, "iterdir", side_effect=PermissionError("no access")):
                response = client.get("/api/fs/list")

    assert response.status_code == 200
    assert response.json()["entries"] == []

    # logger.info must have been called at least once
    assert mock_logger.info.called, (
        "PermissionError was silently swallowed. "
        "logger.info must be called so the failure is diagnosable."
    )


# ── AC2: OSError is logged at INFO level and does not yield a 500 ─────────────

def test_oserror_is_logged_at_info_not_500(tmp_path):
    """When iterdir() raises a generic OSError (e.g. I/O error), the endpoint
    must return 200 with empty entries — not a 500 — and logger.info must be
    called so the failure is diagnosable.
    """
    import apps.dashboard.server as server_module

    client = TestClient(server_module.app)

    with patch.object(server_module, "_FS_BROWSE_ROOT", tmp_path):
        with patch.object(server_module, "logger") as mock_logger:
            with patch.object(Path, "iterdir", side_effect=OSError("I/O error")):
                response = client.get("/api/fs/list")

    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        "OSError must be caught and logged, not propagated as a 500."
    )
    assert response.json()["entries"] == []
    assert mock_logger.info.called, "OSError must be logged at INFO level."


# ── AC3: Log message contains the target path ─────────────────────────────────

def test_log_message_includes_target_path(tmp_path):
    """The INFO log message for a path resolution failure must include the
    target path so that operators can identify which directory triggered the
    error without needing to correlate with request logs.
    """
    import apps.dashboard.server as server_module

    client = TestClient(server_module.app)

    with patch.object(server_module, "_FS_BROWSE_ROOT", tmp_path):
        with patch.object(server_module, "logger") as mock_logger:
            with patch.object(Path, "iterdir", side_effect=PermissionError("no access")):
                response = client.get("/api/fs/list")

    assert mock_logger.info.called
    # Collect all args/kwargs from every logger.info call
    all_args = []
    for c in mock_logger.info.call_args_list:
        all_args.extend(str(a) for a in c.args)
        all_args.extend(str(v) for v in c.kwargs.values())

    logged_text = " ".join(all_args)
    assert str(tmp_path) in logged_text, (
        f"Target path '{tmp_path}' not found in log message. "
        f"Logged: {logged_text!r}"
    )

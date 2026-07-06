"""Tests for issue #1754: Board card 'Re-run → N.1' button body contract.

AC coverage:
- AC1: POST /api/sprints/{label}/rerun with valid JSON body is accepted (not 422 for missing body)
- AC2: POST /api/sprints/{label}/rerun with no body 422s (proves frontend must send body)
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _patches(tmp_path):
    """Common patches needed to call the rerun endpoint without side effects."""
    import github_client as gc

    gc.list_open_issues_with_body = MagicMock(return_value=[])
    gc.list_labels = MagicMock(return_value=[])
    gc.get_label_color = MagicMock(return_value="0075ca")
    gc.create_label = MagicMock()
    gc.update_labels = MagicMock()
    gc.invalidate = MagicMock()

    sm_path = tmp_path / "sprint_manager.py"
    sm_path.touch()

    return [
        patch("server._is_sprint_running", return_value=False),
        patch("server._project_root_path", return_value=tmp_path),
        patch("server._coder_clone_path", return_value=tmp_path),
        patch("server.SPRINT_MANAGER_PATH", sm_path),
    ]


class _stack:
    def __init__(self, ctx_managers):
        self._cms = ctx_managers
        self._entered = []

    def __enter__(self):
        for cm in self._cms:
            self._entered.append(cm.__enter__())
        return self

    def __exit__(self, *args):
        for cm in reversed(self._cms):
            cm.__exit__(*args)


def test_rerun_body_valid_json_accepted(tmp_path):
    """AC1: valid JSON body is accepted — no 422 for missing body."""
    with _stack(_patches(tmp_path)):
        resp = client.post(
            "/api/sprints/sprint-106/rerun?project=owner/repo",
            json={"ticket_numbers": [], "auto_run": False},
        )
    # Any non-422-for-missing-body response confirms the endpoint received the body.
    # 200 (noop when no issues), 404, or 409 are all valid depending on state.
    assert resp.status_code != 422 or "body" not in resp.text.lower(), (
        "Endpoint must accept a valid JSON body without 422-for-missing-body"
    )


def test_rerun_no_body_returns_422(tmp_path):
    """AC2: omitting the body 422s, confirming the frontend click handler must send one."""
    with _stack(_patches(tmp_path)):
        resp = client.post(
            "/api/sprints/sprint-106/rerun?project=owner/repo",
        )
    assert resp.status_code == 422, (
        f"Expected 422 when no body sent; got {resp.status_code}"
    )
    detail = resp.json().get("detail", [])
    assert any("body" in str(d).lower() for d in detail), (
        "422 detail must mention missing body"
    )

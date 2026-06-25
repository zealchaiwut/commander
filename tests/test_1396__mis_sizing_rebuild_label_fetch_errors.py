"""Tests for issue #1396 — surface GitHub fetch failures in mis-sizing history rebuild.

One test per acceptance criterion.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def _make_project_tree(tmp_path: Path, issue_num: int = 42, estimated_size: str = "M") -> tuple[Path, Path]:
    """Create minimal sprint-state + estimate files so rebuild finds one completed ticket."""
    commander = tmp_path / ".commander"
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"
    sprints_dir.mkdir(parents=True)
    estimates_dir.mkdir(parents=True)

    state = {
        "issues": [
            {
                "number": issue_num,
                "status": "done",
                "coder_started_at": None,
                "tester_finished_at": None,
                "status_changed_at": None,
            }
        ]
    }
    (sprints_dir / "sprint-1-state.json").write_text(json.dumps(state))
    (estimates_dir / f"issue-{issue_num}.json").write_text(json.dumps({"size": estimated_size}))

    return tmp_path, commander


def _rebuild_fn():
    """Import and return the rebuild route handler from the router module."""
    mod = importlib.import_module("routers.mis_sizing")
    # The handler is registered on the router; pull it out by name
    return mod.rebuild_mis_sizing_history


def _call_rebuild(tmp_path: Path, *, subprocess_mock, github_client_mock) -> dict:
    """Call rebuild_mis_sizing_history with the project pointing at tmp_path."""
    mod = importlib.import_module("routers.mis_sizing")
    project = tmp_path.name

    # Point _project_root_path → tmp_path
    with patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", github_client_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        return mod.rebuild_mis_sizing_history(project=project)


# ── AC1: non-zero returncode → stderr captured and logged at WARNING ───────────

def test_ac1_nonzero_returncode_logs_warning(tmp_path, caplog):
    """When gh exits non-zero, stderr is captured and logged at WARNING level."""
    _make_project_tree(tmp_path)

    proc_result = MagicMock()
    proc_result.returncode = 1
    proc_result.stderr = "authentication required: run gh auth login"
    proc_result.stdout = ""

    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc_result

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    with caplog.at_level(logging.WARNING, logger="routers.mis_sizing"):
        _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("authentication required" in m for m in warning_messages), (
        f"Expected stderr text in WARNING log, got: {warning_messages}"
    )


# ── AC2: exception during label fetch is logged, not silently passed ───────────

def test_ac2_exception_during_fetch_is_logged(tmp_path, caplog):
    """When an exception is raised during the gh subprocess call, it is logged (not silently swallowed)."""
    _make_project_tree(tmp_path)

    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = TimeoutError("gh timed out after 60s")

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    with caplog.at_level(logging.WARNING, logger="routers.mis_sizing"):
        result = _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("gh timed out after 60s" in m or "TimeoutError" in m for m in warning_messages), (
        f"Expected exception info in WARNING log, got: {warning_messages}"
    )
    # Must continue and return a result (not crash)
    assert "message" in result


# ── AC3: response includes labels_fetched boolean field ───────────────────────

def test_ac3_response_includes_labels_fetched_false_on_failure(tmp_path):
    """When label fetch fails (non-zero returncode), response has labels_fetched=false."""
    _make_project_tree(tmp_path)

    proc_result = MagicMock()
    proc_result.returncode = 1
    proc_result.stderr = "rate limit exceeded"
    proc_result.stdout = ""

    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc_result

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    result = _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    assert "labels_fetched" in result, f"Response missing 'labels_fetched' key: {result}"
    assert result["labels_fetched"] is False, (
        f"Expected labels_fetched=False on fetch failure, got: {result['labels_fetched']}"
    )


def test_ac3_response_includes_labels_fetched_false_on_exception(tmp_path):
    """When label fetch raises an exception, response has labels_fetched=false."""
    _make_project_tree(tmp_path)

    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = OSError("network unreachable")

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    result = _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    assert "labels_fetched" in result, f"Response missing 'labels_fetched' key: {result}"
    assert result["labels_fetched"] is False


# ── AC4: HTTP 200 and message intact when labels_fetched=false ─────────────────

def test_ac4_http200_and_message_intact_when_labels_fetch_fails(tmp_path):
    """HTTP 200 is returned with the 'History rebuilt:' message when label fetch fails."""
    _make_project_tree(tmp_path)

    proc_result = MagicMock()
    proc_result.returncode = 2
    proc_result.stderr = "auth error"
    proc_result.stdout = ""

    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc_result

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    result = _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    # Route returns a dict (FastAPI wraps it as 200); check message is intact
    assert "message" in result
    assert result["message"].startswith("History rebuilt:"), (
        f"Expected 'History rebuilt:' message, got: {result['message']}"
    )
    assert result["labels_fetched"] is False


# ── AC5: labels_fetched=true when fetch succeeds and labels_by_num is populated ─

def test_ac5_labels_fetched_true_on_success(tmp_path):
    """When gh returns exit 0 and labels are populated, response has labels_fetched=true."""
    issue_num = 42
    _make_project_tree(tmp_path, issue_num=issue_num)

    gh_response = json.dumps([
        {"number": issue_num, "labels": [{"name": "size-M"}, {"name": "backend"}]},
    ])

    proc_result = MagicMock()
    proc_result.returncode = 0
    proc_result.stdout = gh_response
    proc_result.stderr = ""

    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc_result

    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    result = _call_rebuild(tmp_path, subprocess_mock=subprocess_mock, github_client_mock=github_client_mock)

    assert "labels_fetched" in result, f"Response missing 'labels_fetched' key: {result}"
    assert result["labels_fetched"] is True, (
        f"Expected labels_fetched=True on successful fetch, got: {result['labels_fetched']}"
    )

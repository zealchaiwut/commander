"""Tests for #450: retry with exponential backoff in issue estimation flow."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.estimate_issue import (
    _ESTIMATOR_MAX_RETRIES,
    _ESTIMATOR_RETRY_DELAYS,
    run_estimator,
)

_ISSUE_DATA = {"title": "Test issue", "body": "Some body."}

_VALID_PAYLOAD = {
    "size": "S",
    "estimated_hours": 1,
    "confidence": "high",
    "files_likely_affected": [],
    "depends_on": [],
    "blocks": [],
    "risk_flags": [],
    "summary": "Small fix.",
}


def _ok(payload=None):
    p = MagicMock()
    p.returncode = 0
    p.stdout = json.dumps(payload or _VALID_PAYLOAD)
    p.stderr = ""
    return p


def _fail(returncode=1, stderr="model error"):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = ""
    p.stderr = stderr
    return p


def _parse_fail():
    p = MagicMock()
    p.returncode = 0
    p.stdout = "not json at all"
    p.stderr = ""
    return p


# ── retry constants ───────────────────────────────────────────────────────────

def test_retry_constants():
    assert _ESTIMATOR_MAX_RETRIES == 3
    assert _ESTIMATOR_RETRY_DELAYS == [2, 4, 8]


# ── success on first attempt ──────────────────────────────────────────────────

def test_success_first_attempt():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.return_value = _ok()
        result = run_estimator(1, _ISSUE_DATA)

    assert result is not None
    assert result["size"] == "S"
    assert mock_run.call_count == 1
    mock_time.sleep.assert_not_called()


# ── success on second attempt (after one failure) ─────────────────────────────

def test_success_second_attempt_model_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.side_effect = [_fail(), _ok()]
        result = run_estimator(1, _ISSUE_DATA)

    assert result is not None
    assert mock_run.call_count == 2
    mock_time.sleep.assert_called_once_with(2)


def test_success_second_attempt_parse_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.side_effect = [_parse_fail(), _ok()]
        result = run_estimator(1, _ISSUE_DATA)

    assert result is not None
    assert mock_run.call_count == 2
    mock_time.sleep.assert_called_once_with(2)


def test_success_second_attempt_network_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.side_effect = [subprocess.TimeoutExpired(cmd="claude", timeout=180), _ok()]
        result = run_estimator(1, _ISSUE_DATA)

    assert result is not None
    assert mock_run.call_count == 2
    mock_time.sleep.assert_called_once_with(2)


# ── all retries exhausted ─────────────────────────────────────────────────────

def test_all_retries_exhausted_model_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.return_value = _fail()
        result = run_estimator(1, _ISSUE_DATA)

    assert result is None
    assert mock_run.call_count == 4  # 1 initial + 3 retries
    assert mock_time.sleep.call_count == 3
    mock_time.sleep.assert_any_call(2)
    mock_time.sleep.assert_any_call(4)
    mock_time.sleep.assert_any_call(8)


def test_all_retries_exhausted_parse_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.return_value = _parse_fail()
        result = run_estimator(1, _ISSUE_DATA)

    assert result is None
    assert mock_run.call_count == 4
    assert mock_time.sleep.call_args_list == [call(2), call(4), call(8)]


def test_all_retries_exhausted_network_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time") as mock_time:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=180)
        result = run_estimator(1, _ISSUE_DATA)

    assert result is None
    assert mock_run.call_count == 4
    assert mock_time.sleep.call_args_list == [call(2), call(4), call(8)]


# ── retry log entries emitted ─────────────────────────────────────────────────

def test_retry_log_entry_emitted_on_model_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time"), \
         patch("services.sprint_manager.estimate_issue.structured_log") as mock_log:
        mock_run.side_effect = [_fail(), _ok()]
        run_estimator(42, _ISSUE_DATA)

    warn_calls = [c for c in mock_log.warn.call_args_list if c.args[0] == "estimator_retry"]
    assert len(warn_calls) == 1
    kwargs = warn_calls[0].kwargs
    assert kwargs["attempt"] == 1
    assert kwargs["error_type"] == "model_error"
    assert kwargs["delay_seconds"] == 2


def test_retry_log_entry_emitted_on_parse_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time"), \
         patch("services.sprint_manager.estimate_issue.structured_log") as mock_log:
        mock_run.side_effect = [_parse_fail(), _ok()]
        run_estimator(7, _ISSUE_DATA)

    warn_calls = [c for c in mock_log.warn.call_args_list if c.args[0] == "estimator_retry"]
    assert len(warn_calls) == 1
    kwargs = warn_calls[0].kwargs
    assert kwargs["error_type"] == "parse_error"
    assert "delay_seconds" in kwargs


def test_retry_log_entry_emitted_on_network_error():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time"), \
         patch("services.sprint_manager.estimate_issue.structured_log") as mock_log:
        mock_run.side_effect = [subprocess.TimeoutExpired(cmd="claude", timeout=180), _ok()]
        run_estimator(3, _ISSUE_DATA)

    warn_calls = [c for c in mock_log.warn.call_args_list if c.args[0] == "estimator_retry"]
    assert len(warn_calls) == 1
    kwargs = warn_calls[0].kwargs
    assert kwargs["error_type"] == "network_error"
    assert kwargs["delay_seconds"] == 2


def test_three_retry_log_entries_when_all_exhausted():
    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.time"), \
         patch("services.sprint_manager.estimate_issue.structured_log") as mock_log:
        mock_run.return_value = _fail()
        run_estimator(99, _ISSUE_DATA)

    warn_calls = [c for c in mock_log.warn.call_args_list if c.args[0] == "estimator_retry"]
    assert len(warn_calls) == 3
    delays = [c.kwargs["delay_seconds"] for c in warn_calls]
    assert delays == [2, 4, 8]
    attempts = [c.kwargs["attempt"] for c in warn_calls]
    assert attempts == [1, 2, 3]

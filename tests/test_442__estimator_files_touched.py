"""Tests for #442: files_touched field in estimator output."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.estimate_issue import extract_json, run_estimator


# ── extract_json helpers ───────────────────────────────────────────────────────

def test_extract_json_with_files_touched():
    raw = json.dumps({
        "issue_number": 1,
        "size": "S",
        "estimated_hours": 1,
        "confidence": "high",
        "files_likely_affected": ["services/sprint_manager/estimate_issue.py"],
        "files_touched": ["services/sprint_manager/estimate_issue.py"],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "Small change.",
    })
    result = extract_json(raw)
    assert result is not None
    assert result["files_touched"] == ["services/sprint_manager/estimate_issue.py"]


def test_extract_json_without_files_touched():
    raw = json.dumps({
        "issue_number": 1,
        "size": "S",
        "estimated_hours": 1,
        "confidence": "high",
        "files_likely_affected": ["services/sprint_manager/estimate_issue.py"],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "Small change.",
    })
    result = extract_json(raw)
    assert result is not None
    assert "files_touched" not in result


# ── run_estimator normalization ────────────────────────────────────────────────

def _mock_completed_process(stdout: str, returncode: int = 0):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = ""
    return p


def test_run_estimator_normalizes_missing_files_touched():
    """Model response that omits files_touched → normalized to []."""
    payload = {
        "size": "M",
        "estimated_hours": 2,
        "confidence": "medium",
        "files_likely_affected": ["apps/dashboard/main.py"],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "Adds feature X.",
    }
    issue_data = {"title": "Test issue", "body": "Body text."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_completed_process(json.dumps(payload))
        estimate, error_type = run_estimator(1, issue_data)

    assert estimate is not None
    assert error_type is None
    assert estimate["files_touched"] == []


def test_run_estimator_preserves_files_touched_when_present():
    """Model response that includes files_touched → value preserved."""
    expected_files = ["apps/dashboard/main.py", "services/sprint_manager/estimate_issue.py"]
    payload = {
        "size": "M",
        "estimated_hours": 2,
        "confidence": "medium",
        "files_likely_affected": expected_files,
        "files_touched": expected_files,
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "Adds feature X.",
    }
    issue_data = {"title": "Test issue", "body": "Body text."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_completed_process(json.dumps(payload))
        estimate, error_type = run_estimator(1, issue_data)

    assert estimate is not None
    assert error_type is None
    assert estimate["files_touched"] == expected_files


def test_run_estimator_normalizes_empty_files_touched():
    """Model response with files_touched: [] → preserved as []."""
    payload = {
        "size": "S",
        "estimated_hours": 1,
        "confidence": "high",
        "files_likely_affected": [],
        "files_touched": [],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "No files.",
    }
    issue_data = {"title": "Test issue", "body": "Body."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_completed_process(json.dumps(payload))
        estimate, error_type = run_estimator(1, issue_data)

    assert estimate is not None
    assert error_type is None
    assert estimate["files_touched"] == []

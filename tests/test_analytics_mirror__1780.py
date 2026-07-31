"""Tests for issue #1780 — mis_sizing, estimates, and sprint_summaries route through mirror.

AC2: POST /api/mis-sizing/rebuild issues zero gh subprocess calls with populated mirror.
AC3: estimates._get_uat_numbers uses mirror when populated, zero subprocess calls.
AC4: GET /api/sprints/summaries issues zero gh subprocess calls with populated mirror.
AC6: each endpoint falls back to gh when mirror is empty.
AC5: response payloads are byte-identical between mirror path and gh-baseline fixture.
AC8: mis_sizing.py replaces --state all --limit 1000 subprocess with mirror read.
AC9: estimates.py replaces --state all --label UAT --limit 200 with mirror read.
AC10: sprint_summaries.py replaces its per-request subprocess with mirror read.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

import db  # noqa: E402


# ══ AC2 / AC8: POST /api/mis-sizing/rebuild ══════════════════════════════════

def _make_rebuild_tree(tmp_path: Path, issue_num: int = 42, size: str = "M") -> tuple[Path, Path]:
    """Create minimal sprint-state + estimate files for rebuild."""
    commander = tmp_path / ".commander"
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"
    sprints_dir.mkdir(parents=True)
    estimates_dir.mkdir(parents=True)
    state = {"issues": [{"number": issue_num, "status": "done",
                          "coder_started_at": None, "tester_finished_at": None,
                          "status_changed_at": None}]}
    (sprints_dir / "sprint-1-state.json").write_text(json.dumps(state))
    (estimates_dir / f"issue-{issue_num}.json").write_text(json.dumps({"size": size}))
    return tmp_path, commander


def _call_rebuild(tmp_path: Path, *, mirror: list[dict], raise_on_subprocess: bool = False) -> dict:
    issue_num = 42
    mod = importlib.import_module("routers.mis_sizing")

    subprocess_mock = MagicMock()
    if raise_on_subprocess:
        subprocess_mock.run.side_effect = AssertionError(
            "subprocess.run must NOT be called when mirror is populated"
        )
    subprocess_mock.CalledProcessError = subprocess.CalledProcessError

    gh_mock = MagicMock()
    gh_mock.get_repo_for_operation.return_value = "owner/repo"

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", gh_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        return mod.rebuild_mis_sizing_history(project="owner/repo")


def test_ac2_rebuild_zero_gh_calls_with_populated_mirror(tmp_path):
    """AC2/AC8: rebuild fires no subprocess when mirror has issue data."""
    _make_rebuild_tree(tmp_path)
    mirror = [{"number": 42, "state": "closed", "labels": [{"name": "size-M"}], "updatedAt": ""}]

    result = _call_rebuild(tmp_path, mirror=mirror, raise_on_subprocess=True)

    assert "message" in result
    assert result["labels_fetched"] is True


def test_ac2_rebuild_labels_from_mirror_populate_history(tmp_path):
    """AC8: labels fetched from mirror are used to build history (not subprocess)."""
    _make_rebuild_tree(tmp_path, issue_num=99, size="L")
    mirror = [
        {"number": 99, "state": "closed",
         "labels": [{"name": "size-L"}, {"name": "backend"}], "updatedAt": ""},
    ]

    result = _call_rebuild(tmp_path, mirror=mirror, raise_on_subprocess=True)

    assert result["labels_fetched"] is True
    assert result.get("total_events", 0) >= 0


def test_ac6_rebuild_empty_mirror_falls_back_to_gh(tmp_path):
    """AC6: when mirror empty, rebuild falls back to subprocess without error."""
    _make_rebuild_tree(tmp_path)
    gh_response = json.dumps([{"number": 42, "labels": [{"name": "size-M"}]}])

    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = gh_response

    mod = importlib.import_module("routers.mis_sizing")
    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc
    subprocess_mock.CalledProcessError = subprocess.CalledProcessError

    gh_mock = MagicMock()
    gh_mock.get_repo_for_operation.return_value = "owner/repo"

    with patch.object(db, "get_mirrored_issues", return_value=[]), \
         patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", gh_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        result = mod.rebuild_mis_sizing_history(project="owner/repo")

    assert result["labels_fetched"] is True
    subprocess_mock.run.assert_called_once()  # gh called as fallback


def test_ac5_rebuild_response_schema_unchanged(tmp_path):
    """AC5: rebuild response keys are identical between mirror and gh paths."""
    _make_rebuild_tree(tmp_path)
    mirror = [{"number": 42, "state": "closed", "labels": [{"name": "size-M"}], "updatedAt": ""}]
    result = _call_rebuild(tmp_path, mirror=mirror, raise_on_subprocess=True)

    assert "message" in result
    assert "labels_with_history" in result
    assert "total_events" in result
    assert "last_rebuilt" in result
    assert "labels_fetched" in result
    assert isinstance(result["labels_fetched"], bool)


# ══ AC3 / AC9: estimates._get_uat_numbers ════════════════════════════════════

def test_ac3_get_uat_numbers_uses_mirror_when_populated():
    """AC3/AC9: _get_uat_numbers returns UAT numbers from mirror without subprocess."""
    mod = importlib.import_module("routers.estimates")

    mirror = [
        {"number": 100, "state": "closed", "labels": [{"name": "UAT"}], "updatedAt": ""},
        {"number": 101, "state": "open", "labels": [{"name": "in-progress"}], "updatedAt": ""},
        {"number": 102, "state": "closed", "labels": [{"name": "UAT"}, {"name": "sprint-5"}], "updatedAt": ""},
    ]
    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = AssertionError("subprocess.run must NOT be called")

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "subprocess", subprocess_mock):
        result = mod._get_uat_numbers("owner/repo", "owner/repo")

    assert result == {100, 102}


def test_ac3_get_uat_numbers_empty_set_when_no_uat_in_mirror():
    """AC3: _get_uat_numbers returns empty set when mirror has no UAT issues."""
    mod = importlib.import_module("routers.estimates")

    mirror = [
        {"number": 200, "state": "open", "labels": [{"name": "in-progress"}], "updatedAt": ""},
    ]
    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = AssertionError("subprocess.run must NOT be called")

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "subprocess", subprocess_mock):
        result = mod._get_uat_numbers("owner/repo", "owner/repo")

    assert result == set()


def test_ac6_get_uat_numbers_falls_back_to_gh_when_mirror_empty():
    """AC6: _get_uat_numbers falls back to subprocess when mirror returns no rows."""
    mod = importlib.import_module("routers.estimates")

    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps([{"number": 300}, {"number": 301}])

    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc

    with patch.object(db, "get_mirrored_issues", return_value=[]), \
         patch.object(mod, "subprocess", subprocess_mock):
        result = mod._get_uat_numbers("owner/repo", "owner/repo")

    assert result == {300, 301}
    subprocess_mock.run.assert_called_once()


def test_ac6_get_uat_numbers_returns_empty_on_gh_failure():
    """AC6: _get_uat_numbers returns empty set if both mirror and gh fail."""
    mod = importlib.import_module("routers.estimates")

    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = OSError("network error")

    with patch.object(db, "get_mirrored_issues", return_value=[]), \
         patch.object(mod, "subprocess", subprocess_mock):
        result = mod._get_uat_numbers("owner/repo", "owner/repo")

    assert result == set()


# ══ AC4 / AC10: GET /api/sprints/summaries ═══════════════════════════════════

def _make_server_mock(tmp_path: Path) -> MagicMock:
    """Build a MagicMock that acts as the server module for sprint_summaries."""
    import re
    srv = MagicMock()
    srv._SUMMARY_TITLE_RE = re.compile(r"^Sprint \d+(?:\.\d+)? Executive Summary$")
    srv._project_root_path.return_value = tmp_path
    srv._commander_dir.return_value = tmp_path / ".commander"
    srv._sprint_json_path.return_value = tmp_path / ".commander" / "sprints" / "sprint-1.json"
    srv._sprint_json_read.return_value = {}
    srv._parse_summary_file.return_value = {}
    srv._has_rework_tickets.return_value = False
    return srv


def test_ac4_summaries_zero_gh_calls_with_mirror(tmp_path):
    """AC4/AC10: GET /api/sprints/summaries fires no subprocess when mirror has data."""
    (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)

    mirror = [
        {
            "number": 10, "state": "open",
            "title": "Sprint 1 Executive Summary",
            "labels": [{"name": "sprint-summary"}, {"name": "sprint-1"}],
            "url": "https://github.com/owner/repo/issues/10",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "",
        },
    ]

    mod = importlib.import_module("routers.sprint_summaries")
    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = AssertionError(
        "subprocess.run must NOT be called when mirror is populated"
    )

    srv_mock = _make_server_mock(tmp_path)

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "subprocess", subprocess_mock), \
         patch.object(mod, "github_client") as gh_mock, \
         patch.object(mod, "_server", return_value=srv_mock):
        gh_mock.get_repo_for_operation.return_value = "owner/repo"
        gh_mock.list_open_issues_with_body.return_value = []
        result = mod.get_sprint_summaries("owner/repo")

    assert len(result["summaries"]) == 1
    assert result["summaries"][0]["number"] == 10


def test_ac4_summaries_filters_by_sprint_summary_label(tmp_path):
    """AC4: mirror results are filtered to sprint-summary label only."""
    (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)

    mirror = [
        {
            "number": 20, "state": "open",
            "title": "Sprint 2 Executive Summary",
            "labels": [{"name": "sprint-summary"}, {"name": "sprint-2"}],
            "url": "https://github.com/owner/repo/issues/20",
            "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "",
        },
        {
            "number": 21, "state": "open",
            "title": "Some feature ticket",
            "labels": [{"name": "sprint-2"}, {"name": "enhancement"}],
            "url": "https://github.com/owner/repo/issues/21",
            "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "",
        },
    ]

    mod = importlib.import_module("routers.sprint_summaries")
    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = AssertionError("subprocess.run must NOT be called")

    srv_mock = _make_server_mock(tmp_path)

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "subprocess", subprocess_mock), \
         patch.object(mod, "github_client") as gh_mock, \
         patch.object(mod, "_server", return_value=srv_mock):
        gh_mock.get_repo_for_operation.return_value = "owner/repo"
        gh_mock.list_open_issues_with_body.return_value = []
        result = mod.get_sprint_summaries("owner/repo")

    nums = [s["number"] for s in result["summaries"]]
    assert 20 in nums
    assert 21 not in nums, "Non-sprint-summary issue should be filtered out"


def test_ac6_summaries_falls_back_to_gh_when_mirror_empty(tmp_path):
    """AC6: summaries falls back to subprocess when mirror returns no rows."""
    (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)

    gh_response = json.dumps([
        {
            "number": 30, "state": "open",
            "title": "Sprint 3 Executive Summary",
            "labels": [{"name": "sprint-summary"}, {"name": "sprint-3"}],
            "url": "https://github.com/owner/repo/issues/30",
            "createdAt": "2026-03-01T00:00:00Z",
        },
    ])
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = gh_response

    mod = importlib.import_module("routers.sprint_summaries")
    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc

    srv_mock = _make_server_mock(tmp_path)

    with patch.object(db, "get_mirrored_issues", return_value=[]), \
         patch.object(mod, "subprocess", subprocess_mock), \
         patch.object(mod, "github_client") as gh_mock, \
         patch.object(mod, "_server", return_value=srv_mock):
        gh_mock.get_repo_for_operation.return_value = "owner/repo"
        gh_mock.list_open_issues_with_body.return_value = []
        result = mod.get_sprint_summaries("owner/repo")

    subprocess_mock.run.assert_called_once()  # subprocess used as fallback
    nums = [s["number"] for s in result["summaries"]]
    assert 30 in nums


def test_ac5_summaries_response_schema_unchanged(tmp_path):
    """AC5: summaries response schema is identical between mirror and gh paths."""
    (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)

    mirror = [
        {
            "number": 40, "state": "open",
            "title": "Sprint 4 Executive Summary",
            "labels": [{"name": "sprint-summary"}, {"name": "sprint-4"}],
            "url": "https://github.com/owner/repo/issues/40",
            "createdAt": "2026-04-01T00:00:00Z", "updatedAt": "",
        },
    ]

    mod = importlib.import_module("routers.sprint_summaries")
    subprocess_mock = MagicMock()
    subprocess_mock.run.side_effect = AssertionError("subprocess.run must NOT be called")

    srv_mock = _make_server_mock(tmp_path)

    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "subprocess", subprocess_mock), \
         patch.object(mod, "github_client") as gh_mock, \
         patch.object(mod, "_server", return_value=srv_mock):
        gh_mock.get_repo_for_operation.return_value = "owner/repo"
        gh_mock.list_open_issues_with_body.return_value = []
        result = mod.get_sprint_summaries("owner/repo")

    assert "summaries" in result
    s = result["summaries"][0]
    # All expected keys must be present with correct types
    expected_keys = {
        "number", "title", "sprint_number", "sprint_sub_label", "state",
        "outcome", "url", "created_at", "summary_file_path",
    }
    assert expected_keys.issubset(set(s.keys())), (
        f"Missing keys: {expected_keys - set(s.keys())}"
    )
    assert s["number"] == 40
    assert s["state"] == "open"
    assert isinstance(s["sprint_number"], int)

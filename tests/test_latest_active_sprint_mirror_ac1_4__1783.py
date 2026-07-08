"""Tests for issue #1783 AC1-4: latest_active_sprint mirror-based derivation.

AC mapping:
AC1  latest_active_sprint derives the active sprint from group_issues_by_sprint (mirror):
     the sprint with the highest sprint number that contains at least one non-closed issue.
AC2  When the mirror is populated, latest_active_sprint makes zero gh subprocess calls
     (verified by patching/mocking subprocess in a unit test).
AC3  latest_active_sprint falls back to the existing gh issue list GraphQL path
     only when the mirror is empty/unpopulated.
AC4  A parity test confirms latest_active_sprint returns the same sprint label
     against a fixture mirror as the legacy gh path returns against the same fixture data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

import github_client as gc  # noqa: E402


def _mock_issue(number: int, state: str, sprint: int) -> dict:
    return {
        "number": number,
        "state": state,
        "labels": [{"name": f"sprint-{sprint}"}],
        "title": f"Issue {number} in sprint {sprint}",
    }


# ── AC1: derive active sprint from mirror ──────────────────────────────────

def test_ac1_derives_highest_sprint_with_open_issue():
    """AC1: latest_active_sprint returns the sprint with highest number
    that contains at least one open (non-closed) issue."""
    mirror_data = [
        _mock_issue(1, "open", 95),
        _mock_issue(2, "open", 97),
        _mock_issue(3, "closed", 97),  # closed issue in 97
        _mock_issue(4, "closed", 98),  # all issues in 98 are closed
        _mock_issue(5, "closed", 98),
    ]
    grouped = {
        95: [mirror_data[0]],
        97: [mirror_data[1], mirror_data[2]],  # has an open one
        98: [mirror_data[3], mirror_data[4]],  # all closed
    }

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        result = gc.latest_active_sprint("owner/repo")

    # Should return 97 (highest sprint with at least one open)
    assert result == 97, f"Expected 97, got {result}"


def test_ac1_returns_max_of_active_sprints():
    """AC1: among all sprints with open issues, return the highest number."""
    mirror_data = [
        _mock_issue(1, "open", 50),
        _mock_issue(2, "open", 75),
        _mock_issue(3, "open", 60),
        _mock_issue(4, "closed", 75),
    ]
    grouped = {
        50: [mirror_data[0]],
        60: [mirror_data[2]],
        75: [mirror_data[1], mirror_data[3]],  # 75 has both open and closed
    }

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        result = gc.latest_active_sprint("owner/repo")

    # Should return 75 (highest sprint with ≥1 open)
    assert result == 75, f"Expected 75, got {result}"


def test_ac1_ignores_sprints_with_only_closed():
    """AC1: sprints containing only closed issues are not considered active."""
    mirror_data = [
        _mock_issue(10, "open", 100),
        _mock_issue(20, "closed", 200),
        _mock_issue(21, "closed", 200),
        _mock_issue(30, "closed", 300),
    ]
    grouped = {
        100: [mirror_data[0]],
        200: [mirror_data[1], mirror_data[2]],
        300: [mirror_data[3]],
    }

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        result = gc.latest_active_sprint("owner/repo")

    # Only 100 has open issues; return 100 (ignore 200, 300)
    assert result == 100, f"Expected 100, got {result}"


def test_ac1_fallback_to_list_sprints_when_no_open():
    """AC1: if all issues are closed (no active sprints), fall back to list_sprints."""
    mirror_data = [
        _mock_issue(1, "closed", 90),
        _mock_issue(2, "closed", 91),
    ]
    grouped = {
        90: [mirror_data[0]],
        91: [mirror_data[1]],
    }

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch.object(gc, "list_sprints", return_value=[90, 91, 92]):
            result = gc.latest_active_sprint("owner/repo")

    # No open issues; fall back to list_sprints and return max
    assert result == 92, f"Expected 92 from list_sprints, got {result}"


# ── AC2: zero subprocess calls when mirror populated ─────────────────────

def test_ac2_zero_subprocess_calls_on_mirror_hit():
    """AC2: When mirror is populated, latest_active_sprint makes zero gh subprocess calls."""
    mirror_data = [_mock_issue(1, "open", 42)]
    grouped = {42: mirror_data}  # grouping returns list of issues per sprint
    subprocess_calls: list = []

    def tracking_subprocess(args, **kwargs):
        subprocess_calls.append([str(a) for a in args])
        # Should not be reached when mirror is available
        raise AssertionError("subprocess.run should not be called when mirror is populated")

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch("subprocess.run", side_effect=tracking_subprocess):
            result = gc.latest_active_sprint("owner/repo")

    assert result == 42
    assert subprocess_calls == [], (
        f"Expected 0 subprocess calls on mirror hit, got {len(subprocess_calls)}: {subprocess_calls}"
    )


def test_ac2_zero_calls_even_with_list_sprints_fallback():
    """AC2: no gh subprocess calls even when mirror is hit but no open issues
    (must use list_sprints function, not subprocess)."""
    mirror_data = [_mock_issue(1, "closed", 40)]
    grouped = {40: mirror_data}  # grouping returns list of issues per sprint
    subprocess_calls: list = []

    def tracking_subprocess(args, **kwargs):
        subprocess_calls.append([str(a) for a in args])
        # Should not be reached when mirror is available
        raise AssertionError("subprocess.run should not be called on mirror hit")

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch.object(gc, "list_sprints", return_value=[40, 41]):
            with patch("subprocess.run", side_effect=tracking_subprocess):
                result = gc.latest_active_sprint("owner/repo")

    assert result == 41
    assert subprocess_calls == [], (
        f"Expected 0 subprocess calls even with list_sprints fallback, got {subprocess_calls}"
    )


def test_ac2_mirror_iteration_has_no_subprocess():
    """AC2: the mirror-based loop through group_issues_by_sprint does not invoke subprocess."""
    # Verify that the source code path for mirror lookup does not call subprocess
    # by mocking the entire issue-grouping operation without subprocess.
    mirror_data = [
        _mock_issue(100, "open", 50),
        _mock_issue(101, "open", 50),
    ]
    grouped = {50: mirror_data}
    subprocess_calls: list = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append(args)
        return MagicMock()

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch("subprocess.run", side_effect=counting_subprocess):
            gc.latest_active_sprint("owner/repo")

    assert subprocess_calls == [], (
        f"Mirror-based path should make zero subprocess calls, got {len(subprocess_calls)}"
    )


# ── AC3: fallback to GraphQL when mirror empty ──────────────────────────

def test_ac3_fallback_when_mirror_none():
    """AC3: latest_active_sprint falls back to gh issue list (GraphQL)
    only when group_issues_by_sprint returns None."""
    subprocess_calls: list = []
    gh_issue_list_called = []

    def tracking_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        subprocess_calls.append(cmd)
        if "issue" in cmd and "list" in cmd:
            gh_issue_list_called.append(cmd)
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps([
            {"number": 1, "labels": [{"name": "sprint-30"}]}
        ])
        r.stderr = ""
        return r

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=None):
        with patch("subprocess.run", side_effect=tracking_subprocess):
            result = gc.latest_active_sprint("owner/repo")

    assert result == 30, f"Expected 30 from fallback, got {result}"
    assert len(subprocess_calls) > 0, "Expected subprocess calls on mirror miss"
    assert len(gh_issue_list_called) >= 1, (
        "Expected 'gh issue list' (GraphQL) fallback call"
    )


def test_ac3_no_fallback_when_mirror_populated():
    """AC3: no fallback to GraphQL when mirror is populated (even if empty list)."""
    grouped = {}  # Empty but not None — mirror IS populated
    subprocess_calls: list = []

    def should_not_call(args, **kwargs):
        subprocess_calls.append(args)
        raise AssertionError("Should not fall back to GraphQL when mirror is populated")

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch.object(gc, "list_sprints", return_value=[]):
            with patch("subprocess.run", side_effect=should_not_call):
                result = gc.latest_active_sprint("owner/repo")

    assert result is None, "Expected None when no active sprints and empty list_sprints"
    assert subprocess_calls == [], (
        f"Should not call GraphQL fallback when mirror is empty dict (populated), got {subprocess_calls}"
    )


# ── AC4: parity between mirror and GraphQL paths ─────────────────────────

def test_ac4_parity_with_gh_path():
    """AC4: latest_active_sprint returns the same result from mirror path
    as from the legacy GraphQL path when tested against the same fixture data."""
    fixture = [
        _mock_issue(1, "open", 40),
        _mock_issue(2, "closed", 40),
        _mock_issue(3, "open", 41),
        _mock_issue(4, "closed", 42),
    ]
    grouped = {
        40: [fixture[0], fixture[1]],
        41: [fixture[2]],
        42: [fixture[3]],
    }

    # Path 1: Mirror
    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        mirror_result = gc.latest_active_sprint("owner/repo")

    # Path 2: GraphQL fallback (same fixture data)
    # The GraphQL path extracts all open issues and finds the max sprint label
    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=None):
        with patch.object(gc, "_json") as mock_json:
            # _json returns a list of issues with open state
            open_issues = [i for i in fixture if i["state"] == "open"]
            mock_json.return_value = open_issues
            graphql_result = gc.latest_active_sprint("owner/repo")

    # Both should return the highest sprint with an open issue
    assert mirror_result == graphql_result, (
        f"Parity broken: mirror={mirror_result}, graphql={graphql_result}"
    )
    assert mirror_result == 41, f"Expected 41, got {mirror_result}"


def test_ac4_parity_all_closed():
    """AC4: parity when all issues are closed (both paths fall back to list_sprints)."""
    fixture = [
        _mock_issue(1, "closed", 10),
        _mock_issue(2, "closed", 10),
        _mock_issue(3, "closed", 11),
    ]
    grouped = {
        10: [fixture[0], fixture[1]],
        11: [fixture[2]],
    }
    sprint_list = [10, 11, 12]

    # Mirror path
    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch.object(gc, "list_sprints", return_value=sprint_list):
            mirror_result = gc.latest_active_sprint("owner/repo")

    # GraphQL path — empty open issues list forces fallback to list_sprints
    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=None):
        with patch.object(gc, "_json", return_value=[]):  # No open issues
            with patch.object(gc, "list_sprints", return_value=sprint_list):
                graphql_result = gc.latest_active_sprint("owner/repo")

    assert mirror_result == graphql_result == 12, (
        f"Parity broken on all-closed: mirror={mirror_result}, graphql={graphql_result}"
    )


def test_ac4_parity_empty_result():
    """AC4: parity when there are no sprints at all."""
    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value={}):
        with patch.object(gc, "list_sprints", return_value=[]):
            mirror_result = gc.latest_active_sprint("owner/repo")

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=None):
        with patch.object(gc, "_json", return_value=[]):
            with patch.object(gc, "list_sprints", return_value=[]):
                graphql_result = gc.latest_active_sprint("owner/repo")

    assert mirror_result == graphql_result == None, (
        f"Parity broken on empty: mirror={mirror_result}, graphql={graphql_result}"
    )

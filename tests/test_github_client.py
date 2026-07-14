"""Tests for issue #1783 — latest_active_sprint off GraphQL (github_client.py).

AC mapping:
AC1  latest_active_sprint derives active sprint from group_issues_by_sprint():
     highest sprint number with at least one open (non-closed) issue
AC2  When mirror populated, latest_active_sprint makes zero gh subprocess calls
AC3  latest_active_sprint falls back to gh issue list when mirror is empty/unpopulated
     (group_issues_by_sprint returns None)
AC4  Parity test: latest_active_sprint returns same sprint label against a fixture
     mirror as the legacy gh path returns against the same fixture data
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

import github_client  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _issue(number: int, sprint: int, state: str = "open") -> dict:
    return {
        "number": number,
        "title": f"Issue #{number}",
        "state": state,
        "labels": [{"name": f"sprint-{sprint}"}],
        "column": "done" if state == "closed" else "backlog",
    }


def _grouped(*specs) -> dict:
    """Build a group_issues_by_sprint dict.

    Each spec is (sprint_num, list_of_states), e.g. (97, ["open", "closed"]).
    """
    result: dict[int, list] = {}
    for sprint_num, states in specs:
        result[sprint_num] = [
            _issue(sprint_num * 1000 + i, sprint_num, s)
            for i, s in enumerate(states)
        ]
    return result


def _fake_subprocess_json(payload) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps(payload)
    return r


# ── AC1: derives from group_issues_by_sprint ──────────────────────────────────

def test_ac1_highest_sprint_with_open_issue():
    """AC1: returns highest sprint that has at least one open issue."""
    grouped = _grouped((97, ["open", "closed"]), (98, ["closed"]))
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        result = github_client.latest_active_sprint("owner/repo")
    assert result == 97, f"Expected sprint 97 (has open issue), got {result}"


def test_ac1_highest_when_multiple_active():
    """AC1: when multiple sprints have open issues, returns highest."""
    grouped = _grouped((95, ["open"]), (96, ["open"]), (97, ["open"]))
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        result = github_client.latest_active_sprint("owner/repo")
    assert result == 97


def test_ac1_ignores_sprints_with_only_closed_issues():
    """AC1: sprint where all issues are closed is not selected as active."""
    grouped = _grouped((96, ["closed"]), (97, ["open"]))
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        result = github_client.latest_active_sprint("owner/repo")
    assert result == 97, f"Sprint 96 is all closed; expected 97, got {result}"


def test_ac1_all_sprints_closed_falls_to_list_sprints():
    """AC1: when every sprint's issues are closed, falls to list_sprints."""
    grouped = _grouped((90, ["closed"]), (91, ["closed"]))
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        with patch.object(github_client, "list_sprints", return_value=[90, 91]) as mock_ls:
            result = github_client.latest_active_sprint("owner/repo")
    mock_ls.assert_called_once()
    assert result == 91, f"Expected highest sprint 91 from list_sprints, got {result}"


# ── AC2: zero subprocess calls when mirror populated ─────────────────────────

def test_ac2_zero_gh_subprocess_calls_when_mirror_populated():
    """AC2: when group_issues_by_sprint returns data, no gh subprocess is spawned."""
    grouped = _grouped((97, ["open"]), (98, ["closed"]))
    subprocess_calls: list = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json([])

    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = github_client.latest_active_sprint("owner/repo")

    gh_calls = [c for c in subprocess_calls if "gh" in c]
    assert gh_calls == [], (
        f"Expected 0 gh subprocess calls when mirror populated, got {len(gh_calls)}: {gh_calls}"
    )
    assert result == 97


def test_ac2_empty_grouped_dict_no_gh_subprocess():
    """AC2: even with empty grouped dict (no sprint issues in mirror), no gh issue list call."""
    subprocess_calls: list = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json([])

    github_client.invalidate()
    # group_issues_by_sprint returns {} (mirror available, but no sprint-labeled issues)
    with patch.object(github_client, "group_issues_by_sprint", return_value={}):
        with patch.object(github_client, "list_sprints", return_value=[]):
            with patch("subprocess.run", side_effect=counting_subprocess):
                github_client.latest_active_sprint("owner/repo")

    issue_list_calls = [
        c for c in subprocess_calls
        if "issue" in c and "list" in c
    ]
    assert issue_list_calls == [], (
        f"Expected 0 gh issue list calls when mirror dict available, got: {issue_list_calls}"
    )


# ── AC3: fallback when mirror empty/unpopulated ───────────────────────────────

def test_ac3_fallback_when_group_returns_none():
    """AC3: when group_issues_by_sprint returns None, falls back to gh issue list."""
    subprocess_calls: list = []

    def fake_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json([{"labels": [{"name": "sprint-99"}]}])

    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=None):
        with patch("subprocess.run", side_effect=fake_subprocess):
            result = github_client.latest_active_sprint("owner/repo")

    issue_list_calls = [c for c in subprocess_calls if "issue" in c and "list" in c]
    assert len(issue_list_calls) >= 1, (
        f"Expected ≥1 gh issue list call on mirror miss, got {len(issue_list_calls)}"
    )
    assert result == 99


def test_ac3_fallback_selects_highest_sprint():
    """AC3: legacy gh path returns the highest sprint from open issues."""
    open_issues_payload = [
        {"labels": [{"name": "sprint-97"}]},
        {"labels": [{"name": "sprint-100"}]},
        {"labels": [{"name": "sprint-98"}]},
    ]

    def fake_subprocess(args, **kwargs):
        return _fake_subprocess_json(open_issues_payload)

    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=None):
        with patch("subprocess.run", side_effect=fake_subprocess):
            result = github_client.latest_active_sprint("owner/repo")

    assert result == 100, f"Expected sprint 100 (highest with open issues), got {result}"


def test_ac3_fallback_empty_open_issues_uses_list_sprints():
    """AC3: legacy path with no open sprint issues falls to list_sprints."""
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=None):
        with patch("subprocess.run", return_value=_fake_subprocess_json([])):
            with patch.object(github_client, "list_sprints", return_value=[90, 91]) as mock_ls:
                result = github_client.latest_active_sprint("owner/repo")
    mock_ls.assert_called()
    assert result == 91


# ── AC4: parity — mirror path returns same result as legacy gh path ───────────

def test_ac4_mirror_and_legacy_return_same_sprint():
    """AC4: against the same fixture data, mirror path and legacy gh path agree."""
    # Fixture: sprint-97 has open issues; sprint-98 is all closed
    grouped = _grouped((97, ["open"]), (98, ["closed"]))

    # Mirror path
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        mirror_result = github_client.latest_active_sprint("owner/repo")

    # Legacy gh path: same data — sprint-97 has open issues
    open_issues_for_sprint_97 = [{"labels": [{"name": "sprint-97"}]}]

    def fake_subprocess_legacy(args, **kwargs):
        return _fake_subprocess_json(open_issues_for_sprint_97)

    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=None):
        with patch("subprocess.run", side_effect=fake_subprocess_legacy):
            legacy_result = github_client.latest_active_sprint("owner/repo")

    assert mirror_result == legacy_result, (
        f"Mirror path returned sprint-{mirror_result}, legacy path returned sprint-{legacy_result}; "
        "they must agree on the same fixture data"
    )
    assert mirror_result == 97


def test_ac4_parity_with_97_open_98_closed_fixture():
    """AC4: UAT test step 1 — sprint-97 open, sprint-98 all closed → returns sprint-97."""
    # Sprint 97: open issues; Sprint 98: all closed
    grouped = _grouped((97, ["open", "open"]), (98, ["closed", "closed"]))
    github_client.invalidate()
    with patch.object(github_client, "group_issues_by_sprint", return_value=grouped):
        result = github_client.latest_active_sprint("owner/repo")
    assert result == 97, f"Expected sprint 97 (has open issues; sprint 98 all closed), got {result}"

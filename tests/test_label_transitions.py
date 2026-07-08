"""Tests for issue #1783 — move _get_issue_labels off GraphQL (label_transitions.py).

AC mapping:
AC5  _get_issue_labels no longer calls gh issue view --json labels;
     uses REST endpoint (gh api repos/{repo}/issues/{number}) or mirror
AC6  All existing state-machine transition tests pass without modification
     (confirmed by smoke-importing and exercising transition())
AC7  No new GraphQL calls introduced by either change
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

from services.sprint_manager import label_transitions as lt  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _rest_response(labels: list[str]) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps({"labels": [{"name": n} for n in labels]})
    r.stderr = ""
    return r


def _mirror_issue(number: int, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": f"Issue #{number}",
        "state": "open",
        "labels": [{"name": n} for n in labels],
    }


# ── AC5: no gh issue view --json labels ──────────────────────────────────────

def test_ac5_no_gh_issue_view_on_mirror_miss():
    """AC5: when mirror has no record, _get_issue_labels does NOT call gh issue view."""
    gh_issue_view_calls: list = []

    def counting_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "issue" in cmd and "view" in cmd:
            gh_issue_view_calls.append(cmd)
        # Respond to gh api call
        return _rest_response(["in-progress"])

    with patch("github_client._mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=counting_subprocess):
            lt._get_issue_labels(123, "owner/repo")

    assert gh_issue_view_calls == [], (
        f"_get_issue_labels must not call 'gh issue view'; "
        f"got {len(gh_issue_view_calls)} call(s): {gh_issue_view_calls}"
    )


def test_ac5_mirror_hit_returns_labels_without_subprocess():
    """AC5: when mirror has the issue, labels come from mirror — no subprocess call."""
    issue = _mirror_issue(456, ["in-progress", "sprint-99"])
    subprocess_calls: list = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append([str(a) for a in args])
        return _rest_response(["should-not-appear"])

    with patch("github_client._mirror_issue", return_value=issue):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = lt._get_issue_labels(456, "owner/repo")

    assert "in-progress" in result, f"Expected 'in-progress' from mirror, got {result}"
    assert "sprint-99" in result, f"Expected 'sprint-99' from mirror, got {result}"
    assert subprocess_calls == [], (
        f"Expected 0 subprocess calls on mirror hit; got {len(subprocess_calls)}: {subprocess_calls}"
    )


def test_ac5_mirror_miss_uses_rest_endpoint():
    """AC5: on mirror miss, _get_issue_labels calls REST (gh api), not GraphQL (gh issue view)."""
    graphql_calls: list = []
    rest_calls: list = []

    def counting_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "gh" in cmd:
            if "issue" in cmd and "view" in cmd:
                graphql_calls.append(cmd)
            elif "api" in cmd:
                rest_calls.append(cmd)
        return _rest_response(["SIT"])

    with patch("github_client._mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = lt._get_issue_labels(789, "owner/repo")

    assert graphql_calls == [], (
        f"Expected 0 GraphQL 'gh issue view' calls, got: {graphql_calls}"
    )
    assert len(rest_calls) >= 1, (
        f"Expected ≥1 REST 'gh api' call on mirror miss, got: {rest_calls}"
    )
    assert "SIT" in result


def test_ac5_mirror_miss_returns_correct_labels():
    """AC5: labels from REST endpoint are returned correctly on mirror miss."""
    with patch("github_client._mirror_issue", return_value=None):
        with patch("subprocess.run", return_value=_rest_response(["UAT", "sprint-42"])):
            result = lt._get_issue_labels(999, "owner/repo")

    assert result == {"UAT", "sprint-42"}, f"Unexpected label set: {result}"


def test_ac5_exception_returns_empty_set():
    """AC5: if both mirror and REST fail, _get_issue_labels returns empty set (not raises)."""
    with patch("github_client._mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=Exception("network down")):
            result = lt._get_issue_labels(111, "owner/repo")

    assert result == set(), f"Expected empty set on failure, got {result}"


def test_ac5_signature_unchanged():
    """AC5: _get_issue_labels still accepts (issue_num, repo_name=None) signature."""
    sig = inspect.signature(lt._get_issue_labels)
    params = list(sig.parameters)
    assert params[0] == "issue_num", f"First param must be 'issue_num', got {params[0]}"
    assert "repo_name" in params, f"'repo_name' param missing; got {params}"


# ── AC6: state-machine transition behavior unchanged ─────────────────────────

def test_ac6_state_machine_importable():
    """AC6: state_machine imports cleanly after the label_transitions change."""
    from services.sprint_manager.state_machine import (  # noqa: PLC0415
        TicketState, TransitionError, STATE_LABELS, STATUS_LABELS, transition,
    )
    assert callable(transition)
    assert TicketState.IN_PROGRESS in STATE_LABELS
    assert "in-progress" in STATUS_LABELS


def test_ac6_transition_in_progress_emits_label_edit():
    """AC6: transition() to IN_PROGRESS still issues a gh issue edit (behavioral check)."""
    from services.sprint_manager.state_machine import TicketState, transition  # noqa: PLC0415

    calls: list = []

    def fake_subprocess(args, **kwargs):
        calls.append([str(a) for a in args])
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"labels": [{"name": "backlog"}, {"name": "sprint-99"}]})
        r.stderr = ""
        return r

    with patch("services.sprint_manager.state_machine.subprocess.run",
               side_effect=fake_subprocess):
        transition(123, TicketState.IN_PROGRESS, actor="test", repo="owner/repo")

    edit_calls = [c for c in calls if "issue" in c and "edit" in c]
    assert len(edit_calls) >= 1, (
        f"Expected ≥1 gh issue edit call from transition(); got {len(edit_calls)}"
    )


def test_ac6_transition_sit_removes_in_progress():
    """AC6: transition() to SIT correctly computes label diff (removes in-progress, adds SIT)."""
    from services.sprint_manager.state_machine import TicketState, transition  # noqa: PLC0415

    remove_labels: list = []
    add_labels: list = []

    def fake_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "--remove-label" in cmd:
            idx = cmd.index("--remove-label")
            remove_labels.append(cmd[idx + 1])
        if "--add-label" in cmd:
            idx = cmd.index("--add-label")
            add_labels.append(cmd[idx + 1])
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "labels": [{"name": "in-progress"}, {"name": "sprint-42"}],
        })
        r.stderr = ""
        return r

    with patch("services.sprint_manager.state_machine.subprocess.run",
               side_effect=fake_subprocess):
        transition(456, TicketState.SIT, actor="test", repo="owner/repo")

    assert "in-progress" in " ".join(remove_labels), (
        f"Expected 'in-progress' in removed labels, got {remove_labels}"
    )
    assert "SIT" in " ".join(add_labels), (
        f"Expected 'SIT' in added labels, got {add_labels}"
    )


# ── AC7: no new GraphQL calls introduced ─────────────────────────────────────

def test_ac7_source_of_get_issue_labels_has_no_issue_view():
    """AC7: _get_issue_labels source must not contain 'gh issue view' (GraphQL)."""
    src = inspect.getsource(lt._get_issue_labels)
    assert '"issue", "view"' not in src, (
        "Source of _get_issue_labels must not contain '\"issue\", \"view\"' — "
        "that routes through GraphQL"
    )
    assert "issue view" not in src, (
        "Source of _get_issue_labels must not contain 'issue view' command string"
    )


def test_ac7_get_issue_labels_rest_call_uses_api_subcommand():
    """AC7: the REST fallback uses 'gh api repos/...' (REST), not GraphQL subcommands."""
    api_calls: list = []

    def checking_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "gh" in cmd and "api" in cmd:
            api_calls.append(cmd)
        elif "gh" in cmd:
            # Any non-api gh call is a potential GraphQL call
            raise AssertionError(
                f"_get_issue_labels made a non-API gh call on mirror miss: {cmd}"
            )
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"labels": []})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=checking_subprocess):
            lt._get_issue_labels(555, "owner/repo")

    assert len(api_calls) >= 1, f"Expected REST 'gh api' call, got none"


def test_ac7_no_new_graphql_in_latest_active_sprint_on_mirror_hit():
    """AC7: latest_active_sprint makes zero GraphQL subprocess calls when mirror populated."""
    import github_client as gc  # noqa: E402

    from services.sprint_manager import label_transitions  # noqa: E402

    grouped = {97: [{"number": 1, "state": "open", "labels": [{"name": "sprint-97"}]}]}
    graphql_calls: list = []

    _GRAPHQL_SUBCOMMANDS = {("issue", "list"), ("issue", "view"), ("label", "list"),
                            ("pr", "list"), ("pr", "view")}

    def counting_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        # Check for GraphQL subcommand pairs
        for pair in _GRAPHQL_SUBCOMMANDS:
            if pair[0] in cmd and pair[1] in cmd:
                graphql_calls.append(cmd)
                break
        r = MagicMock()
        r.returncode = 0
        r.stdout = "[]"
        return r

    gc.invalidate()
    with patch.object(gc, "group_issues_by_sprint", return_value=grouped):
        with patch("subprocess.run", side_effect=counting_subprocess):
            gc.latest_active_sprint("owner/repo")

    assert graphql_calls == [], (
        f"Expected 0 GraphQL subprocess calls when mirror populated, got: {graphql_calls}"
    )

"""Tester AC verification for issue #811: Strip stale status labels when a sprint is re-run.

These tests drive the real ``server.rerun_sprint`` endpoint in-process with
``github_client`` fully mocked, so no labels are mutated on any live GitHub
repo (required by the GitHub repo segregation rule — GITHUB_ISSUE_TEST_REPO is
not configured). The rerun endpoint is a GitHub-mutating action, so it cannot
be exercised over HTTP against the work repo without applying/removing real
labels; in-process verification against the actual implementation is the safe
equivalent.

One AC → one test function (double-underscore naming).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(DASHBOARD_DIR))

import server  # noqa: E402


@pytest.fixture
def rerun(monkeypatch, tmp_path):
    """Run server.rerun_sprint against tmp dirs with GitHub calls captured."""
    monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
    monkeypatch.setattr(server, "_is_sprint_running", lambda root, label: False)
    monkeypatch.setattr(
        server.github_client, "list_labels",
        lambda repo_name=None: [{"name": "sprint-62"}],
    )
    monkeypatch.setattr(
        server.github_client, "get_label_color",
        lambda name, repo_name=None: "0075ca",
    )
    monkeypatch.setattr(server.github_client, "create_label", lambda *a, **kw: None)
    monkeypatch.setattr(server.github_client, "invalidate", lambda *a, **kw: None)

    update_calls: list[dict] = []

    def _capture(issue_num, add=None, remove=None, repo_name=None):
        update_calls.append({
            "issue_num": issue_num,
            "add": list(add or []),
            "remove": list(remove or []),
        })

    monkeypatch.setattr(server.github_client, "update_labels", _capture)

    events: list[dict] = []
    monkeypatch.setattr(server, "_emit_dashboard_event", lambda **kw: events.append(kw))

    state = {"issues": []}
    monkeypatch.setattr(server, "_get_sprint_issues", lambda project, label: state["issues"])

    def run(issues):
        state["issues"] = issues
        body = server.SprintRerunV2Body(ticket_numbers=[], auto_run=False)
        return server.rerun_sprint("sprint-62", "zealchaiwut/commander", body)

    return {"run": run, "calls": update_calls, "events": events}


def _issue(num, labels):
    return {"number": num, "title": f"item-{num}", "labels": [{"name": n} for n in labels]}


def _removed(calls, num):
    for c in calls:
        if c["issue_num"] == num:
            return set(c["remove"])
    raise AssertionError(f"no update_labels call for #{num}")


# --- Acceptance Criteria ---

def test_strip_stale_status_labels_when_sprint_is_re_run__removes_needs_rework(rerun):
    # AC1: re-run removes `needs-rework` from all sprint items
    rerun["run"]([_issue(1, ["sprint-62", "needs-rework"])])
    assert "needs-rework" in _removed(rerun["calls"], 1)


def test_strip_stale_status_labels_when_sprint_is_re_run__removes_in_progress(rerun):
    # AC2: re-run removes `in-progress` from all sprint items
    rerun["run"]([_issue(2, ["sprint-62", "in-progress"])])
    assert "in-progress" in _removed(rerun["calls"], 2)


def test_strip_stale_status_labels_when_sprint_is_re_run__removes_sit_away(rerun):
    # AC3: re-run removes `sit-away` from all sprint items
    rerun["run"]([_issue(3, ["sprint-62", "sit-away"])])
    assert "sit-away" in _removed(rerun["calls"], 3)


def test_strip_stale_status_labels_when_sprint_is_re_run__removes_other_taxonomy(rerun):
    # AC4: other session-state labels in the taxonomy are also stripped
    rerun["run"]([_issue(4, ["sprint-62", "tester-rejected", "need-rework"])])
    removed = _removed(rerun["calls"], 4)
    assert {"tester-rejected", "need-rework"} <= removed


def test_strip_stale_status_labels_when_sprint_is_re_run__preserves_intrinsic(rerun):
    # AC5: non-session labels (bug, priority-high, size-M) are preserved
    rerun["run"]([_issue(5, ["sprint-62", "in-progress", "bug", "priority-high", "size-M"])])
    removed = _removed(rerun["calls"], 5)
    assert removed.isdisjoint({"bug", "priority-high", "size-M"})
    assert "in-progress" in removed  # the stale one still went


def test_strip_stale_status_labels_when_sprint_is_re_run__audit_trail_records_removals(rerun):
    # AC6: audit trail (dashboard event + API result) records which labels were
    # removed from which items, with a timestamped sprint_rerun event.
    result = rerun["run"]([
        _issue(1, ["sprint-62", "needs-rework"]),
        _issue(2, ["sprint-62", "in-progress", "bug"]),
    ])
    evts = [e for e in rerun["events"] if e.get("type") == "sprint_rerun"]
    assert evts, "expected a sprint_rerun dashboard event"
    by_item = {e["issue_num"]: e["removed_labels"]
               for e in evts[-1]["detail"]["stripped_labels"]}
    assert by_item == {1: ["needs-rework"], 2: ["in-progress"]}
    # API response carries the same audit trail
    res_by_item = {e["issue_num"]: e["removed_labels"] for e in result["stripped_labels"]}
    assert res_by_item == {1: ["needs-rework"], 2: ["in-progress"]}


def test_strip_stale_status_labels_when_sprint_is_re_run__no_stale_no_error(rerun):
    # AC7: a clean sprint re-runs without error and with an empty audit trail
    result = rerun["run"]([_issue(8, ["sprint-62", "bug"])])
    assert "errors" not in result
    assert result["stripped_labels"] == []
    assert _removed(rerun["calls"], 8) == {"sprint-62"}  # only the parent label swaps

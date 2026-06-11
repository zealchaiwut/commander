"""Tests for issue #811: Strip stale status labels when a sprint is re-run.

When a sprint is re-run, items may still carry session-state labels (e.g.
``needs-rework``, ``in-progress``, ``sit-away``) left over from the previous
run. No active work session exists yet at the start of a re-run, so those
labels are stale and misrepresent what needs attention. The re-run must strip
them while preserving labels that are intrinsic to the ticket.

Acceptance Criteria → tests:
  AC1 - Re-run removes the ``needs-rework`` label from all sprint items.
  AC2 - Re-run removes the ``in-progress`` label from all sprint items.
  AC3 - Re-run removes the ``sit-away`` label from all sprint items.
  AC4 - Any other session-state labels defined in the taxonomy are also stripped.
  AC5 - Labels unrelated to session state (bug, priority-high) are preserved.
  AC6 - A log entry / audit trail records which labels were removed and from
        which items.
  AC7 - If no stale labels exist, re-run completes without error.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── path setup: allow `import server` like the other dashboard tests ──────────
DASHBOARD_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(DASHBOARD_DIR))

import server  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests on the session-state taxonomy + pure helper
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionStateTaxonomy:
    def test_needs_rework_is_session_state(self):
        # AC1
        assert "needs-rework" in server._SESSION_STATE_LABELS

    def test_in_progress_is_session_state(self):
        # AC2
        assert "in-progress" in server._SESSION_STATE_LABELS

    def test_sit_away_is_session_state(self):
        # AC3
        assert "sit-away" in server._SESSION_STATE_LABELS

    def test_other_taxonomy_labels_present(self):
        # AC4 — taxonomy also covers the rework/rejection aliases used elsewhere
        assert {"need-rework", "tester-rejected"} <= set(server._SESSION_STATE_LABELS)

    def test_non_session_labels_not_in_taxonomy(self):
        # AC5 — intrinsic labels must never be classed as session state
        for lbl in ("bug", "priority-high", "size-M", "sprint-62", "enhancement"):
            assert lbl not in server._SESSION_STATE_LABELS


class TestStaleSessionLabelsHelper:
    def test_strips_needs_rework(self):
        # AC1
        assert server._stale_session_labels({"needs-rework", "bug"}) == ["needs-rework"]

    def test_strips_in_progress(self):
        # AC2
        assert server._stale_session_labels({"in-progress"}) == ["in-progress"]

    def test_strips_sit_away(self):
        # AC3
        assert server._stale_session_labels({"sit-away"}) == ["sit-away"]

    def test_strips_multiple_taxonomy_labels(self):
        # AC4
        got = server._stale_session_labels(
            {"in-progress", "sit-away", "tester-rejected", "need-rework"}
        )
        assert got == ["in-progress", "need-rework", "sit-away", "tester-rejected"]

    def test_preserves_non_session_labels(self):
        # AC5 — only the stale label comes back; intrinsic labels are not returned
        got = server._stale_session_labels(
            {"bug", "priority-high", "size-M", "sprint-62", "in-progress"}
        )
        assert got == ["in-progress"]

    def test_no_stale_labels_returns_empty(self):
        # AC7 — nothing to strip → empty list, no error
        assert server._stale_session_labels({"bug", "priority-high"}) == []
        assert server._stale_session_labels(set()) == []


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end behaviour of the rerun_sprint endpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def rerun_env(monkeypatch, tmp_path):
    """Wire server.rerun_sprint to run against tmp dirs + captured GitHub calls.

    Returns a dict with:
      - run(issues): call rerun_sprint for the given issue list, returns result
      - update_calls: list of (issue_num, add, remove) captured per ticket
      - events: list of dashboard events emitted
    """
    monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)

    # No sprint is running in a fresh tmp project.
    monkeypatch.setattr(server, "_is_sprint_running", lambda root, label: False)

    # Existing labels on the repo — only the parent sprint label exists, so the
    # next sub-label is sprint-62.1.
    monkeypatch.setattr(
        server.github_client, "list_labels",
        lambda repo_name=None: [{"name": "sprint-62"}],
    )
    monkeypatch.setattr(
        server.github_client, "get_label_color",
        lambda name, repo_name=None: "0075ca",
    )
    monkeypatch.setattr(
        server.github_client, "create_label",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(server.github_client, "invalidate", lambda *a, **kw: None)

    update_calls: list[dict] = []

    def _capture_update_labels(issue_num, add=None, remove=None, repo_name=None):
        update_calls.append({
            "issue_num": issue_num,
            "add": list(add or []),
            "remove": list(remove or []),
        })

    monkeypatch.setattr(server.github_client, "update_labels", _capture_update_labels)

    events: list[dict] = []
    monkeypatch.setattr(
        server, "_emit_dashboard_event",
        lambda **kw: events.append(kw),
    )

    state = {"issues": []}
    monkeypatch.setattr(
        server, "_get_sprint_issues",
        lambda project, sprint_label: state["issues"],
    )

    def run(issues, ticket_numbers=None):
        state["issues"] = issues
        body = server.SprintRerunV2Body(
            ticket_numbers=ticket_numbers or [], auto_run=False
        )
        return server.rerun_sprint("sprint-62", "zealchaiwut/commander", body)

    return {"run": run, "update_calls": update_calls, "events": events}


def _issue(num, title, labels):
    return {"number": num, "title": title, "labels": [{"name": n} for n in labels]}


def _removed_for(update_calls, issue_num):
    for call in update_calls:
        if call["issue_num"] == issue_num:
            return set(call["remove"])
    raise AssertionError(f"no update_labels call captured for #{issue_num}")


class TestRerunStripsStaleLabels:
    def test_needs_rework_removed_on_rerun(self, rerun_env):
        # AC1
        rerun_env["run"]([_issue(1, "rework item", ["sprint-62", "needs-rework"])])
        assert "needs-rework" in _removed_for(rerun_env["update_calls"], 1)

    def test_in_progress_removed_on_rerun(self, rerun_env):
        # AC2
        rerun_env["run"]([_issue(2, "wip item", ["sprint-62", "in-progress"])])
        assert "in-progress" in _removed_for(rerun_env["update_calls"], 2)

    def test_sit_away_removed_on_rerun(self, rerun_env):
        # AC3
        rerun_env["run"]([_issue(3, "away item", ["sprint-62", "sit-away"])])
        assert "sit-away" in _removed_for(rerun_env["update_calls"], 3)

    def test_other_taxonomy_label_removed_on_rerun(self, rerun_env):
        # AC4 — tester-rejected is also a session-state label
        rerun_env["run"]([_issue(4, "rejected item", ["sprint-62", "tester-rejected"])])
        assert "tester-rejected" in _removed_for(rerun_env["update_calls"], 4)

    def test_non_session_labels_preserved(self, rerun_env):
        # AC5 — bug / priority-high are never in the remove set
        rerun_env["run"]([
            _issue(5, "buggy item",
                   ["sprint-62", "in-progress", "bug", "priority-high"]),
        ])
        removed = _removed_for(rerun_env["update_calls"], 5)
        assert "bug" not in removed
        assert "priority-high" not in removed
        # ...but the stale session label still went away.
        assert "in-progress" in removed

    def test_all_items_cleaned_in_one_rerun(self, rerun_env):
        # AC1+AC2+AC3 across every item in the sprint
        rerun_env["run"]([
            _issue(1, "a", ["sprint-62", "needs-rework"]),
            _issue(2, "b", ["sprint-62", "in-progress"]),
            _issue(3, "c", ["sprint-62", "sit-away"]),
        ])
        calls = rerun_env["update_calls"]
        assert "needs-rework" in _removed_for(calls, 1)
        assert "in-progress" in _removed_for(calls, 2)
        assert "sit-away" in _removed_for(calls, 3)


class TestRerunAuditTrail:
    def test_event_records_removed_labels_and_items(self, rerun_env):
        # AC6 — the emitted dashboard event names which labels were removed
        # from which items.
        rerun_env["run"]([
            _issue(1, "a", ["sprint-62", "needs-rework"]),
            _issue(2, "b", ["sprint-62", "in-progress", "bug"]),
        ])
        rerun_events = [e for e in rerun_env["events"] if e.get("type") == "sprint_rerun"]
        assert rerun_events, "expected a sprint_rerun dashboard event"
        stripped = rerun_events[-1]["detail"]["stripped_labels"]
        by_item = {e["issue_num"]: e["removed_labels"] for e in stripped}
        assert by_item == {1: ["needs-rework"], 2: ["in-progress"]}

    def test_result_includes_stripped_labels(self, rerun_env):
        # AC6 — the API response also carries the audit trail.
        result = rerun_env["run"]([
            _issue(7, "g", ["sprint-62", "sit-away", "priority-high"]),
        ])
        assert {"issue_num": 7, "removed_labels": ["sit-away"]} in result["stripped_labels"]


class TestRerunWithNoStaleLabels:
    def test_no_stale_labels_completes_without_error(self, rerun_env):
        # AC7 — a clean sprint re-runs with no errors and an empty audit trail.
        result = rerun_env["run"]([
            _issue(8, "clean", ["sprint-62", "bug"]),
        ])
        assert "errors" not in result
        assert result["stripped_labels"] == []
        # The item still moved to the sub-sprint; only the parent label is swapped.
        removed = _removed_for(rerun_env["update_calls"], 8)
        assert removed == {"sprint-62"}

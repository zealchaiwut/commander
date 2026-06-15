"""
Test suite for issue #862: Pending sign-off state with Approve/Reject for
planned sprints.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC1  A newly planned sprint enters the PENDING SIGN-OFF state.
AC2  Run Sprint is blocked server-side while a sprint is pending sign-off
     (the badge/disabled button in the UI is driven by this same state).
AC3  While pending the sprint stays editable — sign-off state does not touch
     ticket membership/order/capacity (the signoff field is orthogonal to the
     tickets array).
AC5  Confirming approval transitions the sprint to the ready/planned state and
     clears the pending flag (Run Sprint becomes runnable again).
AC6  Rejecting dissolves the sprint (deletes the GitHub label, returning all
     tickets to the backlog) and removes the local sprint files.
AC7  On approval the plan file records the approver's identity and an ISO-8601
     approval timestamp.
AC8  On approval an entry is appended to the activity log (actor + when).
AC9  On rejection the activity log records the rejection event (actor + when).
AC10 The pending state persists across reloads (it is file-backed, never
     silently advanced on read).

The board data contract (sprint_signoff map consumed by the front-end badge /
disabled Run button) is covered by test_sprint_management_exposes_signoff.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Import the dashboard monolith + the new sign-off service the same way the
# existing sprint-plan suite does (issue #441).
_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

import server  # noqa: E402
from routers import signoff_service  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temp project root wired into server._project_root_path."""
    root = tmp_path / "proj"
    (root / ".commander" / "sprints").mkdir(parents=True)
    monkeypatch.setattr(server, "_project_root_path", lambda repo: root)
    return root


@pytest.fixture
def captured_events(monkeypatch):
    """Capture every activity-log event recorded during a test."""
    events = []

    def _fake_record_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(server.db, "record_event", _fake_record_event)
    return events


def _plan(root, label):
    return json.loads(
        server._sprint_plan_path(root, label).read_text(encoding="utf-8")
    )


# ── AC1: a newly planned sprint enters PENDING SIGN-OFF ──────────────────────

def test_ac1_planned_sprint_is_pending_signoff(project):
    """Writing a plan with a pending sign-off marker reads back as 'pending'."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    assert server._sprint_signoff_state(project, "sprint-7") == "pending"


def test_ac1_legacy_sprint_without_signoff_is_none(project):
    """A pre-existing plan with no signoff key has no gate (None) — back-compat."""
    server._plan_json_set_state(project, "sprint-3", "planned")
    assert server._sprint_signoff_state(project, "sprint-3") is None


def test_ac1_no_plan_file_is_none(project):
    """A label with no plan.json at all returns None, never 'pending'."""
    assert server._sprint_signoff_state(project, "sprint-99") is None


# ── AC2: Run Sprint blocked while pending ────────────────────────────────────

def test_ac2_run_guard_blocks_pending_sprint(project):
    """_assert_sprint_signed_off raises 409 while the sprint is pending."""
    from fastapi import HTTPException

    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    with pytest.raises(HTTPException) as exc:
        server._assert_sprint_signed_off(project, "sprint-7")
    assert exc.value.status_code == 409


def test_ac2_run_guard_allows_approved_sprint(project):
    """Once approved, the run guard no longer blocks (no exception)."""
    server._plan_json_set_state(
        project, "sprint-7", "planned",
        signoff={"state": "approved", "approver": "alice",
                 "approved_at": "2026-06-13T00:00:00+00:00"},
    )
    # Should not raise.
    server._assert_sprint_signed_off(project, "sprint-7")


def test_ac2_run_guard_allows_sprint_without_signoff(project):
    """Sprints predating this feature (no signoff key) are never blocked."""
    server._plan_json_set_state(project, "sprint-2", "planned")
    server._assert_sprint_signed_off(project, "sprint-2")  # no raise


# ── AC3: pending sprint stays editable (signoff orthogonal to tickets) ───────

def test_ac3_editing_tickets_preserves_pending_state(project):
    """Re-saving the ticket order must not clear the pending sign-off marker."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    # Simulate the existing reorder/plan-save path (issue #441) updating tickets.
    existing = server._read_plan_json(project, "sprint-7") or {}
    existing["tickets"] = [3, 1, 2]
    server._write_plan_json(project, "sprint-7", existing)

    assert server._sprint_signoff_state(project, "sprint-7") == "pending"
    assert _plan(project, "sprint-7")["tickets"] == [3, 1, 2]


# ── AC5 + AC7 + AC8: approve transitions state, records approver/ts + activity

def test_ac5_approve_clears_pending(project, captured_events):
    """Approval flips the sign-off state from pending to approved."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    signoff_service.approve_sprint("owner/repo", "sprint-7")
    assert server._sprint_signoff_state(project, "sprint-7") == "approved"


def test_ac7_approve_records_approver_and_iso_timestamp(project, captured_events):
    """The plan file gains approver identity + a valid ISO-8601 timestamp."""
    monkey_actor = "reviewer-bob"
    import os
    os.environ["COMMANDER_USER"] = monkey_actor
    try:
        server._plan_json_set_state(
            project, "sprint-7", "draft", signoff={"state": "pending"}
        )
        signoff_service.approve_sprint("owner/repo", "sprint-7")
    finally:
        del os.environ["COMMANDER_USER"]

    signoff = _plan(project, "sprint-7")["signoff"]
    assert signoff["state"] == "approved"
    assert signoff["approver"] == monkey_actor
    # Must parse as ISO-8601.
    datetime.fromisoformat(signoff["approved_at"])


def test_ac8_approve_appends_activity_log_entry(project, captured_events):
    """Approval records an activity event naming who approved and when."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    signoff_service.approve_sprint("owner/repo", "sprint-7")

    approvals = [e for e in captured_events if e.get("type") == "sprint_signoff_approved"]
    assert len(approvals) == 1
    ev = approvals[0]
    assert ev["target"] == "sprint-7"
    assert ev["detail"]["approver"]
    datetime.fromisoformat(ev["detail"]["approved_at"])


def test_approve_rejects_sprint_not_pending(project, captured_events):
    """Approving a sprint with no pending sign-off is a 409 (nothing to sign)."""
    from fastapi import HTTPException

    server._plan_json_set_state(project, "sprint-7", "planned")
    with pytest.raises(HTTPException) as exc:
        signoff_service.approve_sprint("owner/repo", "sprint-7")
    assert exc.value.status_code == 409


# ── AC6 + AC9: reject dissolves the sprint + records activity ────────────────

@pytest.fixture
def fake_gh(monkeypatch):
    """Capture github_client.delete_label calls instead of hitting GitHub."""
    calls = []
    monkeypatch.setattr(
        server.github_client, "delete_label",
        lambda label, repo_name=None: calls.append((label, repo_name)),
    )
    return calls


def test_ac6_reject_dissolves_sprint_label(project, fake_gh, captured_events):
    """Rejection deletes the sprint label (tickets fall back to the backlog)."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    signoff_service.reject_sprint("owner/repo", "sprint-7")
    assert ("sprint-7", "owner/repo") in fake_gh


def test_ac6_reject_removes_local_sprint_files(project, fake_gh, captured_events):
    """Rejection cleans up the local plan/goal/state files for the sprint."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    server._sprint_goal_path(project, "sprint-7").write_text("goal", encoding="utf-8")
    server._sprint_json_write(
        server._sprint_json_path(project, "sprint-7"), {"label": "sprint-7"}
    )
    assert server._sprint_plan_path(project, "sprint-7").exists()

    signoff_service.reject_sprint("owner/repo", "sprint-7")

    assert not server._sprint_plan_path(project, "sprint-7").exists()
    assert not server._sprint_goal_path(project, "sprint-7").exists()
    assert not server._sprint_json_path(project, "sprint-7").exists()


def test_ac9_reject_appends_activity_log_entry(project, fake_gh, captured_events):
    """Rejection records an activity event with actor + timestamp."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    signoff_service.reject_sprint("owner/repo", "sprint-7")

    rejections = [e for e in captured_events if e.get("type") == "sprint_signoff_rejected"]
    assert len(rejections) == 1
    ev = rejections[0]
    assert ev["target"] == "sprint-7"
    assert ev["detail"]["actor"]
    datetime.fromisoformat(ev["detail"]["rejected_at"])


def test_reject_rejects_sprint_not_pending(project, fake_gh, captured_events):
    """Rejecting a sprint that is not pending sign-off is a 409."""
    from fastapi import HTTPException

    server._plan_json_set_state(project, "sprint-7", "planned")
    with pytest.raises(HTTPException) as exc:
        signoff_service.reject_sprint("owner/repo", "sprint-7")
    assert exc.value.status_code == 409


# ── AC10: pending state survives a reload (file-backed, never auto-advanced) ──

def test_ac10_pending_persists_across_reload(project):
    """A fresh read of plan.json from disk still reports pending — no drift."""
    server._plan_json_set_state(
        project, "sprint-7", "draft", signoff={"state": "pending"}
    )
    # Re-read straight from disk (simulating a page reload / new request).
    on_disk = json.loads(
        server._sprint_plan_path(project, "sprint-7").read_text(encoding="utf-8")
    )
    assert on_disk["signoff"]["state"] == "pending"
    assert server._sprint_signoff_state(project, "sprint-7") == "pending"


# ── Board data contract: sprint_signoff map for the UI badge/disabled button ─

def test_sprint_management_exposes_signoff(project, monkeypatch):
    """get_sprint_management_issues returns a sprint_signoff map per label.

    Drives the PENDING SIGN-OFF badge (AC1) and the disabled Run button (AC2)
    in the board front-end.
    """
    label = "sprint-7"
    server._plan_json_set_state(
        project, label, "draft", signoff={"state": "pending"}
    )

    issue = {
        "number": 101, "title": "T", "body": "",
        "labels": [{"name": label}], "url": "", "createdAt": "",
    }
    monkeypatch.setattr(
        server.github_client, "list_open_issues_with_body",
        lambda repo_name=None, limit=200: [issue],
    )
    monkeypatch.setattr(
        server.github_client, "list_sprints", lambda repo_name=None: [7]
    )
    monkeypatch.setattr(
        server.github_client, "list_sprint_labels", lambda repo_name=None: [label]
    )
    monkeypatch.setattr(server.github_client, "classify_issue", lambda iss: "open")
    monkeypatch.setattr(server, "_finished_sprint_summaries", lambda repo: {})
    monkeypatch.setattr(server, "_check_estimate_stale", lambda *a, **k: False)

    resp = server.get_sprint_management_issues("owner/repo")
    assert resp["sprint_signoff"].get(label) == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

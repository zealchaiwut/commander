"""Tester verification for issue #720: Emit activity event on every label transition.

This feature lives in `_transition_safe()` in sprint_manager.py — an internal
function, not an HTTP endpoint — so the AC are verified in-process rather than
against the UAT HTTP server. Frontend render (AC-3) is verified by a static
check on project.html.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import services.sprint_manager.sprint_manager as sm
from services.sprint_manager.state_machine import TicketState


@pytest.fixture
def harness(monkeypatch):
    """Capture emitted events and drive a controllable fake transition."""
    calls = []
    state = {"changed": True, "before": set()}

    def fake_record(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project, "source": source, "actor": actor,
            "type": type, "target": target, "detail": detail,
        })

    def fake_transition(issue, target_state, *, actor, repo=None, note=None):
        return state["changed"]

    class _FakeGH:
        def get_issue(self, issue_number, repo_name=None):
            labels = state["before"]
            if labels is None:
                raise RuntimeError("labels unavailable")
            return {"labels": [{"name": n} for n in labels]}

    monkeypatch.setattr(sm, "_db_record_event", fake_record, raising=False)
    monkeypatch.setattr(sm, "_RECORD_EVENT_AVAILABLE", True, raising=False)
    monkeypatch.setattr(sm, "_STATE_MACHINE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(sm, "_sm_transition", fake_transition, raising=False)
    monkeypatch.setattr(sm, "github_client", _FakeGH(), raising=False)

    return {
        "calls": calls,
        "set_changed": lambda v: state.__setitem__("changed", v),
        "set_before": lambda v: state.__setitem__("before", v),
        "events": lambda: [c for c in calls if c["type"] == "ticket_label_changed"],
    }


# AC: emits a structured event on every label change with the required fields
def test_label_transition_events__emits_structured_event(harness):
    harness["set_before"]({"in-progress"})
    sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
    evs = harness["events"]()
    assert len(evs) == 1
    ev = evs[0]
    assert ev["actor"] == "coder"
    assert "700" in str(ev["target"])
    for key in ("from_label", "to_label", "added", "removed"):
        assert key in ev["detail"], f"missing detail field {key!r}"
    assert ev["detail"]["added"] == ["SIT"]
    assert ev["detail"]["removed"] == ["in-progress"]


# AC: event posted via record_event with source="agent" to the target repo/project
def test_label_transition_events__posted_via_record_event_source_agent(harness):
    harness["set_before"]({"in-progress"})
    sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
    ev = harness["events"]()[0]
    assert ev["source"] == "agent"
    assert ev["project"] == "owner/repo"


# AC: activity log renders human-readable entries for the new event type
def test_label_transition_events__frontend_renders_human_readable():
    html = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text()
    assert "ticket_label_changed" in html
    assert "moved" in html and "labeled" in html


# AC: entry includes issue number, label delta, and actor role (pure-add case)
def test_label_transition_events__pure_add_includes_issue_and_actor(harness):
    harness["set_before"](set())
    sm._transition_safe(815, TicketState.NEEDS_REWORK,
                        actor="sprint_manager", repo_name="owner/repo")
    ev = harness["events"]()[0]
    assert ev["actor"] == "sprint_manager"
    assert "815" in str(ev["target"])
    assert ev["detail"]["added"] == ["needs-rework"]
    assert ev["detail"]["removed"] == []
    assert ev["detail"]["from_label"] is None
    assert ev["detail"]["to_label"] == "needs-rework"


# AC: no activity entry when _transition_safe() is a no-op (no label changed)
def test_label_transition_events__no_event_on_noop(harness):
    harness["set_changed"](False)
    harness["set_before"]({"SIT"})
    sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
    assert harness["events"]() == []


# AC: state machine unavailable → no crash, no event
def test_label_transition_events__state_machine_unavailable_noop(harness, monkeypatch):
    monkeypatch.setattr(sm, "_STATE_MACHINE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(sm, "_sm_transition", None, raising=False)
    sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
    assert harness["events"]() == []

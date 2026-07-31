"""Tests for issue #720: Emit activity event on every label transition.

Acceptance Criteria:
- AC-1: `_transition_safe()` emits a structured event on every label add/remove
        with fields: actor (agent role string), target (issue number),
        detail: { from_label, to_label, added: [...], removed: [...] }.
- AC-2: Event is posted to the dashboard activity feed via record_event
        (source="agent").
- AC-3: Activity log renders human-readable entries, e.g.
        "coder moved #700 → SIT" or "sprint_manager labeled #700 needs-rework".
- AC-4: Entry includes issue number, label change (before → after / added/removed),
        and actor role.
- AC-5: No activity entry is emitted when `_transition_safe()` is called but no
        label actually changes (transition is a no-op).
- AC-6: Existing agent event recording behaviour continues to work
        (record_event signature unchanged; source="agent").
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


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def capture(monkeypatch):
    """Capture record_event calls and drive a controllable fake transition.

    Returns a dict with:
      - calls: list of emitted events
      - set_changed(bool): control whether the fake transition reports a change
      - set_before(set|None): control the status labels present before transition
    """
    calls = []

    def fake_record(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project,
            "source": source,
            "actor": actor,
            "type": type,
            "target": target,
            "detail": detail,
            "action_id": action_id,
        })

    state = {"changed": True, "before": set()}

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

    def set_changed(v):
        state["changed"] = v

    def set_before(v):
        state["before"] = v

    return {"calls": calls, "set_changed": set_changed, "set_before": set_before}


def _label_events(calls):
    return [c for c in calls if c["type"] == "ticket_label_changed"]


# ── AC-1: structured event on every label change ──────────────────────────────

class TestEmitsStructuredEvent:
    def test_emits_one_event_on_change(self, capture):
        capture["set_before"]({"in-progress"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        evs = _label_events(capture["calls"])
        assert len(evs) == 1

    def test_event_has_required_fields(self, capture):
        capture["set_before"]({"in-progress"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        ev = _label_events(capture["calls"])[0]
        assert ev["actor"] == "coder"
        assert "700" in str(ev["target"])
        d = ev["detail"]
        for key in ("from_label", "to_label", "added", "removed"):
            assert key in d, f"missing detail field {key!r}"

    def test_added_and_removed_reflect_label_delta(self, capture):
        capture["set_before"]({"in-progress"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        d = _label_events(capture["calls"])[0]["detail"]
        assert d["added"] == ["SIT"]
        assert d["removed"] == ["in-progress"]
        assert d["from_label"] == "in-progress"
        assert d["to_label"] == "SIT"

    def test_pure_add_has_empty_removed(self, capture):
        # Ticket starts with no status label, gets needs-rework added.
        capture["set_before"](set())
        sm._transition_safe(700, TicketState.NEEDS_REWORK,
                            actor="sprint_manager", repo_name="owner/repo")
        d = _label_events(capture["calls"])[0]["detail"]
        assert d["added"] == ["needs-rework"]
        assert d["removed"] == []
        assert d["from_label"] is None
        assert d["to_label"] == "needs-rework"


# ── AC-2: posted via record_event with source="agent" ─────────────────────────

class TestRecordEventSourceAgent:
    def test_source_is_agent(self, capture):
        capture["set_before"]({"in-progress"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        ev = _label_events(capture["calls"])[0]
        assert ev["source"] == "agent"

    def test_project_is_target_repo(self, capture):
        capture["set_before"]({"in-progress"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        ev = _label_events(capture["calls"])[0]
        assert ev["project"] == "owner/repo"


# ── AC-4: actor role and issue number present ─────────────────────────────────

class TestActorAndIssue:
    def test_actor_role_preserved(self, capture):
        capture["set_before"](set())
        sm._transition_safe(815, TicketState.UAT, actor="sprint_manager", repo_name="owner/repo")
        ev = _label_events(capture["calls"])[0]
        assert ev["actor"] == "sprint_manager"
        assert "815" in str(ev["target"])


# ── AC-5: no event on no-op transition ────────────────────────────────────────

class TestNoOpEmitsNothing:
    def test_no_event_when_transition_unchanged(self, capture):
        capture["set_changed"](False)
        capture["set_before"]({"SIT"})
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        assert _label_events(capture["calls"]) == []


# ── AC-6: state machine unavailable → no crash, no event ──────────────────────

class TestStateMachineUnavailable:
    def test_unavailable_emits_nothing(self, capture, monkeypatch):
        monkeypatch.setattr(sm, "_STATE_MACHINE_AVAILABLE", False, raising=False)
        monkeypatch.setattr(sm, "_sm_transition", None, raising=False)
        sm._transition_safe(700, TicketState.SIT, actor="coder", repo_name="owner/repo")
        assert _label_events(capture["calls"]) == []


# ── AC-3: frontend renders a human-readable entry ─────────────────────────────

class TestFrontendRendersHumanReadable:
    """Static check: the activity-log row renderer special-cases the new
    ticket_label_changed event type into a human-readable string
    ("moved #N → X" / "labeled #N X")."""

    def test_project_html_renders_label_changed(self):
        html = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text()
        assert "ticket_label_changed" in html, \
            "project.html does not render the ticket_label_changed event type"
        assert "moved" in html and "labeled" in html, \
            "project.html missing human-readable verbs for label transitions"

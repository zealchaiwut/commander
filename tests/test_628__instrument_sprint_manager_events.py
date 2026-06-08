"""Tests for issue #628: Instrument sprint_manager to record agent/sprint lifecycle events.

AC-1: sprint_started event with target=sprint-N, actor=system, detail={ticket_count, levels}
AC-2: ticket_dispatched event each time a ticket is assigned to an agent
AC-3: ticket_agent_finished event when agent completes, with detail={agent, duration}
AC-4: sprint_finished event on normal completion with detail={done, failed, skipped, duration}
AC-5: sprint_cancelled event on early cancellation with detail={tickets_remaining}
AC-6: Events appear in chronological order when queried for a sprint
AC-7: actor uses agent identity when available; falls back to "system"
AC-8: No duplicate events fired for the same lifecycle moment
"""
from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ["COMMANDER_MAX_FIX_ROUNDS"] = "1"

import services.sprint_manager.sprint_manager as sm


# ── helpers ───────────────────────────────────────────────────────────────────

def _run_sprint(label="sprint-99", **kwargs):
    """Call run_sprint() with test-safe defaults."""
    defaults = dict(
        label=label,
        skip_gates=True,
        gate_pytest=False,
        gate_lint=False,
        gate_merge_preview=False,
        gate_typecheck=False,
        gate_design=False,
        gate_frontend_lint=False,
        skip_estimator=True,
    )
    defaults.update(kwargs)
    return sm.run_sprint(**defaults)


def _apply_base_mocks(monkeypatch, tmp_path, issues=None, extra_issues=None):
    """Patch all external I/O needed by run_sprint()."""
    if issues is None:
        issues = [
            {"number": 11, "title": "First ticket"},
            {"number": 12, "title": "Second ticket"},
        ]
    if extra_issues:
        issues = issues + extra_issues

    monkeypatch.setattr(sm, "list_backlog_issues", lambda *a, **kw: issues)
    monkeypatch.setattr(sm, "_dispatch_coder", lambda *a, **kw: (True, None))
    monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-test")
    monkeypatch.setattr(sm, "_dispatch_tester", lambda *a, **kw: (0, None))
    monkeypatch.setattr(sm, "handle_post_tester", lambda *a, **kw: (True, "Merged", None))
    monkeypatch.setattr(sm, "_post_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_ticket_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_init", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_setup_pid_file", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_wait_if_paused", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_create_sprint_branch", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_warn_file_conflicts", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_load_estimate", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path / "sprints")
    monkeypatch.setattr(sm, "structured_log", MagicMock())
    monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)
    sm._sprint_user_cancelled.clear()


@pytest.fixture
def event_capture(tmp_path, monkeypatch):
    """Patch external I/O; inject fake record_event; return captured call list."""
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

    monkeypatch.setattr(sm, "_db_record_event", fake_record, raising=False)
    monkeypatch.setattr(sm, "_RECORD_EVENT_AVAILABLE", True, raising=False)
    _apply_base_mocks(monkeypatch, tmp_path)
    return calls


@pytest.fixture
def cancellation_capture(tmp_path, monkeypatch):
    """3-issue sprint that raises SystemExit during the 2nd coder dispatch."""
    calls = []

    def fake_record(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project,
            "source": source,
            "actor": actor,
            "type": type,
            "target": target,
            "detail": detail,
        })

    monkeypatch.setattr(sm, "_db_record_event", fake_record, raising=False)
    monkeypatch.setattr(sm, "_RECORD_EVENT_AVAILABLE", True, raising=False)

    coder_call_count = [0]

    def coder_that_cancels(*args, **kwargs):
        coder_call_count[0] += 1
        if coder_call_count[0] > 1:
            raise SystemExit(130)
        return (True, None)

    _apply_base_mocks(monkeypatch, tmp_path, issues=[
        {"number": 11, "title": "T1"},
        {"number": 12, "title": "T2"},
        {"number": 13, "title": "T3"},
    ])
    monkeypatch.setattr(sm, "_dispatch_coder", coder_that_cancels)
    return calls


# ── AC-1: sprint_started ──────────────────────────────────────────────────────

class TestSprintStartedEvent:
    """AC-1: sprint_started event fired at sprint begin."""

    def test_sprint_started_fires(self, event_capture):
        _run_sprint()
        assert any(c["type"] == "sprint_started" for c in event_capture)

    def test_sprint_started_target_is_sprint_label(self, event_capture):
        _run_sprint(label="sprint-99")
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert ev["target"] == "sprint-99"

    def test_sprint_started_actor_is_system(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert ev["actor"] == "system"

    def test_sprint_started_detail_ticket_count(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert ev["detail"]["ticket_count"] == 2

    def test_sprint_started_detail_levels_present_and_int(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert "levels" in ev["detail"]
        assert isinstance(ev["detail"]["levels"], int)
        assert ev["detail"]["levels"] >= 1

    def test_sprint_started_source_is_sprint_manager(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert ev["source"] == "sprint_manager"


# ── AC-2: ticket_dispatched ───────────────────────────────────────────────────

class TestTicketDispatchedEvent:
    """AC-2: ticket_dispatched for each CODER and TESTER dispatch."""

    def test_coder_dispatched_once_per_issue(self, event_capture):
        _run_sprint()
        coder_events = [
            c for c in event_capture
            if c["type"] == "ticket_dispatched" and c["detail"].get("agent") == "CODER"
        ]
        assert len(coder_events) == 2

    def test_tester_dispatched_once_per_issue(self, event_capture):
        _run_sprint()
        tester_events = [
            c for c in event_capture
            if c["type"] == "ticket_dispatched" and c["detail"].get("agent") == "TESTER"
        ]
        assert len(tester_events) == 2

    def test_dispatched_target_format(self, event_capture):
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_dispatched":
                assert ev["target"].startswith("#"), f"Expected #N target, got {ev['target']!r}"

    def test_dispatched_actor_is_system(self, event_capture):
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_dispatched":
                assert ev["actor"] == "system"

    def test_coder_dispatch_target_matches_issue_numbers(self, event_capture):
        _run_sprint()
        coder_targets = {
            c["target"]
            for c in event_capture
            if c["type"] == "ticket_dispatched" and c["detail"].get("agent") == "CODER"
        }
        assert coder_targets == {"#11", "#12"}


# ── AC-3: ticket_agent_finished ───────────────────────────────────────────────

class TestTicketAgentFinishedEvent:
    """AC-3: ticket_agent_finished with agent and non-negative duration."""

    def test_coder_finished_once_per_issue(self, event_capture):
        _run_sprint()
        coder_done = [
            c for c in event_capture
            if c["type"] == "ticket_agent_finished" and c["detail"].get("agent") == "CODER"
        ]
        assert len(coder_done) == 2

    def test_tester_finished_once_per_issue(self, event_capture):
        _run_sprint()
        tester_done = [
            c for c in event_capture
            if c["type"] == "ticket_agent_finished" and c["detail"].get("agent") == "TESTER"
        ]
        assert len(tester_done) == 2

    def test_duration_present_in_all_finished_events(self, event_capture):
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_agent_finished":
                assert "duration" in ev["detail"], f"duration missing: {ev}"

    def test_duration_is_nonnegative(self, event_capture):
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_agent_finished":
                assert ev["detail"]["duration"] >= 0, f"duration < 0: {ev}"

    def test_finished_target_format(self, event_capture):
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_agent_finished":
                assert ev["target"].startswith("#")

    def test_finished_actor_is_system_when_identity_unavailable(self, event_capture):
        """AC-7: actor fallback to 'system' when agent identity unavailable."""
        _run_sprint()
        for ev in event_capture:
            if ev["type"] == "ticket_agent_finished":
                assert ev["actor"] == "system"


# ── AC-4: sprint_finished ─────────────────────────────────────────────────────

class TestSprintFinishedEvent:
    """AC-4: sprint_finished on normal sprint completion."""

    def test_sprint_finished_fires(self, event_capture):
        _run_sprint()
        assert any(c["type"] == "sprint_finished" for c in event_capture)

    def test_sprint_finished_target(self, event_capture):
        _run_sprint(label="sprint-99")
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert ev["target"] == "sprint-99"

    def test_sprint_finished_actor_is_system(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert ev["actor"] == "system"

    def test_sprint_finished_detail_done_count(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert ev["detail"]["done"] == 2

    def test_sprint_finished_detail_skipped_and_failed_present(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert "skipped" in ev["detail"]
        assert "failed" in ev["detail"]

    def test_sprint_finished_detail_duration_nonnegative(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert "duration" in ev["detail"]
        assert ev["detail"]["duration"] >= 0


# ── AC-5: sprint_cancelled ────────────────────────────────────────────────────

class TestSprintCancelledEvent:
    """AC-5: sprint_cancelled on early cancellation; no sprint_finished."""

    def test_sprint_cancelled_fires_on_systemexit(self, cancellation_capture):
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-99")
        types = [c["type"] for c in cancellation_capture]
        assert "sprint_cancelled" in types, f"sprint_cancelled missing from {types}"

    def test_sprint_finished_absent_when_cancelled(self, cancellation_capture):
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-99")
        types = [c["type"] for c in cancellation_capture]
        assert "sprint_finished" not in types

    def test_sprint_cancelled_target(self, cancellation_capture):
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-99")
        ev = next(c for c in cancellation_capture if c["type"] == "sprint_cancelled")
        assert ev["target"] == "sprint-99"

    def test_sprint_cancelled_actor_is_system(self, cancellation_capture):
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-99")
        ev = next(c for c in cancellation_capture if c["type"] == "sprint_cancelled")
        assert ev["actor"] == "system"

    def test_sprint_cancelled_tickets_remaining(self, cancellation_capture):
        """3 issues, 1 completes, cancel during 2nd → 2 remaining."""
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-99")
        ev = next(c for c in cancellation_capture if c["type"] == "sprint_cancelled")
        assert ev["detail"]["tickets_remaining"] == 2


# ── AC-6: chronological order ─────────────────────────────────────────────────

class TestEventChronologicalOrder:
    """AC-6: Events appear in the order they occurred for a sprint."""

    def test_sprint_started_before_sprint_finished(self, event_capture):
        _run_sprint()
        types = [c["type"] for c in event_capture]
        assert types.index("sprint_started") < types.index("sprint_finished")

    def test_ticket_dispatched_before_agent_finished(self, event_capture):
        _run_sprint()
        # For issue 11: CODER dispatch must precede CODER finished
        coder_dispatch_idx = next(
            i for i, c in enumerate(event_capture)
            if c["type"] == "ticket_dispatched" and c["detail"].get("agent") == "CODER"
            and c["target"] == "#11"
        )
        coder_done_idx = next(
            i for i, c in enumerate(event_capture)
            if c["type"] == "ticket_agent_finished" and c["detail"].get("agent") == "CODER"
            and c["target"] == "#11"
        )
        assert coder_dispatch_idx < coder_done_idx

    def test_sprint_started_before_any_ticket_event(self, event_capture):
        _run_sprint()
        types = [c["type"] for c in event_capture]
        started_idx = types.index("sprint_started")
        first_ticket_idx = next(
            i for i, c in enumerate(event_capture)
            if c["type"] in ("ticket_dispatched", "ticket_agent_finished")
        )
        assert started_idx < first_ticket_idx

    def test_sprint_finished_after_all_ticket_events(self, event_capture):
        _run_sprint()
        types = [c["type"] for c in event_capture]
        finished_idx = types.index("sprint_finished")
        last_ticket_idx = max(
            i for i, c in enumerate(event_capture)
            if c["type"] in ("ticket_dispatched", "ticket_agent_finished")
        )
        assert finished_idx > last_ticket_idx


# ── AC-7: actor fallback ──────────────────────────────────────────────────────

class TestActorFallback:
    """AC-7: actor uses 'system' when agent identity string not available."""

    def test_all_events_use_system_as_actor(self, event_capture):
        _run_sprint()
        lifecycle_types = {
            "sprint_started", "sprint_finished",
            "ticket_dispatched", "ticket_agent_finished",
        }
        for ev in event_capture:
            if ev["type"] in lifecycle_types:
                assert ev["actor"] == "system", (
                    f"Expected actor='system' for {ev['type']}, got {ev['actor']!r}"
                )


# ── AC-8: no duplicate events ─────────────────────────────────────────────────

class TestNoDuplicateEvents:
    """AC-8: No duplicate events for the same lifecycle moment."""

    def test_exactly_one_sprint_started(self, event_capture):
        _run_sprint()
        count = sum(1 for c in event_capture if c["type"] == "sprint_started")
        assert count == 1

    def test_exactly_one_sprint_finished(self, event_capture):
        _run_sprint()
        count = sum(1 for c in event_capture if c["type"] == "sprint_finished")
        assert count == 1

    def test_coder_dispatched_once_per_issue_no_dup(self, event_capture):
        _run_sprint()
        for issue_num in (11, 12):
            count = sum(
                1 for c in event_capture
                if c["type"] == "ticket_dispatched"
                and c["target"] == f"#{issue_num}"
                and c["detail"].get("agent") == "CODER"
            )
            assert count == 1, f"Expected 1 CODER dispatch for #{issue_num}, got {count}"

    def test_tester_dispatched_once_per_issue_no_dup(self, event_capture):
        _run_sprint()
        for issue_num in (11, 12):
            count = sum(
                1 for c in event_capture
                if c["type"] == "ticket_dispatched"
                and c["target"] == f"#{issue_num}"
                and c["detail"].get("agent") == "TESTER"
            )
            assert count == 1, f"Expected 1 TESTER dispatch for #{issue_num}, got {count}"

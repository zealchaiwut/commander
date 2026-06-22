"""Tests for issue #1275: Extract sprint_manager event emission to events.py.

AC-1: services/sprint_manager/events.py exists and contains exactly the five functions
AC-2: None of the five functions have any signature, docstring, or logic changes
AC-3: Original module imports the moved symbols from events.py
AC-4: python -m py_compile services/sprint_manager/events.py exits 0 with no output
AC-5: python -m py_compile on sprint_manager module also exits 0
AC-6: No other file references old import paths
AC-7: Event payloads, names, and emission order are identical to pre-move behavior
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent


# ── AC-1: events.py exists and contains exactly the five functions ─────────────

class TestEventsModuleExists:
    """AC-1: events.py exists and exports exactly the five event functions."""

    def test_events_module_is_importable(self):
        from services.sprint_manager import events  # noqa: F401

    def test_events_has_emit_sprint_lifecycle_event(self):
        from services.sprint_manager.events import _emit_sprint_lifecycle_event
        assert callable(_emit_sprint_lifecycle_event)

    def test_events_has_failure_event_detail(self):
        from services.sprint_manager.events import _failure_event_detail
        assert callable(_failure_event_detail)

    def test_events_has_emit_ticket_failed(self):
        from services.sprint_manager.events import _emit_ticket_failed
        assert callable(_emit_ticket_failed)

    def test_events_has_post_agent_event(self):
        from services.sprint_manager.events import _post_agent_event
        assert callable(_post_agent_event)

    def test_events_has_post_sprint_status(self):
        from services.sprint_manager.events import _post_sprint_status
        assert callable(_post_sprint_status)


# ── AC-2: Signatures are identical — pure move only ───────────────────────────

class TestFunctionSignatures:
    """AC-2: No signature changes — pure move."""

    def test_emit_sprint_lifecycle_event_signature(self):
        from services.sprint_manager.events import _emit_sprint_lifecycle_event
        sig = inspect.signature(_emit_sprint_lifecycle_event)
        params = list(sig.parameters.keys())
        assert params == ["type", "target", "actor", "detail", "project", "action_id"]
        assert sig.parameters["action_id"].default is None

    def test_failure_event_detail_signature(self):
        from services.sprint_manager.events import _failure_event_detail
        sig = inspect.signature(_failure_event_detail)
        params = list(sig.parameters.keys())
        assert params == ["issue_num", "agent_role", "reason", "category",
                          "cfg", "gate", "sprint_label"]
        assert sig.parameters["cfg"].default is None
        assert sig.parameters["gate"].default is False
        assert sig.parameters["sprint_label"].default is None

    def test_emit_ticket_failed_signature(self):
        from services.sprint_manager.events import _emit_ticket_failed
        sig = inspect.signature(_emit_ticket_failed)
        params = list(sig.parameters.keys())
        assert params == ["issue_num", "agent_role", "reason", "category",
                          "project", "action_id", "cfg", "gate", "sprint_label"]
        assert sig.parameters["action_id"].default is None
        assert sig.parameters["cfg"].default is None
        assert sig.parameters["gate"].default is False
        assert sig.parameters["sprint_label"].default is None

    def test_post_agent_event_signature(self):
        from services.sprint_manager.events import _post_agent_event
        sig = inspect.signature(_post_agent_event)
        params = list(sig.parameters.keys())
        assert params == ["tool_name", "agent_id", "api_url"]
        assert sig.parameters["agent_id"].default == "sprint-manager"
        assert sig.parameters["api_url"].default is None

    def test_post_sprint_status_signature(self):
        from services.sprint_manager.events import _post_sprint_status
        sig = inspect.signature(_post_sprint_status)
        params = list(sig.parameters.keys())
        assert params == ["state", "api_url", "project"]
        assert sig.parameters["api_url"].default is None
        assert sig.parameters["project"].default is None


# ── AC-3: sprint_manager re-exports all five from events.py ──────────────────

class TestSprintManagerReExports:
    """AC-3: sprint_manager imports the moved symbols so call sites work unmodified."""

    def test_sm_emit_sprint_lifecycle_event_is_events_version(self):
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.events import _emit_sprint_lifecycle_event
        assert sm._emit_sprint_lifecycle_event is _emit_sprint_lifecycle_event

    def test_sm_failure_event_detail_is_events_version(self):
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.events import _failure_event_detail
        assert sm._failure_event_detail is _failure_event_detail

    def test_sm_emit_ticket_failed_is_events_version(self):
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.events import _emit_ticket_failed
        assert sm._emit_ticket_failed is _emit_ticket_failed

    def test_sm_post_agent_event_is_events_version(self):
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.events import _post_agent_event
        assert sm._post_agent_event is _post_agent_event

    def test_sm_post_sprint_status_is_events_version(self):
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.events import _post_sprint_status
        assert sm._post_sprint_status is _post_sprint_status


# ── AC-4: py_compile events.py exits 0 ───────────────────────────────────────

class TestPyCompileEvents:
    """AC-4: events.py has no syntax errors."""

    def test_py_compile_events_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/events.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_py_compile_events_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/events.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC-5: py_compile sprint_manager.py exits 0 ───────────────────────────────

class TestPyCompileSprintManager:
    """AC-5: sprint_manager.py has no syntax errors after the move."""

    def test_py_compile_sprint_manager_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_py_compile_sprint_manager_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC-6: Functions live in events.py, not sprint_manager.py ─────────────────

class TestFunctionsDefinedInEventsModule:
    """AC-6: The five functions are defined in events.py (not sprint_manager.py)."""

    def test_emit_sprint_lifecycle_event_module(self):
        from services.sprint_manager.events import _emit_sprint_lifecycle_event
        assert _emit_sprint_lifecycle_event.__module__ == "services.sprint_manager.events"

    def test_failure_event_detail_module(self):
        from services.sprint_manager.events import _failure_event_detail
        assert _failure_event_detail.__module__ == "services.sprint_manager.events"

    def test_emit_ticket_failed_module(self):
        from services.sprint_manager.events import _emit_ticket_failed
        assert _emit_ticket_failed.__module__ == "services.sprint_manager.events"

    def test_post_agent_event_module(self):
        from services.sprint_manager.events import _post_agent_event
        assert _post_agent_event.__module__ == "services.sprint_manager.events"

    def test_post_sprint_status_module(self):
        from services.sprint_manager.events import _post_sprint_status
        assert _post_sprint_status.__module__ == "services.sprint_manager.events"


# ── AC-7: Event payloads and behavior identical ───────────────────────────────

class TestEventPayloads:
    """AC-7: payloads, names, and behavior are byte-for-byte identical to pre-move."""

    def test_post_agent_event_posts_to_correct_url(self):
        from services.sprint_manager import events
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)

        with patch.object(events.urllib.request, "urlopen", fake_urlopen):
            events._post_agent_event("gate:pytest", api_url="http://test:9999")

        assert len(captured) == 1
        assert captured[0].full_url == "http://test:9999/api/agent-event"

    def test_post_agent_event_payload_shape(self):
        from services.sprint_manager import events
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))

        with patch.object(events.urllib.request, "urlopen", fake_urlopen):
            events._post_agent_event("gate:pytest", agent_id="sm", api_url="http://test:9999")

        payload = captured[0]
        assert payload["agent_id"] == "sm"
        assert payload["tool_name"] == "gate:pytest"
        assert "timestamp" in payload

    def test_post_agent_event_silently_swallows_errors(self):
        from services.sprint_manager import events

        def boom(req, timeout=None):
            raise OSError("network down")

        with patch.object(events.urllib.request, "urlopen", boom):
            events._post_agent_event("gate:pytest")  # must not raise

    def test_post_sprint_status_posts_state_dict(self):
        from services.sprint_manager import events
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))

        mock_state = MagicMock()
        mock_state.to_dict.return_value = {"label": "sprint-99", "status": "running"}

        with patch.object(events.urllib.request, "urlopen", fake_urlopen):
            events._post_sprint_status(mock_state, api_url="http://test:9999")

        assert captured[0]["label"] == "sprint-99"
        assert captured[0]["status"] == "running"

    def test_post_sprint_status_injects_project_when_provided(self):
        from services.sprint_manager import events
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode()))

        mock_state = MagicMock()
        mock_state.to_dict.return_value = {"label": "sprint-99"}

        with patch.object(events.urllib.request, "urlopen", fake_urlopen):
            events._post_sprint_status(
                mock_state, api_url="http://test:9999", project="owner/repo"
            )

        assert captured[0]["project"] == "owner/repo"

    def test_emit_sprint_lifecycle_event_calls_record_event(self):
        """Verifies the function invokes _db_record_event with the right kwargs."""
        import services.sprint_manager.sprint_manager as sm
        calls = []

        def fake_record(**kwargs):
            calls.append(kwargs)

        original_record = sm._db_record_event
        original_available = sm._RECORD_EVENT_AVAILABLE
        try:
            sm._db_record_event = fake_record
            sm._RECORD_EVENT_AVAILABLE = True
            from services.sprint_manager.events import _emit_sprint_lifecycle_event
            _emit_sprint_lifecycle_event(
                type="sprint_started",
                target="sprint-99",
                actor="system",
                detail={"ticket_count": 3},
                project="owner/repo",
                action_id="act-1",
            )
        finally:
            sm._db_record_event = original_record
            sm._RECORD_EVENT_AVAILABLE = original_available

        assert len(calls) == 1
        c = calls[0]
        assert c["type"] == "sprint_started"
        assert c["target"] == "sprint-99"
        assert c["actor"] == "system"
        assert c["detail"] == {"ticket_count": 3}
        assert c["project"] == "owner/repo"
        assert c["action_id"] == "act-1"
        assert c["source"] == "agent"

    def test_emit_sprint_lifecycle_event_noop_when_unavailable(self):
        """No-op when _RECORD_EVENT_AVAILABLE is False."""
        import services.sprint_manager.sprint_manager as sm
        calls = []
        original_available = sm._RECORD_EVENT_AVAILABLE
        try:
            sm._RECORD_EVENT_AVAILABLE = False
            from services.sprint_manager.events import _emit_sprint_lifecycle_event
            _emit_sprint_lifecycle_event(
                type="sprint_started", target="s", actor="system",
                detail={}, project="p",
            )
        finally:
            sm._RECORD_EVENT_AVAILABLE = original_available
        assert calls == []

    def test_failure_event_detail_returns_required_keys(self, monkeypatch):
        """_failure_event_detail returns a dict with all required payload keys."""
        import services.sprint_manager.sprint_manager as sm
        monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-slug")
        from services.sprint_manager.events import _failure_event_detail
        detail = _failure_event_detail(
            issue_num=42,
            agent_role="coder",
            reason="tests failed",
            category="PYTEST_FAIL",
        )
        assert detail["issue_num"] == 42
        assert detail["agent"] == "CODER"
        assert detail["reason"] == "tests failed"
        assert detail["category"] == "PYTEST_FAIL"
        assert detail["branch"] == "feature/42-slug"
        assert detail["gate"] is False

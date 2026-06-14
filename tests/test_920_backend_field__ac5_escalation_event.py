"""Tests for issue #920 AC5 — escalation recorded as distinct activity event.

AC5: Escalation is recorded as a distinct event in the activity stream
(e.g. `escalated cline → claude-code`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))


def test_sprint_manager_emits_escalation_event():
    """AC5: sprint_manager must emit a 'coder_backend_escalated' event on escalation."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    assert "coder_backend_escalated" in src, (
        "sprint_manager must emit a coder_backend_escalated event when escalating"
    )


def test_escalation_event_has_category_field():
    """AC5: the escalation event must have category='agent' so the Activity API picks it up."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    # The event call must include category so activity_service's JSONL reader includes it
    # (reader filters: if 'category' not in rec: continue)
    assert 'category="agent"' in src or "category='agent'" in src, (
        "escalation event must be emitted with category='agent' for Activity API visibility"
    )


def test_escalation_event_includes_from_to_backend():
    """AC5: the escalation event must carry from_backend and to_backend fields."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    assert "from_backend" in src, "escalation event must include from_backend"
    assert "to_backend" in src, "escalation event must include to_backend"


def test_activity_service_extracts_from_backend_to_backend():
    """AC5: activity_service must extract from_backend and to_backend from JSONL events."""
    src = (DASHBOARD_DIR / "routers" / "activity_service.py").read_text()
    assert "from_backend" in src, "activity_service must extract from_backend from JSONL"
    assert "to_backend" in src, "activity_service must extract to_backend from JSONL"


def test_project_html_renders_escalation_event():
    """AC5: project.html must render the coder_backend_escalated event type."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "coder_backend_escalated" in src, (
        "project.html must handle and render the coder_backend_escalated event type"
    )


def test_escalation_event_written_to_jsonl(tmp_path, monkeypatch):
    """AC5: structured_log.event with category writes to events JSONL (integration)."""
    from services.logging import log as structured_log  # noqa: E402 (SM_DIR on path)

    # Point log dir to tmp_path
    import services.logging as svc_log
    monkeypatch.setattr(svc_log, "_resolve_log_dir", lambda: tmp_path)

    structured_log.event(
        "coder_backend_escalated",
        category="agent",
        issue_num=920,
        sprint_label="sprint-71",
        project="zealchaiwut/commander",
        agent_role="coder",
        from_backend="cline",
        to_backend="claude-code",
    )

    # Find the written JSONL file
    jsonl_files = list(tmp_path.glob("events-*.jsonl"))
    assert jsonl_files, "expected an events-*.jsonl file to be written"

    lines = jsonl_files[0].read_text().strip().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    escalation_events = [e for e in events if e.get("name") == "coder_backend_escalated"]
    assert escalation_events, "expected coder_backend_escalated event in JSONL"
    ev = escalation_events[0]
    assert ev.get("category") == "agent"
    assert ev.get("from_backend") == "cline"
    assert ev.get("to_backend") == "claude-code"

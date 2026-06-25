"""Cancelled / ended sprints must not keep /live in a running state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from routers.sprint_live import _live_freeze_terminal_fields  # noqa: E402


def test_live_freeze_terminal_clears_active_agents():
    payload = {
        "time_spent_sec": 9999,
        "started_at": "2026-06-14T13:51:18+00:00",
        "active_agents": [{"name": "coder", "ticket": {"number": 887}}],
        "current_ticket": {"number": 887, "title": "Burn pytest"},
        "issues": [{"number": 887, "status": "in-progress", "agent_status": "running", "agent": "coder"}],
    }
    plan = {
        "state": "needs_rework",
        "end_reason": "stopped by user",
        "started_at": "2026-06-14T13:51:18+00:00",
        "ended_at": "2026-06-14T15:27:10+00:00",
    }
    out = _live_freeze_terminal_fields(payload, plan)
    assert out["active_agents"] == []
    assert out["current_ticket"] is None
    assert out["run_state"] == "cancelled"
    assert out["time_spent_sec"] == 5752
    assert out["issues"][0]["agent_status"] is None
    assert out["issues"][0]["status"] == "pending"

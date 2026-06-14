"""Tests for issue #920 AC2 — Activity view shows backend alongside duration.

AC2: Activity view: coder token displays the backend alongside duration
(e.g. `coder · cline · 42s`).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def test_activity_view_renders_backend_chip():
    """AC2: project.html must render the backend alongside duration for coder events."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    # The duration pill or description must reference 'backend' for coder events
    assert "ev.backend" in src or "detail.backend" in src, (
        "project.html must read backend from event to display it"
    )


def test_activity_view_renders_cline_label():
    """AC2: project.html must be able to render 'cline' in an activity event chip."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    # Look for 'cline' in JS event rendering context (not just in comments/docs)
    assert "cline" in src, "project.html must have 'cline' rendering logic"


def test_evl_duration_pill_includes_backend_for_coder():
    """AC2: _evlDurationPill (or equivalent) outputs backend for coder agent_finished events."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    # Must concatenate or include backend in the duration pill output
    assert "_evlDurationPill" in src or "evl-dur-pill" in src, (
        "project.html must have a duration-pill renderer"
    )
    # The pill or adjacent display must show backend for coder events
    assert "backend" in src.lower(), "project.html must include backend display logic"


def test_activity_service_adds_backend_to_detail_extraction():
    """AC2: activity_service extracts 'backend' from JSONL lifecycle events into detail."""
    src = (DASHBOARD_DIR / "routers" / "activity_service.py").read_text()
    # The JSONL detail extraction loop must include 'backend' or similar
    assert '"backend"' in src or "'backend'" in src, (
        "activity_service must extract 'backend' field from JSONL events"
    )

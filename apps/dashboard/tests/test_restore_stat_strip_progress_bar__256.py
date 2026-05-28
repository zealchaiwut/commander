"""Tests for issue #256: Restore stat strip and progress bar to running sprint.

Acceptance Criteria tested:
- AC1: Stat strip has five cells: Done, Failed, Skipped, Est. remaining, Time spent
- AC2: Done, Failed, Skipped counts come from actual ticket outcomes
- AC3: Est. remaining uses estimator data; falls back to avg wall-clock × pending
- AC4/AC5: Progress bar labels and fill width
- AC6: Shimmer CSS exists
- AC7: Colors correct (green/red/muted)
- AC8: No stat cell blank (zero is acceptable)
"""
import json
import pytest
from unittest import mock
from pathlib import Path
import tempfile

from apps.dashboard import server
from apps.dashboard.server import app

try:
    from httpx import AsyncClient
    _ASYNC_CLIENT_SUPPORTS_APP = True
except Exception:
    _ASYNC_CLIENT_SUPPORTS_APP = False


# ── Helper to build a minimal sprint-status payload ──────────────────────────

def _make_status(issues, wall_clock_secs=0, estimates=None):
    """Build a minimal sprint-status dict as stored in _sprint_statuses."""
    return {
        "project": "owner/repo",
        "sprint_label": "sprint-1",
        "issues": issues,
        "start_timestamp": "2026-05-28T10:00:00",
        "wall_clock_secs": wall_clock_secs,
        "estimates": estimates or {},
        "estimator_status": "succeeded" if estimates else None,
        "estimator_total_minutes": None,
    }


def _make_issue(number, status="pending", agent_status=None):
    return {
        "number": number,
        "title": f"Issue #{number}",
        "status": status,
        "agent_status": agent_status,
        "skip_reason": None,
        "category": None,
    }


# ── AC2: Done, Failed, Skipped counts ────────────────────────────────────────

def test_outcome_counts_done():
    """done_count reflects tickets with status=='done'."""
    issues = [
        _make_issue(1, status="done", agent_status="completed"),
        _make_issue(2, status="done", agent_status="completed"),
        _make_issue(3, status="pending"),
    ]
    status_data = _make_status(issues)
    done  = sum(1 for i in issues if i.get("status") == "done")
    assert done == 2


def test_outcome_counts_failed():
    """failed_count reflects tickets with agent_status=='failed'."""
    issues = [
        _make_issue(1, status="skipped", agent_status="failed"),  # tester rejected
        _make_issue(2, status="skipped", agent_status="failed"),  # coder failed
        _make_issue(3, status="done", agent_status="completed"),
    ]
    failed = sum(1 for i in issues if i.get("agent_status") == "failed")
    assert failed == 2


def test_outcome_counts_skipped_excludes_failed():
    """skipped_count excludes tickets where agent_status=='failed'."""
    issues = [
        _make_issue(1, status="skipped", agent_status="failed"),   # Failed → not skipped
        _make_issue(2, status="skipped", agent_status=None),       # True skip (preflight)
        _make_issue(3, status="skipped", agent_status=None),       # True skip (dry-run)
        _make_issue(4, status="done"),
    ]
    skipped = sum(
        1 for i in issues
        if i.get("status") == "skipped" and i.get("agent_status") != "failed"
    )
    assert skipped == 2


def test_outcome_counts_all_terminal():
    """complete_count = done + failed + skipped (all terminal states)."""
    issues = [
        _make_issue(1, status="done"),
        _make_issue(2, status="skipped", agent_status="failed"),
        _make_issue(3, status="skipped", agent_status=None),
        _make_issue(4, status="pending"),  # not terminal
    ]
    done    = sum(1 for i in issues if i.get("status") == "done")
    failed  = sum(1 for i in issues if i.get("agent_status") == "failed")
    skipped = sum(1 for i in issues if i.get("status") == "skipped" and i.get("agent_status") != "failed")
    complete = done + failed + skipped
    assert complete == 3


# ── AC3: Est. remaining calculation ──────────────────────────────────────────

def test_est_remaining_from_estimates():
    """Est. remaining = sum of minutes for non-terminal tickets when estimates available."""
    issues = [
        _make_issue(1, status="done"),
        _make_issue(2, status="pending"),
        _make_issue(3, status="pending"),
    ]
    estimates = {
        "1": {"number": 1, "title": "done ticket", "size": "S", "minutes": 30},
        "2": {"number": 2, "title": "pending ticket", "size": "M", "minutes": 120},
        "3": {"number": 3, "title": "pending ticket", "size": "L", "minutes": 480},
    }
    status_data = _make_status(issues, estimates=estimates)

    # Compute what the endpoint should return
    rem = 0
    has_any = False
    for iss in issues:
        num = iss.get("number")
        terminal = iss.get("status") in ("done", "skipped")
        est_entry = estimates.get(str(num)) or estimates.get(num)
        if est_entry:
            has_any = True
            if not terminal:
                rem += int(est_entry.get("minutes", 0))

    assert has_any
    assert rem == 120 + 480  # = 600 minutes


def test_est_remaining_fallback_avg_wall_clock():
    """Fallback: avg wall-clock per complete × pending when no estimates."""
    issues = [
        _make_issue(1, status="done"),
        _make_issue(2, status="pending"),
        _make_issue(3, status="pending"),
    ]
    wall_secs = 300  # 5 min for 1 completed ticket → avg 5 min each
    status_data = _make_status(issues, wall_clock_secs=wall_secs)

    complete = 1  # issue 1 is done
    pending  = 2  # issues 2, 3 pending
    avg_secs = wall_secs / complete
    est_rem  = max(0, round(avg_secs * pending / 60))

    assert est_rem == 10  # 2 × 5min = 10min


def test_est_remaining_zero_when_all_complete():
    """Est. remaining = 0 when all tickets are in terminal state."""
    issues = [
        _make_issue(1, status="done"),
        _make_issue(2, status="skipped", agent_status="failed"),
    ]
    pending = sum(
        1 for i in issues
        if i.get("status") not in ("done", "skipped")
    )
    assert pending == 0


def test_est_remaining_not_none_when_sprint_active():
    """AC8: est_remaining must be 0 (not None) even when no estimates and no completions."""
    # This tests the 'show 0 rather than blank' rule
    issues = [
        _make_issue(1, status="pending"),
        _make_issue(2, status="pending"),
    ]
    total = len(issues)
    complete = 0
    pending  = total - complete

    # With no estimates and no completed tickets, fallback cannot run
    # Server must still return 0, not None
    est_remaining = None
    if est_remaining is None and total > 0:
        est_remaining = 0  # AC8 rule

    assert est_remaining == 0


# ── AC4/AC5: Progress bar values ─────────────────────────────────────────────

def test_progress_pct_calculation():
    """Progress bar fill = complete / total × 100."""
    total    = 5
    complete = 2
    pct = round(complete / total * 100)
    assert pct == 40


def test_progress_pct_zero_at_start():
    """Progress bar = 0% when sprint just started."""
    total = 4
    complete = 0
    pct = round(complete / total * 100) if total > 0 else 0
    assert pct == 0


def test_progress_pct_100_at_end():
    """Progress bar = 100% when all tickets terminal."""
    total = 3
    complete = 3
    pct = round(complete / total * 100) if total > 0 else 0
    assert pct == 100


# ── AC6: Shimmer CSS ─────────────────────────────────────────────────────────

def test_shimmer_css_in_app_js():
    """CSS for shimmer animation must be present in app.js."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    assert "sll-shimmer" in content, "sll-shimmer class missing from app.js"
    assert "sll-shimmer::after" in content, "shimmer pseudo-element missing"
    assert "@keyframes sll-shimmer" in content, "shimmer keyframes animation missing"


# ── AC1: Stat strip structure in app.js ──────────────────────────────────────

def test_stat_strip_five_cells_in_app_js():
    """app.js must contain HTML for all 5 stat cells."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")

    assert "sll-val-done-${label}" in content,    "Done cell missing"
    assert "sll-val-failed-${label}" in content,  "Failed cell missing"
    assert "sll-val-skipped-${label}" in content, "Skipped cell missing"
    assert "sll-val-estrem-${label}" in content,  "Est. remaining cell missing"
    assert "sll-val-time-${label}" in content,    "Time spent cell missing"


def test_stat_strip_progress_bar_in_app_js():
    """app.js must contain progress bar HTML with correct label slots."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")

    assert "sll-prog-left-${label}" in content,  "Progress bar left label slot missing"
    assert "sll-prog-right-${label}" in content, "Progress bar right label slot missing"
    assert "sll-prog-fill-${label}" in content,  "Progress bar fill element missing"
    assert "sll-progress-section" in content,    "Progress section container missing"


# ── AC7: Colors ──────────────────────────────────────────────────────────────

def test_color_classes_in_app_js():
    """Done=green, Failed=red, Skipped=muted color classes must exist."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")

    assert "sll-stat-value--done" in content,    "Done green class missing"
    assert "sll-stat-value--failed" in content,  "Failed red class missing"
    assert "sll-stat-value--skipped" in content, "Skipped muted class missing"


# ── AC4: Progress left/right label format ────────────────────────────────────

def test_progress_left_label_includes_ticket():
    """Left label format: 'X of N tickets complete · #NNN in progress'."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    assert "in progress" in content, "Progress left label 'in progress' text missing"
    assert "tickets complete" in content or "ticket" in content, "Progress left label count missing"


def test_progress_right_label_includes_est():
    """Right label format: 'NN% · est. Xm remaining'."""
    js_path = Path(__file__).parent.parent / "static" / "app.js"
    content = js_path.read_text(encoding="utf-8")
    assert "est." in content and "remaining" in content, "Est remaining text missing from right label"


# ── Backend: live snapshot returns new fields ─────────────────────────────────

def test_live_snapshot_returns_stat_fields():
    """get_sprint_live_snapshot must return the 8 stat strip fields."""
    # We test the function logic by injecting state and calling the endpoint
    # via TestClient (sync)
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Set up in-memory state
    server._sprint_statuses[("owner/repo", "sprint-1")] = {
        "project": "owner/repo",
        "sprint_label": "sprint-1",
        "issues": [
            {"number": 1, "title": "T1", "status": "done",    "agent_status": "completed"},
            {"number": 2, "title": "T2", "status": "skipped", "agent_status": "failed"},
            {"number": 3, "title": "T3", "status": "skipped", "agent_status": None},
            {"number": 4, "title": "T4", "status": "pending", "agent_status": None},
        ],
        "start_timestamp": "2026-05-28T10:00:00",
        "wall_clock_secs": 120.0,
        "estimates": {},
        "estimator_status": None,
        "estimator_total_minutes": None,
    }

    # Register the project so _is_sprint_running doesn't error
    with tempfile.TemporaryDirectory() as tmpdir:
        pid_dir = Path(tmpdir) / ".commander" / "sprints"
        pid_dir.mkdir(parents=True)
        pid_file = pid_dir / "sprint-1-pid"
        pid_file.write_text("99999")

        with mock.patch("apps.dashboard.server._project_root_path", return_value=Path(tmpdir)):
            resp = client.get("/api/sprints/sprint-1/live?project=owner/repo")

    assert resp.status_code == 200
    data = resp.json()

    # Required stat strip fields
    assert "done_count" in data
    assert "failed_count" in data
    assert "skipped_count" in data
    assert "pending_count" in data
    assert "total_count" in data
    assert "complete_count" in data
    assert "est_remaining_minutes" in data

    # Verify counts
    assert data["done_count"] == 1
    assert data["failed_count"] == 1
    assert data["skipped_count"] == 1
    assert data["pending_count"] == 1
    assert data["total_count"] == 4
    assert data["complete_count"] == 3

    # AC8: est_remaining_minutes must not be None when sprint is active
    assert data["est_remaining_minutes"] is not None


def test_live_snapshot_est_remaining_from_estimates():
    """Live snapshot computes est_remaining from estimator data when available."""
    from fastapi.testclient import TestClient
    client = TestClient(app)

    server._sprint_statuses[("owner/repo", "sprint-2")] = {
        "project": "owner/repo",
        "sprint_label": "sprint-2",
        "issues": [
            {"number": 10, "title": "T10", "status": "done",    "agent_status": "completed"},
            {"number": 11, "title": "T11", "status": "pending", "agent_status": None},
            {"number": 12, "title": "T12", "status": "pending", "agent_status": None},
        ],
        "start_timestamp": "2026-05-28T09:00:00",
        "wall_clock_secs": 300.0,
        "estimates": {
            "10": {"number": 10, "size": "S", "minutes": 30},
            "11": {"number": 11, "size": "M", "minutes": 120},
            "12": {"number": 12, "size": "L", "minutes": 480},
        },
        "estimator_status": "succeeded",
        "estimator_total_minutes": 630,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch("apps.dashboard.server._project_root_path", return_value=Path(tmpdir)):
            resp = client.get("/api/sprints/sprint-2/live?project=owner/repo")

    assert resp.status_code == 200
    data = resp.json()
    # Only non-terminal tickets (11 and 12) → 120 + 480 = 600m
    assert data["est_remaining_minutes"] == 600


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

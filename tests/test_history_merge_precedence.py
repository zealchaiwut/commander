"""History dedup merge must not resurrect completed sprints as actionable."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as h  # noqa: E402


def _rec(label, state, sort_key, source="lifecycle", **extra):
    return {
        "label": label,
        "project": "zealchaiwut/commander",
        "lifecycle_state": state,
        "_sort_key": sort_key,
        "_source": source,
        "issues": extra.get("issues", []),
        **{k: v for k, v in extra.items() if k != "issues"},
    }


def test_completed_lifecycle_beats_newer_failed_history_snapshot():
    completed = _rec("sprint-77", "completed", "2026-06-01T00:00:00+00:00")
    failed_hist = _rec(
        "sprint-77", "failed", "2026-06-15T00:00:00+00:00",
        source="history",
        issues=[{"ticket_id": 1, "state": "merged", "agent_status": "completed"}],
    )
    assert h._merge_history_record(completed, failed_hist)["lifecycle_state"] == "completed"
    assert h._merge_history_record(failed_hist, completed)["lifecycle_state"] == "completed"


def test_newer_lifecycle_draft_beats_stale_history_failed():
    stale = _rec(
        "sprint-99", "failed", "2024-01-01T00:00:00+00:00",
        source="history",
    )
    fresh = _rec("sprint-99", "draft", "2026-06-15T10:00:00+00:00", source="lifecycle")
    winner = h._merge_history_record(stale, fresh)
    assert winner["lifecycle_state"] == "draft"
    assert winner["_source"] == "lifecycle"


def test_bulk_complete_row_not_promoted_by_clear_stale_failure():
    rec = {
        "lifecycle_state": "completed",
        "end_reason": "bulk_complete",
        "failed_tickets": [{"ticket_id": 1}],
        "failure_reason": "stale",
        "issues": [{"state": "merged", "agent_status": "completed", "ticket_id": 1}],
    }
    h._clear_stale_failure_signals(rec)
    assert rec["lifecycle_state"] == "completed"
    assert rec["failed_tickets"]


def test_active_only_excludes_running_and_partial_finished():
    recs = [
        {"label": "sprint-86", "lifecycle_state": "running", "_sort_key": "2026-06-15"},
        {"label": "sprint-77", "lifecycle_state": "partial_finished", "_sort_key": "2026-06-14"},
        {"label": "sprint-77.1", "lifecycle_state": "ready_to_merge", "_sort_key": "2026-06-14"},
        {"label": "sprint-2", "lifecycle_state": "needs_rework", "_sort_key": "2026-06-02"},
    ]
    out = h._filter_active_records(recs, keep_completed=0)
    labels = {r["label"] for r in out}
    assert "sprint-86" not in labels
    assert "sprint-77" not in labels
    assert "sprint-77.1" in labels
    assert "sprint-2" in labels

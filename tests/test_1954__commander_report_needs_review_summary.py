"""Tests for issue #1954: summary.needs_review count in commander report.

The bug: n_attempted = n_completed + n_failed + n_skipped + len(needs_review_list),
but summary only exposed attempted/completed/failed/skipped — no needs_review field.
When any ticket is in needs_review, summary.attempted != completed + failed + skipped,
breaking the add-up invariant.

Fix: add needs_review (int) to the summary object so the arithmetic closes:
  attempted == completed + failed + skipped + needs_review
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def _write_plan(sprints_dir: Path, label: str, **extra):
    data = {"state": "ready_to_merge", "started_at": "2026-01-01T00:00:00+00:00", **extra}
    (sprints_dir / f"{label}-plan.json").write_text(json.dumps(data))
    return data


def _write_state(sprints_dir: Path, label: str, issues: list, **extra):
    data = {"issues": issues, **extra}
    (sprints_dir / f"{label}-state.json").write_text(json.dumps(data))
    return data


class TestSummaryNeedsReviewField:
    """AC: summary object exposes needs_review count so arithmetic always closes."""

    def test_summary_has_needs_review_field(self, tmp_path):
        """needs_review key is present in summary when a ticket is in needs_review status."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "needs_review"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert "needs_review" in payload["summary"], (
            "summary must include a needs_review field"
        )

    def test_summary_needs_review_is_int(self, tmp_path):
        """summary.needs_review is an integer."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "needs_review"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert isinstance(payload["summary"]["needs_review"], int)

    def test_summary_needs_review_count_matches_tickets(self, tmp_path):
        """summary.needs_review equals the number of needs_review tickets."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "needs_review"},
            {"number": 3, "title": "C", "status": "needs_review"},
            {"number": 4, "title": "D", "status": "failed"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert payload["summary"]["needs_review"] == 2

    def test_summary_needs_review_zero_when_none(self, tmp_path):
        """summary.needs_review is 0 when no tickets are in needs_review status."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert payload["summary"]["needs_review"] == 0

    def test_arithmetic_closes_with_needs_review(self, tmp_path):
        """attempted == completed + failed + skipped + needs_review (the invariant)."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
            {"number": 3, "title": "C", "status": "skipped"},
            {"number": 4, "title": "D", "status": "needs_review"},
            {"number": 5, "title": "E", "status": "needs_review"},
            {"number": 6, "title": "F", "status": "pending"},  # not attempted
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        s = payload["summary"]
        assert s["attempted"] == s["completed"] + s["failed"] + s["skipped"] + s["needs_review"], (
            f"attempted={s['attempted']} != completed={s['completed']} + "
            f"failed={s['failed']} + skipped={s['skipped']} + needs_review={s['needs_review']}"
        )

    def test_arithmetic_breaks_without_needs_review_in_summary(self, tmp_path):
        """Demonstrates the original bug: attempted != completed+failed+skipped when
        needs_review tickets exist and needs_review is absent from summary."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "needs_review"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        s = payload["summary"]
        # After the fix, needs_review is in summary and the full equation holds
        assert s["attempted"] == s["completed"] + s["failed"] + s["skipped"] + s["needs_review"]
        # attempted is 2 (done + needs_review), not just 1 (done)
        assert s["attempted"] == 2
        assert s["needs_review"] == 1

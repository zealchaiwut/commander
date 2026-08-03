"""Tests for issue #1953 — build_commander_report reads state.dead_letter as source of truth.

AC coverage:
  AC-a  When state.dead_letter is non-empty, the report dead_letter field uses it verbatim
        (ticket_id, attempts, last_error come from the persisted registry, not status-derived reconstruction).
  AC-b  When state.dead_letter is absent or empty, fall back to reconstructing dead_letter
        from issues with failed/error status using tester_attempt_count and failure_reason.
  AC-c  n_failed (summary.failed) and actions are always derived from per-issue status,
        not from the dead_letter registry (so counts stay correct regardless of source used).
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


# ── AC-a: persisted state.dead_letter is used as source of truth ─────────────

class TestPersistedDeadLetterIsSourceOfTruth:
    """AC-a: when state.dead_letter is populated, report uses it verbatim."""

    def test_persisted_dead_letter_used_over_status_reconstruction(self, tmp_path):
        """Persisted registry has 3 fix_attempts; status-derived would yield tester_attempt_count=1."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-99")
        _write_state(sprints_dir, "sprint-99", [
            {
                "number": 42, "title": "Broken ticket", "status": "failed",
                "tester_attempt_count": 1,  # would yield attempts=1 if reconstructed
                "failure_reason": "old error from status",
            },
        ], dead_letter=[
            {
                "ticket_id": 42, "title": "Broken ticket",
                "attempts": 3,  # authoritative: 3 fix rounds
                "last_error": "pytest: 5 failures (from persisted registry)",
            },
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-99",
            project="owner/repo",
        )
        assert len(payload["dead_letter"]) == 1
        dl = payload["dead_letter"][0]
        assert dl["attempts"] == 3, "Should use fix_attempts from persisted registry, not tester_attempt_count"
        assert dl["last_error"] == "pytest: 5 failures (from persisted registry)", (
            "Should use last_error from persisted registry, not failure_reason"
        )

    def test_persisted_dead_letter_ticket_id_as_reported(self, tmp_path):
        """ticket_id in report matches what the persisted registry stored."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-99")
        _write_state(sprints_dir, "sprint-99", [
            {"number": 7, "title": "T", "status": "failed"},
        ], dead_letter=[
            {"ticket_id": 7, "title": "T", "attempts": 2, "last_error": "gate failed"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-99",
            project="owner/repo",
        )
        assert len(payload["dead_letter"]) == 1
        # ticket_id comes from persisted registry — preserve its type/value
        assert str(payload["dead_letter"][0]["ticket_id"]) == "7"

    def test_multiple_persisted_dead_letter_entries(self, tmp_path):
        """All persisted entries are surfaced in the report."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-99")
        _write_state(sprints_dir, "sprint-99", [
            {"number": 1, "title": "A", "status": "failed"},
            {"number": 2, "title": "B", "status": "error"},
        ], dead_letter=[
            {"ticket_id": 1, "title": "A", "attempts": 3, "last_error": "err A"},
            {"ticket_id": 2, "title": "B", "attempts": 2, "last_error": "err B"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-99",
            project="owner/repo",
        )
        assert len(payload["dead_letter"]) == 2

    def test_persisted_dead_letter_title_preserved(self, tmp_path):
        """title from persisted registry is used."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-99")
        _write_state(sprints_dir, "sprint-99", [
            {"number": 5, "title": "Issue title from issues list", "status": "failed"},
        ], dead_letter=[
            {"ticket_id": 5, "title": "Title from registry", "attempts": 1, "last_error": "err"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-99",
            project="owner/repo",
        )
        dl = payload["dead_letter"][0]
        assert dl["title"] == "Title from registry"


# ── AC-b: fallback to status-derived reconstruction when field absent/empty ───

class TestDeadLetterFallback:
    """AC-b: when state.dead_letter is absent or empty, reconstruct from issues."""

    def test_fallback_when_dead_letter_absent(self, tmp_path):
        """No dead_letter key in state → reconstruct from failed/error issues."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-88")
        _write_state(sprints_dir, "sprint-88", [
            {
                "number": 11, "title": "Old fail", "status": "failed",
                "tester_attempt_count": 2, "failure_reason": "legacy error",
            },
        ])  # no dead_letter key
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-88",
            project="owner/repo",
        )
        assert len(payload["dead_letter"]) == 1
        dl = payload["dead_letter"][0]
        assert dl["ticket_id"] == "11"
        assert dl["attempts"] == 2
        assert dl["last_error"] == "legacy error"

    def test_fallback_when_dead_letter_empty_list(self, tmp_path):
        """Empty dead_letter list in state → reconstruct from failed/error issues."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-88")
        _write_state(sprints_dir, "sprint-88", [
            {
                "number": 22, "title": "Fallback ticket", "status": "error",
                "tester_attempt_count": 4, "failure_reason": "ruff errors",
            },
        ], dead_letter=[])  # empty list → fall back
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-88",
            project="owner/repo",
        )
        assert len(payload["dead_letter"]) == 1
        dl = payload["dead_letter"][0]
        assert dl["attempts"] == 4
        assert dl["last_error"] == "ruff errors"

    def test_fallback_default_attempts_is_one(self, tmp_path):
        """When tester_attempt_count absent in fallback, attempts defaults to 1."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-88")
        _write_state(sprints_dir, "sprint-88", [
            {"number": 33, "title": "No count", "status": "failed"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-88",
            project="owner/repo",
        )
        assert payload["dead_letter"][0]["attempts"] == 1


# ── AC-c: summary counts and actions always from per-issue status ─────────────

class TestCountsAlwaysFromIssueStatus:
    """AC-c: n_failed and actions are always derived from issue status, not dead_letter registry."""

    def test_summary_failed_count_from_issue_status(self, tmp_path):
        """Even when persisted registry exists, summary.failed is counted from issues."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-77")
        _write_state(sprints_dir, "sprint-77", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
            {"number": 3, "title": "C", "status": "failed"},
        ], dead_letter=[
            {"ticket_id": 2, "title": "B", "attempts": 3, "last_error": "err"},
            {"ticket_id": 3, "title": "C", "attempts": 2, "last_error": "err2"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-77",
            project="owner/repo",
        )
        assert payload["summary"]["failed"] == 2
        assert payload["summary"]["completed"] == 1

    def test_actions_from_issue_status_not_registry(self, tmp_path):
        """actions suggest rerun for issues with failed/error status regardless of dead_letter source."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-77")
        _write_state(sprints_dir, "sprint-77", [
            {"number": 55, "title": "Broken", "status": "failed"},
        ], dead_letter=[
            {"ticket_id": 55, "title": "Broken", "attempts": 3, "last_error": "err"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-77",
            project="owner/repo",
        )
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["type"] == "rerun"
        assert payload["actions"][0]["ticket_id"] == "55"

    def test_summary_attempted_not_affected_by_registry(self, tmp_path):
        """summary.attempted is computed from issue statuses, unaffected by dead_letter source."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-77")
        _write_state(sprints_dir, "sprint-77", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
            {"number": 3, "title": "C", "status": "skipped"},
            {"number": 4, "title": "D", "status": "pending"},
        ], dead_letter=[
            {"ticket_id": 2, "title": "B", "attempts": 5, "last_error": "err"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-77",
            project="owner/repo",
        )
        s = payload["summary"]
        assert s["attempted"] == 3  # done + failed + skipped; pending not counted
        assert s["attempted"] == s["completed"] + s["failed"] + s["skipped"]

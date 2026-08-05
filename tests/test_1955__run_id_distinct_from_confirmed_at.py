"""Tests for issue #1955: run_id must be a distinct identifier, not confirmed_at.

AC (from issue body):
- run_id must NOT equal trigger.confirmed_at — they serve different purposes.
- run_id must be a unique identifier (UUID) — two runs starting at the same
  timestamp must not collide.
- The fallback payload (when build_commander_report raises) must also use a
  distinct run_id, not confirmed_at / started_at.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

import os
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


class TestRunIdDistinctFromConfirmedAt:
    """AC: run_id must differ from trigger.confirmed_at."""

    def test_run_id_not_equal_to_confirmed_at(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10", started_at="2026-01-01T12:00:00+00:00")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
            started_at="2026-01-01T12:00:00+00:00",
        )
        assert payload["run_id"] != payload["trigger"]["confirmed_at"], (
            "run_id must be a distinct identifier, not the confirmed_at timestamp"
        )

    def test_run_id_is_valid_uuid(self, tmp_path):
        """run_id must be a UUID string."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
        )
        try:
            uuid.UUID(payload["run_id"])
        except (ValueError, AttributeError):
            pytest.fail(
                f"run_id must be a valid UUID, got: {payload['run_id']!r}"
            )

    def test_confirmed_at_preserved_in_trigger(self, tmp_path):
        """trigger.confirmed_at must still hold the ISO-8601 timestamp."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        ts = "2026-01-01T12:00:00+00:00"
        _write_plan(sprints_dir, "sprint-10", started_at=ts)
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
            started_at=ts,
        )
        assert payload["trigger"]["confirmed_at"] == ts, (
            "trigger.confirmed_at must remain the ISO-8601 start timestamp"
        )

    def test_run_id_unique_across_calls(self, tmp_path):
        """Two consecutive builds must produce different run_id values."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        kwargs = dict(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        p1 = build_commander_report(**kwargs)
        p2 = build_commander_report(**kwargs)
        assert p1["run_id"] != p2["run_id"], (
            "Each build must produce a unique run_id (UUID); got same value twice"
        )

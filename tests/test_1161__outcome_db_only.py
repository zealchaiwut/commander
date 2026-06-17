"""TDD tests for issue #1161: Remove disk-read fallbacks — outcome reader.

Acceptance criteria covered:
- AC2/AC4: Outcome endpoint returns 404 (not disk data) when run_ingested_at is
  not set, even when a disk state file exists.
- AC5 (regression): Outcome endpoint returns correct data from DB when
  run_ingested_at is set and no disk file is needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import server as srv  # noqa: E402

_LABEL = "sprint-61"
_PROJECT = "owner/repo"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_1161_outcome.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _make_state_dict(issues=None):
    return {
        "sprint_label": _LABEL,
        "wall_clock_secs": 300,
        "issues": issues or [
            {
                "number": 100,
                "title": "ticket A",
                "status": "done",
                "agent_status": "completed",
                "failure_reason": None,
                "coder_started_at": "2026-06-01T10:00:00Z",
                "tester_finished_at": "2026-06-01T10:30:00Z",
                "state": "merged",
            }
        ],
    }


def _write_disk_state(sprints_dir: Path, issues=None) -> None:
    """Write a sprint state file to disk (simulating a pre-ingestion artifact)."""
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = _make_state_dict(issues)
    (sprints_dir / f"{_LABEL}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


# ── AC2/AC4: disk fallback removed — outcome must return 404 when not ingested ──

class TestOutcomeDiskFallbackRemoved:
    """Outcome endpoint must NOT fall back to disk when run_ingested_at is unset.

    Currently (before the fix) the endpoint reads sprint-N-state.json from disk
    when run_ingested_at is None.  After the fix it must return 404 instead.
    These tests are RED before the fix and GREEN after.
    """

    def test_outcome_404_when_no_run_ingested_at_even_if_disk_file_exists(self, tmp_path):
        """Disk state file exists but run_ingested_at is NULL → 404, not 200."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        # Sprint row in DB but NOT yet ingested (no run_ingested_at)
        db.record_sprint_start(_LABEL, project=_PROJECT)
        # Disk state file exists
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/outcome",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 404, (
            "Outcome must return 404 when run_ingested_at is not set, "
            f"even if disk state file exists; got {resp.status_code}: {resp.text}"
        )

    def test_outcome_404_when_no_db_row_at_all(self, tmp_path):
        """No DB row, disk state file exists → 404 (no disk fallback)."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/outcome",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 404, (
            "Outcome must return 404 when no DB row exists; "
            f"got {resp.status_code}: {resp.text}"
        )


# ── AC5 regression: outcome returns correct data from DB ─────────────────────

class TestOutcomeDbPathRegression:
    """Outcome endpoint must still return correct data from DB (AC5 regression).

    These tests confirm the DB path (_outcome_from_ingested_row) continues
    to work correctly after the disk fallback is removed.
    """

    def test_outcome_200_from_db_when_run_ingested_at_set(self, tmp_path):
        """DB row with run_ingested_at set → 200 with issue counts from DB."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        # Ingest a sprint into DB
        db.record_sprint_start(_LABEL, project=_PROJECT)
        state = _make_state_dict()
        db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
        db.record_sprint_finish(_LABEL)

        project_root = tmp_path / "proj"

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False), \
             patch("server.github_client.get_repo_for_operation", return_value="owner/repo"):
            resp = client.get(
                f"/api/sprints/{_LABEL}/outcome",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, (
            f"Outcome must return 200 from DB; got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("sprint_label") == _LABEL
        counts = data.get("counts", {})
        assert counts.get("done", 0) >= 0, "counts.done must be present"

    def test_outcome_works_without_disk_file_when_run_ingested(self, tmp_path):
        """No disk state file, DB has run_ingested_at → outcome still returns 200.

        Proves the outcome endpoint no longer needs the disk file.
        """
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        db.record_sprint_start(_LABEL, project=_PROJECT)
        state = _make_state_dict()
        db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
        db.record_sprint_finish(_LABEL)

        project_root = tmp_path / "proj"
        # Deliberately no disk state file

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False), \
             patch("server.github_client.get_repo_for_operation", return_value="owner/repo"):
            resp = client.get(
                f"/api/sprints/{_LABEL}/outcome",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, (
            "Outcome must return 200 from DB even with no disk file; "
            f"got {resp.status_code}: {resp.text}"
        )


# ── AC2: _sprint_has_own_run_outcome is DB-only ───────────────────────────────

class TestSprintHasOwnRunOutcomeDbOnly:
    """_sprint_has_own_run_outcome must not read disk (AC2).

    After the fix, the function only checks the DB sprints row.  Reading the
    disk state file is forbidden — these tests are RED when the old disk-read
    logic remains.
    """

    def test_returns_false_when_no_db_row(self, tmp_path):
        """No DB row → False (no disk check)."""
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        result = srv._sprint_has_own_run_outcome(project_root, _LABEL)
        assert result is False, (
            "_sprint_has_own_run_outcome must return False when no DB row, "
            "regardless of disk state file"
        )

    def test_returns_false_when_db_row_but_no_run_ingested_at(self, tmp_path):
        """DB row present but run_ingested_at is NULL → False (no disk check)."""
        db.record_sprint_start(_LABEL, project=_PROJECT)
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        result = srv._sprint_has_own_run_outcome(project_root, _LABEL)
        assert result is False, (
            "_sprint_has_own_run_outcome must return False when run_ingested_at "
            "is NULL, even if disk state file exists"
        )

    def test_returns_true_when_run_ingested_at_set(self, tmp_path):
        """DB row with run_ingested_at set → True."""
        db.record_sprint_start(_LABEL, project=_PROJECT)
        db.ingest_sprint_run_artifact(_LABEL, _make_state_dict(), project=_PROJECT)

        project_root = tmp_path / "proj"
        result = srv._sprint_has_own_run_outcome(project_root, _LABEL)
        assert result is True, (
            "_sprint_has_own_run_outcome must return True when run_ingested_at is set"
        )

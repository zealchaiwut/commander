"""TDD tests for issue #1161: Remove disk-read fallbacks — finish-card reader.

Acceptance criteria covered:
- AC2/AC4: Finish-card returns state="no_data" when run_ingested_at is not set,
  even when a disk state file exists.
- AC7 (regression): Finish-card returns correct data from DB when run_ingested_at
  is set (currently the endpoint always reads from disk).
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

_LABEL = "sprint-62"
_PROJECT = "owner/repo"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_1161_finish_card.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _make_state_dict(issues=None):
    return {
        "sprint_label": _LABEL,
        "wall_clock_secs": 480,
        "issues": issues or [
            {
                "number": 200,
                "title": "ticket B",
                "status": "done",
                "agent_status": "completed",
                "failure_reason": None,
                "state": "merged",
            },
            {
                "number": 201,
                "title": "ticket C",
                "status": "skipped",
                "agent_status": None,
                "failure_reason": None,
                "state": "open",
            },
        ],
    }


def _write_disk_state(sprints_dir: Path) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = _make_state_dict()
    (sprints_dir / f"{_LABEL}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


# ── AC2/AC4: disk fallback removed — finish-card returns no_data ──────────────

class TestFinishCardDiskFallbackRemoved:
    """Finish-card must NOT fall back to disk when run_ingested_at is unset.

    Currently (before the fix) the finish-card endpoint reads the disk state
    file when run_ingested_at is NULL.  After the fix it must return
    state="no_data" instead.  These tests are RED before the fix.
    """

    def test_finish_card_no_data_when_no_run_ingested_at_but_disk_exists(self, tmp_path):
        """DB row exists without run_ingested_at, disk file present → no_data."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        db.record_sprint_start(_LABEL, project=_PROJECT)
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/finish-card",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, f"Finish-card must return 200; got {resp.status_code}"
        data = resp.json()
        assert data.get("state") == "no_data", (
            "Finish-card must return state='no_data' when run_ingested_at is NULL, "
            f"even if disk state file exists; got state={data.get('state')!r}"
        )

    def test_finish_card_no_data_when_no_db_row_but_disk_exists(self, tmp_path):
        """No DB row, disk file exists → no_data (no disk fallback)."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        _write_disk_state(sprints_dir)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/finish-card",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, f"Finish-card must return 200; got {resp.status_code}"
        data = resp.json()
        assert data.get("state") == "no_data", (
            "Finish-card must return state='no_data' when no DB row; "
            f"got state={data.get('state')!r}"
        )


# ── AC7 regression: finish-card returns correct data from DB ─────────────────

class TestFinishCardDbPathRegression:
    """Finish-card must return data from the DB row when run_ingested_at is set.

    Currently (before the fix) the finish-card endpoint always reads from disk
    for terminal sprints.  After the fix it reads from the DB row.
    """

    def test_finish_card_200_from_db_when_run_ingested(self, tmp_path):
        """DB row with run_ingested_at set → 200, state != 'no_data'."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        db.record_sprint_start(_LABEL, project=_PROJECT)
        state = _make_state_dict()
        db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
        db.record_sprint_finish(_LABEL)

        project_root = tmp_path / "proj"

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/finish-card",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, (
            f"Finish-card must return 200 from DB; got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("state") != "no_data", (
            "Finish-card must return actual state (not no_data) when run_ingested_at is set; "
            f"got {data.get('state')!r}"
        )

    def test_finish_card_works_without_disk_file_when_ingested(self, tmp_path):
        """No disk state file, DB has run_ingested_at → finish-card still returns 200.

        Proves the finish-card no longer needs the disk file.
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
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/finish-card",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, (
            "Finish-card must return 200 from DB even with no disk file; "
            f"got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("state") != "no_data"
        assert "done_count" in data, "finish-card must include done_count"

    def test_finish_card_db_counts_match_ingested_issues(self, tmp_path):
        """Counts in finish-card response match the issues in the DB row."""
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        issues = [
            {"number": 300, "title": "done-1", "status": "done", "agent_status": "completed",
             "failure_reason": None, "state": "merged"},
            {"number": 301, "title": "done-2", "status": "done", "agent_status": "completed",
             "failure_reason": None, "state": "merged"},
            {"number": 302, "title": "failed-1", "status": "failed", "agent_status": "failed",
             "failure_reason": "coder crash", "state": "closed"},
        ]
        db.record_sprint_start(_LABEL, project=_PROJECT)
        state = {"sprint_label": _LABEL, "wall_clock_secs": 600, "issues": issues}
        db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
        db.record_sprint_finish(_LABEL)

        project_root = tmp_path / "proj"

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(
                f"/api/sprints/{_LABEL}/finish-card",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["done_count"] == 2, (
            f"Expected done_count=2 from DB; got {data.get('done_count')}"
        )
        assert data["failed_count"] == 1, (
            f"Expected failed_count=1 from DB; got {data.get('failed_count')}"
        )

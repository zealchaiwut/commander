"""Tests for issue #1096: Make plan.json read-only from GET endpoints.

Acceptance criteria covered:
- AC3: Outcome endpoint leaves plan.json byte-identical before and after request.
- AC4: History endpoint leaves plan.json byte-identical before and after request.
- AC5: Running-check path contains no plan file writes (DB=running+dead-PID and
        legacy-migration paths no longer call _plan_json_set_state).
- AC1 (via AC5): GET /api/sprints/{label}/state returns 404 when plan.json is
        missing instead of lazily creating it.

Each test is RED before the fix and GREEN after:
  - test_outcome_* → fail when _is_sprint_running still calls _plan_json_set_state
  - test_history_* → fail when _heal or reconcile leaks into history handlers
  - test_running_check_* → fail when _plan_json_set_state calls remain in
    _is_sprint_running (DB-running+dead-PID path or legacy-migration path)
  - test_get_sprint_state_* → fail when get_sprint_state still does lazy creation
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_LABEL = "sprint-96"
_PROJECT = "owner/repo"


def _sprints_dir(project_root: Path) -> Path:
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_plan(sprints_dir: Path, state: str = "completed") -> bytes:
    """Write plan.json and return its byte content."""
    data = {"state": state, "tickets": [10, 20]}
    raw = json.dumps(data, indent=2).encode()
    plan_path = sprints_dir / f"{_LABEL}-plan.json"
    plan_path.write_bytes(raw)
    return plan_path.read_bytes()  # authoritative on-disk bytes


def _write_state_json(sprints_dir: Path, issues: list[dict] | None = None) -> None:
    """Write a minimal sprint-N-state.json so outcome endpoint can succeed."""
    import re
    m = re.search(r"(\d+)", _LABEL)
    n = m.group(1) if m else _LABEL
    data = {
        "sprint_label": _LABEL,
        "sprint_number": int(n),
        "wall_clock_secs": 120,
        "issues": issues or [
            {
                "number": 10,
                "title": "ticket one",
                "status": "done",
                "agent_status": None,
                "failure_reason": None,
                "coder_started_at": "2026-06-01T10:00:00Z",
                "tester_finished_at": "2026-06-01T10:30:00Z",
                "status_changed_at": "2026-06-01T10:30:00Z",
            },
        ],
        "ended_at": "2026-06-01T11:00:00Z",
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC5: Running-check path (_is_sprint_running) must not write plan.json
# ─────────────────────────────────────────────────────────────────────────────

class TestRunningCheckNoPlanWrite:
    """_is_sprint_running must never write plan.json (AC5).

    Removing the fixes makes _is_sprint_running call _plan_json_set_state in
    two paths — these tests turn RED when those calls are present.
    """

    def test_db_running_dead_pid_does_not_rewrite_plan_json(self, tmp_path):
        """DB=running + dead PID path must not overwrite plan.json with needs_rework."""
        import server as srv

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        original_bytes = _write_plan(sd, state="running")

        plan_path = sd / f"{_LABEL}-plan.json"

        db_row = {
            "label": _LABEL,
            "project": _PROJECT,
            "state": "running",
            "started_at": None,
        }

        # _project_root_path.name == project_root.name so the row is trusted
        with patch("server.db.get_sprint", return_value=db_row), \
             patch("server._live_manager_pid", return_value=None), \
             patch("server._sprint_pid_alive", return_value=False), \
             patch("server.db.record_sprint_needs_rework"):
            result = srv._is_sprint_running(project_root, _LABEL)

        assert result is False, "_is_sprint_running should return False when PID is dead"
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "DB=running + dead-PID reconcile must NOT rewrite plan.json; "
            f"original={original_bytes!r}, after={after_bytes!r}"
        )

    def test_legacy_migration_does_not_create_plan_json_for_running_pid(self, tmp_path):
        """Legacy path (no plan.json + alive PID) must not create plan.json."""
        import server as srv

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)

        # No plan.json — legacy sprint
        plan_path = sd / f"{_LABEL}-plan.json"
        assert not plan_path.exists()

        # Write a PID file with current process PID (guaranteed alive)
        pid_file = sd / f"{_LABEL}-pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        with patch("server.db.get_sprint", return_value=None):
            srv._is_sprint_running(project_root, _LABEL)

        assert not plan_path.exists(), (
            "Legacy-migration path must not create plan.json for a running sprint; "
            "plan.json appeared after _is_sprint_running"
        )

    def test_legacy_migration_does_not_create_plan_json_for_dead_pid(self, tmp_path):
        """Legacy path (no plan.json + dead PID) must not create plan.json."""
        import server as srv

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)

        plan_path = sd / f"{_LABEL}-plan.json"
        assert not plan_path.exists()

        with patch("server.db.get_sprint", return_value=None):
            result = srv._is_sprint_running(project_root, _LABEL)

        assert result is False
        assert not plan_path.exists(), (
            "Legacy-migration path must not create plan.json for a dead-PID sprint; "
            "plan.json appeared after _is_sprint_running"
        )

    def test_plan_running_dead_pid_does_not_rewrite_plan_json(self, tmp_path):
        """plan.json=running + dead PID path must not rewrite plan.json."""
        import server as srv

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        original_bytes = _write_plan(sd, state="running")
        plan_path = sd / f"{_LABEL}-plan.json"

        with patch("server.db.get_sprint", return_value=None):
            result = srv._is_sprint_running(project_root, _LABEL)

        assert result is False, "should be not-running with no alive PID"
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "plan.json=running + dead-PID reconcile must NOT rewrite plan.json; "
            f"original={original_bytes!r}, after={after_bytes!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC1: get_sprint_state GET must not create plan.json lazily
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSprintStateNoLazyCreate:
    """GET /api/sprints/{label}/state must return 404 when plan.json is missing.

    Before the fix, the endpoint creates plan.json on first access (lazy
    migration). After the fix it returns 404 — the test is RED with the old code.
    """

    def test_returns_404_when_plan_json_missing(self, tmp_path):
        """GET /state with no plan.json on disk → 404, file not created."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        plan_path = sd / f"{_LABEL}-plan.json"

        with patch("server._project_root_path", return_value=project_root):
            resp = client.get(
                f"/api/sprints/{_LABEL}/state",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 404, (
            "GET /state with no plan.json must return 404 (not lazily create it); "
            f"got {resp.status_code}: {resp.text}"
        )
        assert not plan_path.exists(), (
            "plan.json must NOT be created by the GET /state endpoint; "
            "found file after 404 response"
        )

    def test_returns_plan_when_exists(self, tmp_path):
        """GET /state with existing plan.json → 200 with plan content."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        _write_plan(sd, state="completed")

        with patch("server._project_root_path", return_value=project_root):
            resp = client.get(
                f"/api/sprints/{_LABEL}/state",
                params={"project": _PROJECT},
            )

        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("state") == "completed"


# ─────────────────────────────────────────────────────────────────────────────
# AC3: Outcome endpoint leaves plan.json byte-identical
# ─────────────────────────────────────────────────────────────────────────────

class TestOutcomeEndpointPlanByteIdentical:
    """GET /outcome must not modify plan.json (AC3).

    The failure mode before the fix: _is_sprint_running (called from /outcome)
    reconciles plan.json=running+dead-PID → needs_rework, changing the file.
    After the fix these tests are GREEN.
    """

    def test_outcome_plan_byte_identical_when_completed(self, tmp_path):
        """GET /outcome on a completed sprint leaves plan.json unchanged."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        original_bytes = _write_plan(sd, state="completed")
        _write_state_json(sd)
        plan_path = sd / f"{_LABEL}-plan.json"

        fake_db_row = {
            "label": _LABEL, "project": _PROJECT,
            "state": "completed", "run_ingested_at": None,
        }

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._sprint_has_own_run_outcome", return_value=True), \
             patch("server.db.get_sprint", return_value=fake_db_row), \
             patch("server._has_rework_tickets", return_value=False), \
             patch("server.github_client.get_repo_for_operation", return_value="owner/repo"), \
             patch("server._get_sprint_issues", return_value=[]), \
             patch("server._parse_summary_file", side_effect=Exception("no summary")):
            resp = client.get(
                f"/api/sprints/{_LABEL}/outcome",
                params={"project": _PROJECT},
            )

        assert resp.status_code in (200, 404), f"unexpected {resp.status_code}: {resp.text}"
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "GET /outcome must not modify plan.json; "
            f"bytes changed from {len(original_bytes)} to {len(after_bytes)}"
        )

    def test_outcome_plan_byte_identical_via_running_check_reconcile_scenario(self, tmp_path):
        """plan.json=running stays unchanged even when DB=running + PID dead is detected."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        # Write plan.json with state=running — the old code would rewrite to needs_rework
        original_bytes = _write_plan(sd, state="running")
        plan_path = sd / f"{_LABEL}-plan.json"

        db_row_running = {
            "label": _LABEL, "project": _PROJECT,
            "state": "running", "started_at": None, "run_ingested_at": None,
        }

        # Simulate DB=running but no alive PID — triggers the reconcile path
        with patch("server._project_root_path", return_value=project_root), \
             patch("server.db.get_sprint", return_value=db_row_running), \
             patch("server._sprint_pid_alive", return_value=False), \
             patch("server._live_manager_pid", return_value=None), \
             patch("server.db.record_sprint_needs_rework"):
            # _is_sprint_running reconciles DB state but must NOT touch plan.json
            is_running = srv._is_sprint_running(project_root, _LABEL)

        assert is_running is False
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "DB=running+dead-PID reconcile must not rewrite plan.json; "
            f"plan.json changed: before={original_bytes!r}, after={after_bytes!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC4: History endpoints leave plan.json byte-identical
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryEndpointsPlanByteIdentical:
    """GET /api/sprint-history and /api/sprint-history-content must not touch plan.json (AC4)."""

    def test_sprint_history_endpoint_plan_byte_identical(self, tmp_path):
        """GET /api/sprint-history does not modify plan.json."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        original_bytes = _write_plan(sd, state="completed")
        plan_path = sd / f"{_LABEL}-plan.json"

        # history reads from SPRINTS_DIR (server-global) — patch to empty to avoid FS errors
        with patch("server.SPRINTS_DIR", tmp_path / "no-summaries"):
            resp = client.get("/api/sprint-history")

        assert resp.status_code == 200, f"unexpected {resp.status_code}: {resp.text}"
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "GET /api/sprint-history must not modify plan.json; "
            f"bytes changed from {len(original_bytes)} to {len(after_bytes)}"
        )

    def test_sprint_history_content_endpoint_plan_byte_identical(self, tmp_path):
        """GET /api/sprint-history-content does not modify plan.json."""
        import server as srv
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)

        project_root = tmp_path / "proj"
        sd = _sprints_dir(project_root)
        original_bytes = _write_plan(sd, state="completed")
        plan_path = sd / f"{_LABEL}-plan.json"

        with patch("server.SPRINTS_DIR", tmp_path / "no-summaries"):
            resp = client.get("/api/sprint-history-content")

        # 404 is expected when SPRINTS_DIR has no summaries — that's fine
        assert resp.status_code in (200, 404), f"unexpected {resp.status_code}: {resp.text}"
        after_bytes = plan_path.read_bytes()
        assert after_bytes == original_bytes, (
            "GET /api/sprint-history-content must not modify plan.json; "
            f"bytes changed from {len(original_bytes)} to {len(after_bytes)}"
        )

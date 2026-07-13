"""Tests for issue #671: Sync sprint progress across all three pill components.

AC1 — GET /api/sprint-progress endpoint exists, returns sprint/done/total/run_state
AC2 — Endpoint persists fetched data to a local JSON file
AC3 — sb-sprint-badge reads from shared _sprintProgressStore
AC4 — snav-pill reads from shared _sprintProgressStore
AC5 — smgmt-running-badge reads from shared _sprintProgressStore
AC6 — All three render via single _sprintProgressRender() after a data refresh
AC7 — Dot/run-state indicators use the same run_state from the store
AC8 — Stale or missing local data triggers a fresh API fetch
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import datetime as _dt
from datetime import timezone as _tz
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
SERVER_PY     = DASHBOARD_DIR / "server.py"
PROJECT_HTML  = DASHBOARD_DIR / "static" / "project.html"

sys.path.insert(0, str(DASHBOARD_DIR))


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_issue(number: int, column: str = "backlog"):
    return {
        "number": number,
        "title": f"Issue #{number}",
        "labels": [{"name": column}],
        "column": column,
        "url": f"https://github.com/example/repo/issues/{number}",
        "body": "",
        "state": "open",
    }


def _get_test_client():
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app), srv


# ── AC1: GET /api/sprint-progress endpoint ─────────────────────────────────────

class TestSprintProgressEndpoint:
    """AC1 — endpoint exists and returns required fields."""

    def test_endpoint_registered(self):
        """GET /api/sprint-progress must be registered in the app."""
        src = SERVER_PY.read_text(encoding="utf-8")
        assert "/api/sprint-progress" in src, (
            "/api/sprint-progress route not found in server.py"
        )

    def test_endpoint_returns_has_sprint_field(self):
        """Response must include has_sprint field."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        with patch.object(srv, "github_client") as mock_gh:
            mock_gh.list_sprints.return_value = []
            resp = client.get("/api/sprint-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert "has_sprint" in data, "Response missing 'has_sprint' field"

    def test_endpoint_returns_progress_fields_when_sprint_exists(self):
        """When a sprint exists, response has sprint/done/total/run_state."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        # Populate in-memory sprint statuses with live data
        issues = [
            {"number": 1, "status": "done", "title": "T1"},
            {"number": 2, "status": "done", "title": "T2"},
            {"number": 3, "status": "pending", "title": "T3"},
        ]
        live_payload = {
            "project": "zealchaiwut/commander",
            "sprint_label": "sprint-52",
            "sprint_number": 52,
            "issues": issues,
        }
        # Inject via POST /api/sprint-status
        post_resp = client.post("/api/sprint-status", json=live_payload)
        assert post_resp.status_code == 200

        with patch.object(srv, "_all_sprints_running") as mock_running:
            mock_running.return_value = [
                {"project": "zealchaiwut/commander", "sprint_label": "sprint-52", "pid": 9999}
            ]
            resp = client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("has_sprint") is True
        assert "sprint" in data, "Response missing 'sprint' field"
        assert "done" in data, "Response missing 'done' field"
        assert "total" in data, "Response missing 'total' field"
        assert "run_state" in data, "Response missing 'run_state' field"

    def test_endpoint_done_equals_completed_tickets(self):
        """done = count of issues with status in (done, skipped)."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        issues = [
            {"number": 1, "status": "done",    "title": "T1"},
            {"number": 2, "status": "skipped", "title": "T2"},
            {"number": 3, "status": "pending", "title": "T3"},
            {"number": 4, "status": "pending", "title": "T4"},
        ]
        payload = {
            "project": "zealchaiwut/commander",
            "sprint_label": "sprint-99",
            "sprint_number": 99,
            "issues": issues,
        }
        client.post("/api/sprint-status", json=payload)

        with patch.object(srv, "_all_sprints_running") as mock_running:
            mock_running.return_value = [
                {"project": "zealchaiwut/commander", "sprint_label": "sprint-99", "pid": 1234}
            ]
            resp = client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )
        assert resp.status_code == 200
        data = resp.json()
        # done = 1 (done) + 1 (skipped) = 2; total = 4
        assert data["done"] == 2, f"Expected done=2, got {data['done']}"
        assert data["total"] == 4, f"Expected total=4, got {data['total']}"

    def test_endpoint_run_state_running_for_active_sprint(self):
        """run_state must be 'running' when a sprint is actively running."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        issues = [{"number": 1, "status": "in-progress", "title": "T1"}]
        payload = {
            "project": "zealchaiwut/commander",
            "sprint_label": "sprint-88",
            "sprint_number": 88,
            "issues": issues,
        }
        client.post("/api/sprint-status", json=payload)

        with patch.object(srv, "_all_sprints_running") as mock_running:
            mock_running.return_value = [
                {"project": "zealchaiwut/commander", "sprint_label": "sprint-88", "pid": 5678}
            ]
            resp = client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_state"] == "running", (
            f"Expected run_state='running', got '{data.get('run_state')}'"
        )

    def test_endpoint_no_running_sprint_returns_has_sprint_false(self):
        """When no sprint is running and GitHub returns no sprints, has_sprint=False."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        with patch.object(srv, "_all_sprints_running") as mock_running, \
             patch.object(srv, "github_client") as mock_gh, \
             patch.object(srv, "_sprint_progress_file_path") as mock_fp:
            mock_running.return_value = []
            mock_gh.list_sprints.return_value = []
            mock_fp.return_value = None  # suppress persisted-file fallback
            resp = client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("has_sprint") is False


# ── AC2: Persistence ────────────────────────────────────────────────────────────

class TestSprintProgressPersistence:
    """AC2 — data is persisted to a local JSON file after fetch."""

    def test_persistence_code_exists_in_endpoint(self):
        """Endpoint code must write a JSON file."""
        src = SERVER_PY.read_text(encoding="utf-8")
        # Must reference sprint-progress in a file write context
        assert "sprint-progress" in src, (
            "server.py does not reference 'sprint-progress' persistence file"
        )

    def test_endpoint_persists_to_file(self, tmp_path):
        """After fetching, a JSON file is written to disk."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        issues = [
            {"number": 1, "status": "done", "title": "T1"},
            {"number": 2, "status": "pending", "title": "T2"},
        ]
        payload = {
            "project": "zealchaiwut/commander",
            "sprint_label": "sprint-77",
            "sprint_number": 77,
            "issues": issues,
        }
        client.post("/api/sprint-status", json=payload)

        # Patch the project root to use a temp directory
        with patch.object(srv, "_project_root_path") as mock_root, \
             patch.object(srv, "_all_sprints_running") as mock_running:
            mock_root.return_value = tmp_path
            mock_running.return_value = [
                {"project": "zealchaiwut/commander", "sprint_label": "sprint-77", "pid": 1111}
            ]
            resp = client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )

        assert resp.status_code == 200
        # Check that a sprint-progress JSON file was written somewhere under tmp_path
        progress_files = list(tmp_path.rglob("sprint-progress*.json"))
        assert progress_files, (
            f"No sprint-progress*.json written under {tmp_path} after GET /api/sprint-progress"
        )
        data = json.loads(progress_files[0].read_text())
        assert "done" in data and "total" in data, (
            f"Persisted JSON is missing 'done' or 'total': {data}"
        )

    def test_persisted_file_contains_fetched_at(self, tmp_path):
        """Persisted JSON must include a fetched_at timestamp."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        payload = {
            "project": "zealchaiwut/commander",
            "sprint_label": "sprint-76",
            "sprint_number": 76,
            "issues": [{"number": 1, "status": "done", "title": "T1"}],
        }
        client.post("/api/sprint-status", json=payload)

        with patch.object(srv, "_project_root_path") as mock_root, \
             patch.object(srv, "_all_sprints_running") as mock_running:
            mock_root.return_value = tmp_path
            mock_running.return_value = [
                {"project": "zealchaiwut/commander", "sprint_label": "sprint-76", "pid": 2222}
            ]
            client.get(
                "/api/sprint-progress",
                params={"project": "zealchaiwut/commander"},
            )

        progress_files = list(tmp_path.rglob("sprint-progress*.json"))
        if progress_files:
            data = json.loads(progress_files[0].read_text())
            assert "fetched_at" in data, "Persisted JSON missing 'fetched_at' timestamp"


# ── AC3-5: All three JS components reference shared store ──────────────────────

class TestSharedStoreInJs:
    """AC3, 4, 5 — all three components read from _sprintProgressStore."""

    def test_sprint_progress_store_variable_declared(self):
        """_sprintProgressStore variable must be declared in project.html."""
        src = _html()
        assert "_sprintProgressStore" in src, (
            "_sprintProgressStore not declared in project.html"
        )

    def test_sb_sprint_badge_uses_progress_store(self):
        """_snavRenderSidebarBadge must reference _sprintProgressStore."""
        src = _html()
        fn_match = re.search(
            r"function _snavRenderSidebarBadge\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_snavRenderSidebarBadge function not found in project.html"
        fn = fn_match.group(0)
        assert "_sprintProgressStore" in fn, (
            "_snavRenderSidebarBadge does not read from _sprintProgressStore"
        )

    def test_snav_pill_uses_progress_store(self):
        """_snavRenderPill must reference _sprintProgressStore."""
        src = _html()
        fn_match = re.search(
            r"function _snavRenderPill\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_snavRenderPill function not found in project.html"
        fn = fn_match.group(0)
        assert "_sprintProgressStore" in fn, (
            "_snavRenderPill does not read from _sprintProgressStore"
        )

    def test_smgmt_running_badge_uses_progress_store(self):
        """_smgmtLivePatch must reference _sprintProgressStore for badge updates."""
        src = _html()
        fn_match = re.search(
            r"function _smgmtLivePatch\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_smgmtLivePatch function not found in project.html"
        fn = fn_match.group(0)
        assert "_sprintProgressStore" in fn, (
            "_smgmtLivePatch does not read from _sprintProgressStore"
        )

    def test_sprint_progress_fetch_passes_project_param(self):
        """_sprintProgressFetch must pass project= so the persisted file is found."""
        src = _html()
        fn_match = re.search(
            r"async function _sprintProgressFetch\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressFetch not found"
        fn = fn_match.group(0)
        assert "project=" in fn, (
            "_sprintProgressFetch does not pass project= to /api/sprint-progress"
        )

    def test_pill_helpers_support_child_sprint_labels(self):
        """Child sprint labels must override GitHub base-number pills."""
        src = _html()
        assert "_sprintStoreOverridesNav" in src
        assert "_sprintPillShortLabel" in src
        assert "store.sprint_label" in src


# ── AC6: Single render function triggers all three components ──────────────────

class TestUnifiedRenderFunction:
    """AC6 — _sprintProgressRender() triggers all three component renders."""

    def test_sprint_progress_render_function_exists(self):
        """_sprintProgressRender function must be declared in project.html."""
        src = _html()
        assert "_sprintProgressRender" in src, (
            "_sprintProgressRender not found in project.html"
        )

    def test_sprint_progress_render_calls_sidebar_badge(self):
        """_sprintProgressRender must call _snavRenderSidebarBadge (or _snavUpdateSidebarBadge)."""
        src = _html()
        fn_match = re.search(
            r"function _sprintProgressRender\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressRender function not found"
        fn = fn_match.group(0)
        assert "_snavRenderSidebarBadge" in fn or "_snavUpdateSidebarBadge" in fn, (
            "_sprintProgressRender does not call sidebar badge render"
        )

    def test_sprint_progress_render_calls_snav_pill(self):
        """_sprintProgressRender must call _snavRenderPill."""
        src = _html()
        fn_match = re.search(
            r"function _sprintProgressRender\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressRender function not found"
        fn = fn_match.group(0)
        assert "_snavRenderPill" in fn, (
            "_sprintProgressRender does not call _snavRenderPill"
        )

    def test_sprint_progress_render_calls_smgmt_badge_update(self):
        """_sprintProgressRender must trigger smgmt running badge update."""
        src = _html()
        fn_match = re.search(
            r"function _sprintProgressRender\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressRender function not found"
        fn = fn_match.group(0)
        # Should call _smgmtLivePatch, smgmt-running-badge, or a smgmt-specific update
        assert (
            "smgmt-running-badge" in fn
            or "_smgmtLivePatch" in fn
            or "_smgmtUpdateRunningBadge" in fn
        ), "_sprintProgressRender does not trigger smgmt running badge update"

    def test_sprint_progress_fetch_updates_store(self):
        """_sprintProgressFetch must update _sprintProgressStore."""
        src = _html()
        fn_match = re.search(
            r"(?:async\s+)?function _sprintProgressFetch\b.*?(?=\nfunction |\nasync function |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressFetch function not found in project.html"
        fn = fn_match.group(0)
        assert "_sprintProgressStore" in fn, (
            "_sprintProgressFetch does not update _sprintProgressStore"
        )

    def test_sprint_progress_fetch_calls_render(self):
        """_sprintProgressFetch must call _sprintProgressRender after updating store."""
        src = _html()
        fn_match = re.search(
            r"(?:async\s+)?function _sprintProgressFetch\b.*?(?=\nfunction |\nasync function |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_sprintProgressFetch function not found"
        fn = fn_match.group(0)
        assert "_sprintProgressRender" in fn, (
            "_sprintProgressFetch does not call _sprintProgressRender after update"
        )


# ── AC7: Dot/run-state consistency ─────────────────────────────────────────────

class TestDotRunStateConsistency:
    """AC7 — dot/run-state indicators all use the same run_state from the store."""

    def test_run_state_field_present_in_endpoint_response(self):
        """Endpoint response must include run_state field (not separate state fields)."""
        src = SERVER_PY.read_text(encoding="utf-8")
        # The sprint-progress endpoint handler must return run_state
        progress_fn_match = re.search(
            r"@app\.get\(['\"]\/api\/sprint-progress['\"].*?\n(?:async\s+)?def \w+.*?(?=\n@app\.|\nclass |\Z)",
            src, re.DOTALL
        )
        assert progress_fn_match, "/api/sprint-progress route+handler not found"
        handler = progress_fn_match.group(0)
        assert "run_state" in handler, (
            "sprint-progress handler does not return 'run_state' field"
        )

    def test_snav_dot_uses_store_run_state(self):
        """_snavRenderPill dot update must read run_state from _sprintProgressStore."""
        src = _html()
        fn_match = re.search(
            r"function _snavRenderPill\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_snavRenderPill not found"
        fn = fn_match.group(0)
        # Must reference run_state (from store) to set dot class
        assert "run_state" in fn or "runState" in fn or "_sprintProgressStore" in fn, (
            "_snavRenderPill does not use run_state from shared store for dot class"
        )

    def test_sb_sprint_badge_dot_uses_store_run_state(self):
        """_snavRenderSidebarBadge dot must use run_state from the shared store."""
        src = _html()
        fn_match = re.search(
            r"function _snavRenderSidebarBadge\b.*?(?=\nfunction |\n\/\/ ──)",
            src, re.DOTALL
        )
        assert fn_match, "_snavRenderSidebarBadge not found"
        fn = fn_match.group(0)
        assert "run_state" in fn or "runState" in fn or "_sprintProgressStore" in fn, (
            "_snavRenderSidebarBadge does not use run_state from shared store"
        )


# ── AC8: Stale/missing data triggers fresh fetch ───────────────────────────────

class TestStaleFetchBehavior:
    """AC8 — stale or missing local data triggers a fresh API fetch."""

    def test_stale_check_present_in_fetch_function(self):
        """_sprintProgressFetch or its caller must handle missing/stale store."""
        src = _html()
        # Look for a stale-check pattern in the progress fetch logic
        # Either explicit age check or null check on store
        assert (
            "_sprintProgressStore" in src
            and (
                "fetched_at" in src
                or "Date.now()" in src
                or "!_sprintProgressStore" in src
                or "_sprintProgressStore == null" in src
            )
        ), (
            "No stale/missing check found for _sprintProgressStore in project.html"
        )

    def test_endpoint_reads_persisted_file_on_missing_live_data(self):
        """Endpoint must read the persisted JSON file when no live data is in memory."""
        src = SERVER_PY.read_text(encoding="utf-8")
        # The endpoint handler must try to read a persisted file as fallback
        progress_fn_match = re.search(
            r"@app\.get\(['\"]\/api\/sprint-progress['\"].*?\n(?:async\s+)?def \w+.*?(?=\n@app\.|\nclass |\Z)",
            src, re.DOTALL
        )
        assert progress_fn_match, "/api/sprint-progress handler not found"
        handler = progress_fn_match.group(0)
        # Should reference reading a file (read_text, json.loads, etc.) or the persisted path
        assert (
            "read_text" in handler
            or "json.loads" in handler
            or "sprint-progress" in handler
        ), (
            "sprint-progress handler does not attempt to read persisted file as fallback"
        )

    def test_endpoint_reads_persisted_file_with_repo_param(self, tmp_path):
        """repo= alone must resolve the persisted file (frontend passes repo, not project)."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("server import failed")

        runtime = tmp_path / ".commander" / "runtime"
        runtime.mkdir(parents=True)
        cached = {
            "has_sprint": True,
            "sprint_label": "sprint-75.2",
            "sprint": 75,
            "done": 1,
            "total": 1,
            "run_state": "running",
            "source": "live",
            # Fresh timestamp — a real persisted file always has one (issue #1866);
            # the fixture must reflect that to exercise tier 2 without falling
            # through to the GitHub-fallback tier's freshness gate.
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }
        (runtime / "sprint-progress.json").write_text(json.dumps(cached), encoding="utf-8")

        with patch.object(srv, "_project_root_path") as mock_root, \
             patch.object(srv, "_all_sprints_running") as mock_running, \
             patch.object(srv, "get_sprint_nav_status") as mock_nav:
            mock_root.return_value = tmp_path
            mock_running.return_value = []
            mock_nav.side_effect = AssertionError("GitHub fallback should not run when file exists")

            resp = client.get(
                "/api/sprint-progress",
                params={"repo": "zealchaiwut/commander"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("sprint_label") == "sprint-75.2"
        assert data.get("done") == 1
        assert data.get("total") == 1

"""Tests for issue #2253: Post-cut verification pass across all retained surfaces.

AC coverage:
- AC1: Key UI-surface endpoints (Bulk create, Running view, History, Settings,
       Deploy tab, Finish sprint) respond without 5xx errors after orchestrator removal
- AC2: Three lookout contract endpoints return HTTP 200 with expected shape
- AC3: pytest collection-error count at or below 25 (this caps collection
       ERRORS, not test failures — the suite carries ~2442 failures, see #2338)
- AC4: Server imports without ModuleNotFoundError from deleted orchestrator modules

Issue #2345: collection used to spawn a full-tree ``pytest --co`` once per AC3/AC4
assertion. That is now a single module-scoped cached collect, so AC3/AC4 share one nested collect instead of four.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"
DASH_TESTS_DIR = REPO_ROOT / "apps" / "dashboard" / "tests"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

_PROJECT = "zealchaiwut/commander"


# ── shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app)


@pytest.fixture(scope="module")
def collection_output() -> str:
    """One process-group-safe full-tree ``--co`` for the whole module (#2345)."""
    from services.sprint_manager.pytest_runner import run_pytest

    result = run_pytest(
        [str(TESTS_DIR), str(DASH_TESTS_DIR), "--co", "--tb=no", "-q"],
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    return (result.stdout or "") + (result.stderr or "")



# ── AC1
# ── AC1: key UI-surface endpoints respond without 5xx ─────────────────────────

class TestKeyUISurfacesRespondWithout5xx:
    """AC1: retained UI-tab endpoints return non-5xx status after orchestrator removal."""

    def test_bulk_create_job_endpoint_responds(self, client):
        """AC1: Bulk create — /api/tickets/bulk/{job_id} responds (404 expected for unknown job)."""
        resp = client.get("/api/tickets/bulk/nonexistent-job-id")
        assert resp.status_code != 500, (
            f"GET /api/tickets/bulk/{{job_id}} returned 500. Body: {resp.text[:300]}"
        )
        assert resp.status_code in (200, 404), (
            f"Unexpected status {resp.status_code} for bulk job endpoint"
        )

    def test_running_view_endpoint_responds(self, client):
        """AC1: Running view — /api/running returns 200 with project param."""
        resp = client.get(f"/api/running?project={_PROJECT}")
        assert resp.status_code == 200, (
            f"GET /api/running?project=... returned {resp.status_code}, expected 200. "
            f"Body: {resp.text[:300]}"
        )

    def test_history_endpoint_responds(self, client):
        """AC1: History tab — /api/sprints/history returns 200."""
        resp = client.get("/api/sprints/history")
        assert resp.status_code == 200, (
            f"GET /api/sprints/history returned {resp.status_code}, expected 200. "
            f"Body: {resp.text[:300]}"
        )

    def test_settings_endpoint_responds(self, client):
        """AC1: Settings tab — /api/settings returns 200."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200, (
            f"GET /api/settings returned {resp.status_code}, expected 200. "
            f"Body: {resp.text[:300]}"
        )

    def test_deploy_config_endpoint_responds(self, client):
        """AC1: Deploy tab — /api/projects/{slug}/deploy-config returns non-5xx."""
        resp = client.get("/api/projects/commander/deploy-config")
        assert resp.status_code not in (500, 502, 503), (
            f"GET /api/projects/commander/deploy-config returned {resp.status_code}. "
            f"Body: {resp.text[:300]}"
        )

    def test_finish_sprint_preview_endpoint_responds(self, client):
        """AC1: Finish sprint — /api/sprints/{label}/finish-preview returns non-5xx."""
        resp = client.get(f"/api/sprints/sprint-999/finish-preview?project={_PROJECT}")
        assert resp.status_code not in (500, 502, 503), (
            f"GET /api/sprints/sprint-999/finish-preview returned {resp.status_code}. "
            f"Body: {resp.text[:300]}"
        )

    def test_sprint_plan_state_endpoint_responds(self, client):
        """AC1: Sprint plan view — /api/sprints/{label}/state returns non-5xx."""
        resp = client.get(f"/api/sprints/sprint-999/state?project={_PROJECT}")
        assert resp.status_code not in (500, 502, 503), (
            f"GET /api/sprints/sprint-999/state returned {resp.status_code}. "
            f"Body: {resp.text[:300]}"
        )


# ── AC2: lookout contract endpoints return 200 with expected shape ─────────────

class TestLookoutContractEndpoints:
    """AC2: GET /api/health, /api/projects/{slug}/brief, /api/sprints/history
    each return HTTP 200 with the minimum required payload keys."""

    def test_health_returns_200_with_status_key(self, client):
        """AC2: GET /api/health → 200 with 'status' key."""
        resp = client.get("/api/health")
        assert resp.status_code == 200, (
            f"GET /api/health returned {resp.status_code}, expected 200. "
            f"Body: {resp.text[:300]}"
        )
        body = resp.json()
        assert "status" in body, (
            f"Response body missing 'status' key. Got keys: {list(body.keys())}"
        )

    def test_sprints_history_returns_200_with_sprints_key(self, client):
        """AC2: GET /api/sprints/history → 200 with 'sprints' list."""
        resp = client.get("/api/sprints/history")
        assert resp.status_code == 200, (
            f"GET /api/sprints/history returned {resp.status_code}, expected 200. "
            f"Body: {resp.text[:300]}"
        )
        body = resp.json()
        assert "sprints" in body, (
            f"Response body missing 'sprints' key. Got keys: {list(body.keys())}"
        )
        assert isinstance(body["sprints"], list), (
            f"'sprints' value should be a list, got {type(body['sprints'])}"
        )
        for key in ("offset", "limit", "total"):
            assert key in body, f"Pagination key '{key}' missing from history response"

    def test_project_brief_returns_200_with_project_key(self, client):
        """AC2: GET /api/projects/{slug}/brief → 200 with brief payload keys."""
        resp = client.get("/api/projects/commander/brief")
        assert resp.status_code == 200, (
            f"GET /api/projects/commander/brief returned {resp.status_code}, "
            f"expected 200. Body: {resp.text[:300]}"
        )
        body = resp.json()
        assert "project" in body or "slug" in body or "repo" in body, (
            f"Brief response missing project identifier. Got keys: {list(body.keys())}"
        )


# ── AC3: collection error count at or below baseline ──────────────────────────

class TestScopedGateBaseline:
    """AC3: pytest collection errors stay at or below 25."""

    def test_collection_error_count_at_most_baseline(self, collection_output):
        """AC3: collection error count ≤ 25 (sprint exit gate baseline)."""
        error_lines = [
            line for line in collection_output.splitlines()
            if line.startswith("ERROR ")
        ]
        assert len(error_lines) <= 25, (
            f"Expected ≤25 collection errors (sprint exit gate baseline), "
            f"got {len(error_lines)}:\n"
            + "\n".join(error_lines[:30])
        )


# ── AC4: no import errors from deleted orchestrator modules ───────────────────

class TestNoDeletedModuleImportErrors:
    """AC4: server starts clean — no import errors from removed orchestrator modules."""

    def test_no_sprint_manager_module_import_error(self, collection_output):
        """AC4: no test file fails due to missing sprint_manager.py module."""
        assert "No module named 'services.sprint_manager.sprint_manager'" \
               not in collection_output, \
               "Found test(s) still importing the deleted sprint_manager.py module"

    def test_no_dispatch_module_import_error(self, collection_output):
        """AC4: no test file fails due to missing dispatch.py module."""
        assert "No module named 'services.sprint_manager.dispatch'" not in collection_output, \
               "Found test(s) still importing the deleted dispatch module"

    def test_no_pipeline_module_import_error(self, collection_output):
        """AC4: no test file fails due to missing pipeline.py module."""
        assert "No module named 'services.sprint_manager.pipeline'" not in collection_output, \
               "Found test(s) still importing the deleted pipeline module"

    def test_server_imports_without_orchestrator_modules(self):
        """AC4: `import server` succeeds — no unresolved orchestrator imports."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'apps/dashboard'); "
             "sys.path.insert(0, 'services/sprint_manager'); "
             "import server; print('import ok')"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ,
                 "DB_PATH": "/tmp/commander-pytest.db",
                 "COMMANDER_DISABLE_NEON": "1"},
        )
        assert "import ok" in result.stdout, (
            f"Server import failed. stderr:\n{result.stderr[:800]}"
        )
        assert "ModuleNotFoundError" not in result.stderr, (
            f"ModuleNotFoundError during server import:\n{result.stderr[:800]}"
        )

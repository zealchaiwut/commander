"""Tests for issue #2257 — Delete brief caching, LLM summary, and daily-report job.

AC1  The six listed files are deleted:
     - apps/dashboard/routers/brief_summary.py
     - apps/dashboard/routers/brief_artifact.py
     - apps/dashboard/routers/brief_invalidation.py
     - scripts/generate_daily_report.py
     - scripts/com.commander.daily-report.plist
     - scripts/install_daily_report_launchd.sh

AC2  brief_service.py and GET /api/projects/{slug}/brief are retained and
     return their current shape (project, date, shipped, in_progress, up_next,
     blocked, kpis, recent_activity keys present).

AC3  The S4-7 lookout read contract is not broken: /api/projects/{slug}/brief
     responds HTTP 200 with a JSON object (see test_2254 for the full contract).

AC4  home.html no longer polls a dead brief generator — no _devReportTick or
     equivalent interval polling the artifact/summary endpoints.

AC5  brief_invalidation import in finish_progress_service.py is removed —
     dead import of a deleted module cleaned up.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
SCRIPTS_DIR = REPO_ROOT / "scripts"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2257.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")


# ── AC1: Six files are deleted ────────────────────────────────────────────────

class TestDeletedFiles:
    """AC1: Each of the six listed files must not exist."""

    def test_brief_summary_py_deleted(self):
        assert not (ROUTERS_DIR / "brief_summary.py").exists(), (
            "brief_summary.py still exists — must be deleted per AC1"
        )

    def test_brief_artifact_py_deleted(self):
        assert not (ROUTERS_DIR / "brief_artifact.py").exists(), (
            "brief_artifact.py still exists — must be deleted per AC1"
        )

    def test_brief_invalidation_py_deleted(self):
        assert not (ROUTERS_DIR / "brief_invalidation.py").exists(), (
            "brief_invalidation.py still exists — must be deleted per AC1"
        )

    def test_generate_daily_report_py_deleted(self):
        assert not (SCRIPTS_DIR / "generate_daily_report.py").exists(), (
            "scripts/generate_daily_report.py still exists — must be deleted per AC1"
        )

    def test_daily_report_plist_deleted(self):
        assert not (SCRIPTS_DIR / "com.commander.daily-report.plist").exists(), (
            "scripts/com.commander.daily-report.plist still exists — must be deleted per AC1"
        )

    def test_install_daily_report_launchd_sh_deleted(self):
        assert not (SCRIPTS_DIR / "install_daily_report_launchd.sh").exists(), (
            "scripts/install_daily_report_launchd.sh still exists — must be deleted per AC1"
        )


# ── AC2: brief_service.py retained, /brief endpoint works ────────────────────

class TestBriefServiceRetained:
    """AC2: brief_service.py exists and the /brief endpoint returns current shape."""

    def test_brief_service_py_exists(self):
        assert (ROUTERS_DIR / "brief_service.py").exists(), (
            "brief_service.py was deleted — must be retained per AC2"
        )

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        import server  # noqa: PLC0415
        return TestClient(server.app)

    def test_brief_endpoint_returns_200(self, client):
        """AC2 + AC3: GET /api/projects/{slug}/brief returns HTTP 200."""
        resp = client.get("/api/projects/commander/brief")
        assert resp.status_code == 200, (
            f"GET /api/projects/commander/brief returned {resp.status_code}; "
            f"body: {resp.text[:300]}"
        )

    def test_brief_response_is_json_object(self, client):
        """AC2: response body is a JSON object."""
        resp = client.get("/api/projects/commander/brief")
        body = resp.json()
        assert isinstance(body, dict), f"Expected dict, got {type(body).__name__}"

    def test_brief_has_required_keys(self, client):
        """AC2: all ProjectBrief section keys are present."""
        body = client.get("/api/projects/commander/brief").json()
        for key in ("project", "date", "shipped", "in_progress", "up_next",
                    "blocked", "kpis", "recent_activity"):
            assert key in body, (
                f"'{key}' missing from GET /api/projects/commander/brief response. "
                f"Keys: {list(body)}"
            )


# ── AC4: home.html does not poll brief artifact/summary endpoints ─────────────

class TestHomeHtmlNoBriefPolling:
    """AC4: home.html must not contain polling code for the deleted endpoints."""

    @pytest.fixture(scope="class")
    def home_html(self):
        return (DASHBOARD_DIR / "static" / "home.html").read_text(encoding="utf-8")

    def test_no_devReportTick(self, home_html):
        """AC4: _devReportTick (the stale generator poller) must not be present."""
        assert "_devReportTick" not in home_html, (
            "home.html still contains _devReportTick — remove the stale poller"
        )

    def test_no_brief_artifact_polling(self, home_html):
        """AC4: no calls to /api/brief/daily or /api/projects/.*/brief/daily."""
        assert "/brief/daily" not in home_html, (
            "home.html still calls the deleted /brief/daily endpoint"
        )

    def test_no_brief_summary_polling(self, home_html):
        """AC4: no calls to /api/brief/summary or /api/projects/.*/brief/summary."""
        assert "/brief/summary" not in home_html, (
            "home.html still calls the deleted /brief/summary endpoint"
        )


# ── AC5: finish_progress_service no longer imports brief_invalidation ─────────

class TestDeadImportRemoved:
    """AC5: The try/except import of brief_invalidation in finish_progress_service.py
    must be removed — importing a deleted module is dead code."""

    def test_no_brief_invalidation_import(self):
        src = (ROUTERS_DIR / "finish_progress_service.py").read_text(encoding="utf-8")
        assert "brief_invalidation" not in src, (
            "finish_progress_service.py still imports brief_invalidation "
            "(deleted module) — remove the dead import"
        )

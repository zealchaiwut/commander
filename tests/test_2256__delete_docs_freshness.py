"""Tests for issue #2256 — Delete the docs-freshness subsystem.

AC coverage:
  AC1 — scripts/check_docs_freshness.py deleted; removed from scripts/AGENTS.md
  AC2 — docs_freshness_warnings table creation removed from db.py
  AC3 — /api/docs-freshness/* endpoints removed (return 404)
  AC4 — polling panel removed from home-preview.html
  AC5 — no remaining frontend reference to the docs-freshness endpoints
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPTS_DIR = REPO_ROOT / "scripts"
STATIC_DIR = DASHBOARD_DIR / "static"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: script deleted and removed from AGENTS.md ──────────────────────────

class TestScriptDeleted:
    """AC1 — check_docs_freshness.py must not exist and must not be in AGENTS.md."""

    def test_script_file_deleted(self):
        script = SCRIPTS_DIR / "check_docs_freshness.py"
        assert not script.exists(), (
            "scripts/check_docs_freshness.py still exists — delete it"
        )

    def test_script_absent_from_agents_md(self):
        agents_md = (SCRIPTS_DIR / "AGENTS.md").read_text(encoding="utf-8")
        assert "check_docs_freshness" not in agents_md, (
            "check_docs_freshness.py still referenced in scripts/AGENTS.md — remove the entry"
        )

    def test_scripts_agents_md_coverage_still_passes(self):
        """AC1 gate — the coverage test must pass with the script gone."""
        from pathlib import Path as _Path
        agents_md_text = (_Path(__file__).parent.parent / "scripts" / "AGENTS.md").read_text()
        scripts_dir = _Path(__file__).parent.parent / "scripts"
        _non_script = {"AGENTS.md", "com.commander.daily-report.plist"}
        _excluded = {"archive", "test_fixtures"}
        scripts_on_disk = set()
        for p in scripts_dir.iterdir():
            if p.is_dir() and p.name in _excluded:
                continue
            if p.is_file() and p.suffix in {".py", ".sh"} and p.name not in _non_script:
                scripts_on_disk.add(p.name)
        missing = [s for s in sorted(scripts_on_disk) if s not in agents_md_text]
        assert not missing, (
            f"These scripts missing from AGENTS.md: {missing}"
        )


# ── AC2: table creation removed from db.py ──────────────────────────────────

class TestTableCreationRemoved:
    """AC2 — db.py must not create the docs_freshness_warnings table."""

    def test_create_table_not_in_db_py(self):
        db_py = (DASHBOARD_DIR / "db.py").read_text(encoding="utf-8")
        assert "docs_freshness_warnings" not in db_py, (
            "docs_freshness_warnings table creation still present in db.py — remove it"
        )

    def test_db_helper_functions_removed(self):
        db_py = (DASHBOARD_DIR / "db.py").read_text(encoding="utf-8")
        for fn in ("upsert_docs_warning", "clear_docs_warning", "get_active_docs_warnings"):
            assert fn not in db_py, (
                f"db.py still contains {fn}() — remove the docs-freshness helper functions"
            )


# ── AC3: endpoints removed ───────────────────────────────────────────────────

class TestEndpointsRemoved:
    """AC3 — All /api/docs-freshness/* endpoints must return 404."""

    @pytest.fixture(scope="class")
    def client(self):
        import fastapi
        app = fastapi.FastAPI()
        import routers as _routers
        for name in _routers.__all__:
            router = getattr(_routers, name)
            app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_check_endpoint_gone(self, client):
        r = client.post("/api/docs-freshness/check", json={
            "repo": "test/repo",
            "trigger_ref": "abc123",
            "stale_docs": [],
            "cleared_docs": [],
        })
        assert r.status_code == 404, (
            f"POST /api/docs-freshness/check should be 404, got {r.status_code}"
        )

    def test_warnings_get_endpoint_gone(self, client):
        r = client.get("/api/docs-freshness/warnings")
        assert r.status_code == 404, (
            f"GET /api/docs-freshness/warnings should be 404, got {r.status_code}"
        )

    def test_warnings_delete_endpoint_gone(self, client):
        r = client.delete("/api/docs-freshness/warnings/1")
        assert r.status_code == 404, (
            f"DELETE /api/docs-freshness/warnings/1 should be 404, got {r.status_code}"
        )


# ── AC4 + AC5: no frontend references ────────────────────────────────────────

class TestFrontendReferencesRemoved:
    """AC4/AC5 — home-preview.html must not reference docs-freshness endpoints."""

    def test_no_fetch_to_docs_freshness(self):
        html = (STATIC_DIR / "home-preview.html").read_text(encoding="utf-8")
        assert "docs-freshness" not in html, (
            "home-preview.html still references docs-freshness — remove the polling panel"
        )

    def test_no_docs_freshness_in_any_static_file(self):
        hits = []
        for f in STATIC_DIR.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".js", ".mjs", ".ts"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if "docs-freshness" in text:
                    hits.append(str(f.relative_to(REPO_ROOT)))
        assert not hits, (
            f"Static files still referencing docs-freshness endpoints: {hits}"
        )

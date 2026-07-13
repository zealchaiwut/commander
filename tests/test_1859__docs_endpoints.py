"""Tests for issue #1859 — read-only docs API endpoints.

AC coverage:
  AC1 — GET /api/projects/{slug}/docs returns JSON list of all .md files under
         docs/ plus README.md and CHANGELOG.md, each with path, size, mtime
  AC2 — GET /api/projects/{slug}/docs/{path} returns {path, content} for an
         allowed file; 404 for missing files
  AC3 — Path traversal rejected: ../../etc/passwd and docs/../apps/dashboard/.env
         return 400/404 — behavioral tests via TestClient
  AC4 — Unknown project slug returns 404
  AC5 — Zero GitHub API calls — pure local filesystem reads
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.docs_service as docs_service  # noqa: E402
from routers.docs import router as docs_router  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_app():
    app = FastAPI()
    app.include_router(docs_router)
    return TestClient(app)


def _build_project_tree(base: Path) -> Path:
    """Return a fake clone root with docs/, README.md, CHANGELOG.md."""
    clone = base / "myproject"
    clone.mkdir(parents=True)
    (clone / ".git").mkdir()

    docs = clone / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# Architecture\n\nAuto region here.", encoding="utf-8")
    (docs / "quickstart.md").write_text("# Quickstart\n\nSteps.", encoding="utf-8")

    features = docs / "features"
    features.mkdir()
    (features / "sprint-board.md").write_text("# Sprint Board\n\nDetails.", encoding="utf-8")

    changelog_dir = docs / "changelog"
    changelog_dir.mkdir()
    (changelog_dir / "sprint-10.md").write_text("# Sprint 10\n\nShipped.", encoding="utf-8")

    (clone / "README.md").write_text("# My Project\n\nRoot readme.", encoding="utf-8")
    (clone / "CHANGELOG.md").write_text("# Changelog\n\n## v1.0", encoding="utf-8")

    # Non-.md files that must not appear in listing or be readable
    (clone / "apps").mkdir()
    (clone / "apps" / "server.py").write_text("# python")
    (clone / ".env").write_text("SECRET=abc123")

    return clone


# ── AC1: listing ──────────────────────────────────────────────────────────────

class TestListDocs:
    """AC1 — /api/projects/{slug}/docs listing."""

    def test_returns_200_and_list(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_includes_docs_md_files(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        paths = {item["path"] for item in client.get("/api/projects/myproject/docs").json()}
        assert "docs/architecture.md" in paths
        assert "docs/quickstart.md" in paths
        assert "docs/features/sprint-board.md" in paths
        assert "docs/changelog/sprint-10.md" in paths

    def test_includes_readme_and_changelog_at_root(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        paths = {item["path"] for item in client.get("/api/projects/myproject/docs").json()}
        assert "README.md" in paths
        assert "CHANGELOG.md" in paths

    def test_each_entry_has_required_fields(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        items = client.get("/api/projects/myproject/docs").json()
        for item in items:
            assert "path" in item, f"Missing 'path' in {item}"
            assert "size" in item, f"Missing 'size' in {item}"
            assert "mtime" in item, f"Missing 'mtime' in {item}"
            assert isinstance(item["size"], (int, float))
            assert isinstance(item["mtime"], (int, float))

    def test_excludes_non_md_files(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        paths = {item["path"] for item in client.get("/api/projects/myproject/docs").json()}
        assert ".env" not in paths
        assert "apps/server.py" not in paths

    def test_empty_docs_dir_returns_only_root_files(self, tmp_path, monkeypatch):
        clone = tmp_path / "empty"
        clone.mkdir()
        (clone / ".git").mkdir()
        (clone / "README.md").write_text("readme")
        (clone / "CHANGELOG.md").write_text("changelog")
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        paths = {item["path"] for item in client.get("/api/projects/empty/docs").json()}
        assert paths == {"README.md", "CHANGELOG.md"}

    def test_missing_readme_not_listed(self, tmp_path, monkeypatch):
        clone = tmp_path / "noreadme"
        clone.mkdir()
        (clone / "docs").mkdir()
        (clone / "docs" / "arch.md").write_text("arch")
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        paths = {item["path"] for item in client.get("/api/projects/noreadme/docs").json()}
        assert "README.md" not in paths
        assert "docs/arch.md" in paths


# ── AC2: fetch ────────────────────────────────────────────────────────────────

class TestGetDoc:
    """AC2 — /api/projects/{slug}/docs/{path} returns {path, content}; 404 for missing."""

    def test_returns_200_with_path_and_content(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/docs/architecture.md")
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == "docs/architecture.md"
        assert "Architecture" in data["content"]

    def test_returns_readme_from_root(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/README.md")
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == "README.md"
        assert "Root readme" in data["content"]

    def test_returns_changelog_from_root(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/CHANGELOG.md")
        assert r.status_code == 200
        data = r.json()
        assert "Changelog" in data["content"]

    def test_nested_docs_path(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/docs/features/sprint-board.md")
        assert r.status_code == 200
        assert r.json()["path"] == "docs/features/sprint-board.md"

    def test_missing_file_returns_404(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/docs/nonexistent.md")
        assert r.status_code == 404

    def test_content_matches_actual_file(self, tmp_path, monkeypatch):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        expected = (clone / "docs" / "architecture.md").read_text(encoding="utf-8")
        r = client.get("/api/projects/myproject/docs/docs/architecture.md")
        assert r.json()["content"] == expected


# ── AC3: path traversal ───────────────────────────────────────────────────────

class TestPathTraversal:
    """AC3 — path traversal rejected; behavioral tests via TestClient."""

    def test_dotdot_traversal_rejected(self, tmp_path, monkeypatch):
        """../../etc/passwd must be refused (400 or 404, never file content)."""
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/../../etc/passwd")
        assert r.status_code in (400, 404)
        body = r.text
        assert "passwd" not in body or r.status_code in (400, 404)

    def test_inline_traversal_rejected(self, tmp_path, monkeypatch):
        """docs/../apps/dashboard/.env must be refused (non-.md extension → 400)."""
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/docs/../apps/dashboard/.env")
        assert r.status_code in (400, 404)

    def test_non_md_extension_rejected(self, tmp_path, monkeypatch):
        """Files without .md extension are always rejected with 400."""
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/apps/server.py")
        assert r.status_code == 400

    def test_env_file_rejected(self, tmp_path, monkeypatch):
        """.env at repo root is rejected (non-.md extension)."""
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/.env")
        assert r.status_code in (400, 404)
        if r.status_code == 200:
            # Must never return file content for a non-allowed file
            pytest.fail("/.env returned 200 — path traversal guard failed")

    def test_path_outside_allowed_set_rejected(self, tmp_path, monkeypatch):
        """A .md file outside docs/ and not README/CHANGELOG is rejected."""
        clone = _build_project_tree(tmp_path)
        secret_dir = clone / "apps"
        secret_dir.mkdir(exist_ok=True)
        (secret_dir / "secrets.md").write_text("secret stuff")
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        r = client.get("/api/projects/myproject/docs/apps/secrets.md")
        assert r.status_code in (400, 404)


# ── AC4: unknown slug ─────────────────────────────────────────────────────────

class TestUnknownSlug:
    """AC4 — unknown project slug returns 404."""

    def test_list_unknown_slug_returns_404(self, monkeypatch):
        def _raise_404(slug):
            raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
        monkeypatch.setattr(docs_service, "resolve_clone_root", _raise_404)
        client = _make_app()

        r = client.get("/api/projects/does-not-exist/docs")
        assert r.status_code == 404

    def test_get_unknown_slug_returns_404(self, monkeypatch):
        def _raise_404(slug):
            raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
        monkeypatch.setattr(docs_service, "resolve_clone_root", _raise_404)
        client = _make_app()

        r = client.get("/api/projects/does-not-exist/docs/README.md")
        assert r.status_code == 404


# ── AC5: zero GitHub API calls ────────────────────────────────────────────────

class TestZeroGitHubCalls:
    """AC5 — endpoints make zero GitHub API calls (pure local filesystem reads)."""

    def test_list_makes_zero_gh_calls(self, tmp_path, monkeypatch, call_budget_fixture):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        client.get("/api/projects/myproject/docs")
        call_budget_fixture.assert_zero_gh()

    def test_get_makes_zero_gh_calls(self, tmp_path, monkeypatch, call_budget_fixture):
        clone = _build_project_tree(tmp_path)
        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
        client = _make_app()

        client.get("/api/projects/myproject/docs/README.md")
        call_budget_fixture.assert_zero_gh()

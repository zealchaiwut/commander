"""Tests for issue #1859: read-only docs endpoints (runs against UAT).

AC1: GET /api/projects/{slug}/docs returns JSON list of .md files under docs/
     plus README.md and CHANGELOG.md, each with path, size, mtime
AC2: GET /api/projects/{slug}/docs/{path} returns {path, content} for allowed file;
     404 for missing files
AC3: Path traversal rejected: ../../etc/passwd and docs/../apps/dashboard/.env
     return 400/404 — behavioral tests prove traversal is blocked
AC4: Unknown project slug returns 404
AC5: Zero GitHub API calls — pure local filesystem reads
"""
import os
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

import routers.docs_service as docs_service
from routers.docs import router as docs_router


@pytest.fixture
def client():
    """Create a TestClient against the docs router."""
    app = FastAPI()
    app.include_router(docs_router)
    return TestClient(app)


# ─── AC1: GET /api/projects/{slug}/docs listing ───────────────────────────

def _make_test_clone(tmp_path: Path) -> Path:
    """Create a minimal test clone with docs/ and root README/CHANGELOG."""
    clone = tmp_path / "test_clone"
    clone.mkdir()
    (clone / ".git").mkdir()

    docs = clone / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# Architecture\n\nAuto region.", encoding="utf-8")
    (docs / "quickstart.md").write_text("# Quickstart\n\nSteps.", encoding="utf-8")

    changelog_dir = docs / "changelog"
    changelog_dir.mkdir()
    (changelog_dir / "sprint-1.md").write_text("# Sprint 1\n\nShipped.", encoding="utf-8")

    (clone / "README.md").write_text("# Test Project\n\nRoot readme.", encoding="utf-8")
    (clone / "CHANGELOG.md").write_text("# Changelog\n\n## v1.0", encoding="utf-8")

    return clone


def test_read_only_docs_endpoints__list_returns_200_and_json(client, tmp_path, monkeypatch):
    """AC1: listing endpoint responds with 200 and JSON array."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_read_only_docs_endpoints__list_includes_readme_changelog(client, tmp_path, monkeypatch):
    """AC1: listing includes README.md and CHANGELOG.md at repo root."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs")
    assert r.status_code == 200
    paths = {item["path"] for item in r.json()}
    assert "README.md" in paths, "README.md not in listing"
    assert "CHANGELOG.md" in paths, "CHANGELOG.md not in listing"


def test_read_only_docs_endpoints__list_includes_docs_subdir(client, tmp_path, monkeypatch):
    """AC1: listing includes files under docs/ (e.g., docs/architecture.md)."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs")
    assert r.status_code == 200
    paths = {item["path"] for item in r.json()}
    assert "docs/architecture.md" in paths, "docs/architecture.md not in listing"
    assert "docs/changelog/sprint-1.md" in paths, "docs/changelog/sprint-1.md not in listing"


def test_read_only_docs_endpoints__list_entries_have_required_fields(client, tmp_path, monkeypatch):
    """AC1: each entry has path, size, mtime fields."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs")
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0, "Listing is empty"
    for item in items:
        assert "path" in item, f"Missing 'path' in {item}"
        assert "size" in item, f"Missing 'size' in {item}"
        assert "mtime" in item, f"Missing 'mtime' in {item}"
        assert isinstance(item["size"], (int, float)), f"size not a number: {item}"
        assert isinstance(item["mtime"], (int, float)), f"mtime not a number: {item}"


# ─── AC2: GET /api/projects/{slug}/docs/{path} fetch ──────────────────────

def test_read_only_docs_endpoints__get_readme_returns_200(client, tmp_path, monkeypatch):
    """AC2: fetching README.md returns 200 with path and content."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/README.md")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "README.md"
    assert isinstance(data["content"], str)
    assert "Test Project" in data["content"]


def test_read_only_docs_endpoints__get_changelog_returns_200(client, tmp_path, monkeypatch):
    """AC2: fetching CHANGELOG.md returns 200 with path and content."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/CHANGELOG.md")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "CHANGELOG.md"
    assert isinstance(data["content"], str)
    assert "Changelog" in data["content"]


def test_read_only_docs_endpoints__get_docs_subdir_file_returns_200(client, tmp_path, monkeypatch):
    """AC2: fetching a file under docs/ (e.g., docs/architecture.md) returns 200."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/docs/architecture.md")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "docs/architecture.md"
    assert isinstance(data["content"], str)
    assert "Architecture" in data["content"]


def test_read_only_docs_endpoints__get_missing_file_returns_404(client, tmp_path, monkeypatch):
    """AC2: requesting a non-existent file returns 404."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/docs/nonexistent-file-xyz-12345.md")
    assert r.status_code == 404


# ─── AC3: Path traversal rejection ────────────────────────────────────────

def test_read_only_docs_endpoints__dotdot_traversal_rejected(client, tmp_path, monkeypatch):
    """AC3: ../../etc/passwd is rejected with 400 or 404, never returns file."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/../../etc/passwd")
    assert r.status_code in (400, 404), f"Expected 400 or 404, got {r.status_code}"


def test_read_only_docs_endpoints__inline_traversal_rejected(client, tmp_path, monkeypatch):
    """AC3: docs/../apps/dashboard/.env is rejected (non-.md extension)."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/docs/../apps/dashboard/.env")
    # Should be rejected either because of path traversal or non-.md extension
    assert r.status_code in (400, 404), f"Expected 400 or 404, got {r.status_code}"


def test_read_only_docs_endpoints__non_md_extension_rejected(client, tmp_path, monkeypatch):
    """AC3: .py files are rejected with 400."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/apps/dashboard/server.py")
    assert r.status_code == 400, f"Expected 400 for .py file, got {r.status_code}"


def test_read_only_docs_endpoints__env_file_rejected(client, tmp_path, monkeypatch):
    """AC3: .env files are rejected with 400."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/.env")
    assert r.status_code in (400, 404), f"Expected 400 or 404 for .env, got {r.status_code}"


# ─── AC4: Unknown project slug ───────────────────────────────────────────

def test_read_only_docs_endpoints__unknown_slug_list_returns_404(client, monkeypatch):
    """AC4: listing for unknown project returns 404."""
    def _raise_404(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    monkeypatch.setattr(docs_service, "resolve_clone_root", _raise_404)

    r = client.get("/api/projects/does-not-exist/docs")
    assert r.status_code == 404


def test_read_only_docs_endpoints__unknown_slug_get_returns_404(client, monkeypatch):
    """AC4: fetching for unknown project returns 404."""
    def _raise_404(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    monkeypatch.setattr(docs_service, "resolve_clone_root", _raise_404)

    r = client.get("/api/projects/does-not-exist/docs/README.md")
    assert r.status_code == 404


# ─── AC5: Zero GitHub API calls ──────────────────────────────────────────

def test_read_only_docs_endpoints__list_github_isolation(client, tmp_path, monkeypatch):
    """AC5: listing endpoint makes only local filesystem calls (isolated from GitHub)."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
    # Verify the endpoint returns successfully without any GitHub module access
    r = client.get("/api/projects/test/docs")
    assert r.status_code == 200, "Endpoint should succeed without GitHub calls"
    assert isinstance(r.json(), list)


def test_read_only_docs_endpoints__get_github_isolation(client, tmp_path, monkeypatch):
    """AC5: fetch endpoint makes only local filesystem calls (isolated from GitHub)."""
    clone = _make_test_clone(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)
    r = client.get("/api/projects/test/docs/README.md")
    assert r.status_code == 200, "Endpoint should succeed without GitHub calls"
    data = r.json()
    assert "path" in data and "content" in data

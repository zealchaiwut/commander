"""Tests for issue #1978: dev-report project entries must include project, repo, icon, color.

AC1: _build_project_entry returns an entry with keys "project" (slug), "repo", "icon", "color".
AC2: build_contract propagates icon/color from projects_list into the "projects" list entries.
AC3: /api/dev-report?force=1 response includes project/repo/icon/color in each project entry.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import export_hermes_report as _mod


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY,
            issue_number INTEGER,
            repo TEXT,
            state TEXT,
            title TEXT,
            label_names TEXT DEFAULT '',
            body TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            closed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sprints (
            id INTEGER PRIMARY KEY,
            project TEXT,
            label TEXT,
            state TEXT
        );
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY,
            amount INTEGER DEFAULT 0,
            recorded_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS brief_artifacts (
            scope TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL,
            payload TEXT,
            generated_at TEXT,
            PRIMARY KEY (scope, project, date)
        );
    """)
    return conn


_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


# ── AC1: _build_project_entry includes required metadata fields ───────────────

def test_build_project_entry_includes_project_slug():
    """AC1: entry["project"] is the basename of repo (slug)."""
    conn = _make_db()
    proj = {"repo": "owner/myproj", "name": "My Project", "icon": "ti-star", "color": "blue"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["project"] == "myproj"


def test_build_project_entry_includes_repo():
    """AC1: entry["repo"] is the full owner/repo string."""
    conn = _make_db()
    proj = {"repo": "owner/myproj", "name": "My Project", "icon": "ti-star", "color": "blue"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["repo"] == "owner/myproj"


def test_build_project_entry_includes_icon():
    """AC1: entry["icon"] reflects the projects_list icon."""
    conn = _make_db()
    proj = {"repo": "owner/myproj", "name": "My Project", "icon": "ti-star", "color": "blue"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["icon"] == "ti-star"


def test_build_project_entry_includes_color():
    """AC1: entry["color"] reflects the projects_list color."""
    conn = _make_db()
    proj = {"repo": "owner/myproj", "name": "My Project", "icon": "ti-star", "color": "purple"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["color"] == "purple"


def test_build_project_entry_icon_defaults_to_folder():
    """AC1: entry["icon"] falls back to 'ti-folder' when not specified."""
    conn = _make_db()
    proj = {"repo": "owner/proj", "name": "P"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["icon"] == "ti-folder"


def test_build_project_entry_color_defaults_to_gray():
    """AC1: entry["color"] falls back to 'gray' when not specified."""
    conn = _make_db()
    proj = {"repo": "owner/proj", "name": "P"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["color"] == "gray"


def test_build_project_entry_slug_from_bare_repo():
    """AC1: slug is the repo string itself when it has no slash."""
    conn = _make_db()
    proj = {"repo": "myproject", "name": "P"}
    entry, _ = _mod._build_project_entry(proj, conn, _NOW, {}, 14, 7, 30)
    assert entry["project"] == "myproject"
    assert entry["repo"] == "myproject"


# ── AC2: build_contract propagates these fields through the full pipeline ─────

def test_build_contract_project_entries_have_metadata_fields():
    """AC2: build_contract result projects list includes project/repo/icon/color per entry."""
    projects = [
        {"repo": "owner/alpha", "name": "Alpha", "icon": "ti-rocket", "color": "green"},
        {"repo": "owner/beta",  "name": "Beta",  "icon": "ti-folder", "color": "red"},
    ]
    result = _mod.build_contract(
        ":memory:",
        now=_NOW,
        prev_state=None,
        projects_list=projects,
        price_map=None,
    )
    assert "projects" in result
    entries = {e["project"]: e for e in result["projects"]}
    assert "alpha" in entries
    assert entries["alpha"]["repo"] == "owner/alpha"
    assert entries["alpha"]["icon"] == "ti-rocket"
    assert entries["alpha"]["color"] == "green"

    assert "beta" in entries
    assert entries["beta"]["repo"] == "owner/beta"
    assert entries["beta"]["color"] == "red"


# ── AC3: /api/dev-report endpoint returns entries with these fields ───────────

def test_dev_report_endpoint_includes_project_metadata(tmp_path):
    """AC3: GET /api/dev-report?force=1 returns project entries with project/repo/icon/color."""
    import importlib, os
    db_file = tmp_path / "test.db"
    os.environ["DB_PATH"] = str(db_file)

    from fastapi.testclient import TestClient
    import apps.dashboard.server as server_mod
    importlib.reload(server_mod)
    client = TestClient(server_mod.app, raise_server_exceptions=True)

    from unittest.mock import patch
    projects = [{"repo": "owner/cmd", "name": "Cmd", "icon": "ti-terminal", "color": "cyan"}]
    with patch("export_hermes_report._auto_load_projects", return_value=projects):
        r = client.get("/api/dev-report?force=1")

    assert r.status_code == 200
    data = r.json()
    assert "projects" in data
    entries = data["projects"]
    assert len(entries) >= 1
    e = next((p for p in entries if p.get("project") == "cmd"), None)
    assert e is not None, f"Expected 'cmd' in projects; got {[p.get('project') for p in entries]}"
    assert e["repo"] == "owner/cmd"
    assert e["icon"] == "ti-terminal"
    assert e["color"] == "cyan"

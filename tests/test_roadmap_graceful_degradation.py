"""Roadmap tab degrades gracefully when the gh CLI is unavailable.

`get_roadmap` reads milestones (gh-only) and issues (mirror-backed, gh fallback).
When the gh CLI fails (CalledProcessError) or is missing (FileNotFoundError) the
endpoint must NOT 500 — it serves whatever is available (cached/empty) and flags
the payload `stale=true` with `error="github_unavailable"` so the UI can show a
"couldn't refresh from GitHub" notice instead of crashing (phase-1 design).
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# db.py (imported transitively via github_client) hard-exits if DB_PATH is unset.
# Keep the test self-contained without clobbering a DB_PATH the suite already set.
os.environ.setdefault("DB_PATH", str(Path(tempfile.gettempdir()) / "roadmap_degrade_test.db"))

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(REPO_ROOT / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()


def _called_process_error():
    return subprocess.CalledProcessError(
        returncode=1, cmd=["gh", "api", "..."], stderr="HTTP 503: upstream unavailable\n"
    )


class _FakeSettings:
    def __init__(self):
        self.store = {}

    def _key(self, scope, key, project):
        return f"{scope}|{project or ''}|{key}"

    def get_setting_scoped(self, scope, key, project=None):
        return self.store.get(self._key(scope, key, project), {})

    def set_setting(self, scope, key, value, project=None):
        self.store[self._key(scope, key, project)] = value


class _GHMilestonesDown:
    """gh down for milestones; issues come back from the mirror (no raise)."""

    def list_milestones(self, repo_name=None, state="all"):
        raise _called_process_error()

    def list_issues_with_milestone(self, repo_name=None):
        return [{"number": 10, "state": "open", "labels": [], "milestone": {"number": 1}}]


class _GHFullyDown:
    """gh down for everything; mirror also empty (issue list raises too)."""

    def list_milestones(self, repo_name=None, state="all"):
        raise _called_process_error()

    def list_issues_with_milestone(self, repo_name=None):
        raise _called_process_error()


class _GHMissing:
    """gh binary not installed at all."""

    def list_milestones(self, repo_name=None, state="all"):
        raise FileNotFoundError("gh")

    def list_issues_with_milestone(self, repo_name=None):
        raise FileNotFoundError("gh")


class _GHHealthy:
    def classify_issue(self, issue):
        return "done" if issue.get("state") == "closed" else "backlog"

    def list_milestones(self, repo_name=None, state="all"):
        return [{"number": 1, "title": "Alpha", "description": "", "state": "open",
                 "due_on": None}]

    def list_issues_with_milestone(self, repo_name=None):
        return [{"number": 10, "state": "closed", "labels": [], "milestone": {"number": 1}}]


def _service_with(monkeypatch, fake_gh):
    import routers.roadmap_service as service
    monkeypatch.setattr(service, "gh", fake_gh)
    monkeypatch.setattr(service, "_settings", _FakeSettings())
    return service


# ── core: no exception, 200-shaped payload, stale flag ───────────────────────

def test_milestones_down_returns_payload_not_exception(monkeypatch):
    service = _service_with(monkeypatch, _GHMilestonesDown())
    data = service.get_roadmap("owner/repo")  # must NOT raise
    assert data["stale"] is True
    assert data["error"] == "github_unavailable"
    # milestones are gh-only → no cards can be built without milestone metadata
    assert data["milestones"] == []
    assert data["closed_milestones"] == []
    # payload still carries the persisted settings shape
    assert "active_milestone" in data and "order" in data


def test_fully_down_returns_empty_stale_payload(monkeypatch):
    service = _service_with(monkeypatch, _GHFullyDown())
    data = service.get_roadmap("owner/repo")
    assert data["stale"] is True
    assert data["error"] == "github_unavailable"
    assert data["milestones"] == []
    assert data["closed_milestones"] == []


def test_gh_missing_binary_degrades(monkeypatch):
    service = _service_with(monkeypatch, _GHMissing())
    data = service.get_roadmap("owner/repo")
    assert data["stale"] is True
    assert data["error"] == "github_unavailable"


def test_healthy_path_not_stale(monkeypatch):
    service = _service_with(monkeypatch, _GHHealthy())
    data = service.get_roadmap("owner/repo")
    assert data["stale"] is False
    assert "error" not in data
    assert len(data["milestones"]) == 1


# ── the HTTP layer must return 200, not 500 ──────────────────────────────────

def test_route_returns_200_when_github_unavailable(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    service = _service_with(monkeypatch, _GHFullyDown())
    import routers.roadmap as roadmap_mod
    importlib.reload(roadmap_mod)
    app = FastAPI()
    app.include_router(roadmap_mod.router)
    client = TestClient(app)
    resp = client.get("/api/roadmap?repo=owner/repo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stale"] is True
    assert body["error"] == "github_unavailable"


# ── frontend wiring guard: stale notice + retry exist ────────────────────────

def test_frontend_renders_stale_notice():
    assert "_rmData.stale" in PROJECT_HTML
    assert "rm-stale-notice" in PROJECT_HTML
    assert "GitHub unreachable" in PROJECT_HTML

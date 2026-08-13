"""Tests for issue #2259 — Delete dead milestone surfaces, keep the selector.

AC1: home_milestone.py and home_milestone_service.py deleted.
     The GET /api/home/milestone endpoint no longer exists (returns 404/405).
AC2: Milestone CRUD endpoints removed.
     POST /api/projects/{slug}/milestones, PATCH /api/projects/{slug}/milestones/{n},
     and DELETE /api/projects/{slug}/milestones/{n} return 404/405.
AC3: GET /api/milestones retained and the selector still populates.
AC4: docs/milestones/ files untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MGR_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SPRINT_MGR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def client():
    for mod in list(sys.modules):
        if "server" in mod or "routers" in mod:
            del sys.modules[mod]
    import server as srv
    from fastapi.testclient import TestClient
    return TestClient(srv.app, raise_server_exceptions=False)


# ── AC1: home_milestone.py and home_milestone_service.py deleted ─────────────

def test_ac1_home_milestone_endpoint_gone(client):
    """GET /api/home/milestone must not exist after deletion of home_milestone.py."""
    resp = client.get("/api/home/milestone?repo=zealchaiwut/commander")
    assert resp.status_code in (404, 405), (
        f"Expected 404/405 but got {resp.status_code} — "
        "home_milestone.py router is still mounted"
    )


def test_ac1_home_milestone_py_file_deleted():
    """home_milestone.py must not exist on disk."""
    assert not (DASHBOARD_DIR / "routers" / "home_milestone.py").exists(), (
        "home_milestone.py still exists — it should have been deleted"
    )


def test_ac1_home_milestone_service_py_file_deleted():
    """home_milestone_service.py must not exist on disk."""
    assert not (DASHBOARD_DIR / "routers" / "home_milestone_service.py").exists(), (
        "home_milestone_service.py still exists — it should have been deleted"
    )


# ── AC2: Milestone CRUD endpoints removed ────────────────────────────────────

def test_ac2_create_milestone_gone(client):
    """POST /api/projects/{slug}/milestones must return 404/405 after CRUD removal."""
    resp = client.post(
        "/api/projects/commander/milestones",
        json={"title": "v1.0"},
    )
    assert resp.status_code in (404, 405), (
        f"Expected 404/405 but got {resp.status_code} — "
        "POST /api/projects/.../milestones still exists"
    )


def test_ac2_update_milestone_gone(client):
    """PATCH /api/projects/{slug}/milestones/{n} must return 404/405 after CRUD removal."""
    resp = client.patch(
        "/api/projects/commander/milestones/1",
        json={"title": "v1.1"},
    )
    assert resp.status_code in (404, 405), (
        f"Expected 404/405 but got {resp.status_code} — "
        "PATCH /api/projects/.../milestones/{n} still exists"
    )


def test_ac2_delete_milestone_gone(client):
    """DELETE /api/projects/{slug}/milestones/{n} must return 404/405 after CRUD removal."""
    resp = client.delete("/api/projects/commander/milestones/1")
    assert resp.status_code in (404, 405), (
        f"Expected 404/405 but got {resp.status_code} — "
        "DELETE /api/projects/.../milestones/{n} still exists"
    )


# ── AC3: GET /api/milestones retained ────────────────────────────────────────

def test_ac3_selector_endpoint_still_responds(client):
    """GET /api/milestones must still return 200 with the milestone selector payload."""
    fake_payload = {
        "milestones": [{"number": 1, "title": "v1.0", "active": True}],
        "active": "v1.0",
    }
    with patch(
        "routers.milestones.milestones_service.list_milestones",
        return_value=fake_payload,
    ):
        resp = client.get("/api/milestones?repo=zealchaiwut/commander")
    assert resp.status_code == 200, (
        f"GET /api/milestones returned {resp.status_code} — selector endpoint was removed"
    )
    body = resp.json()
    assert "milestones" in body, "Response payload is missing 'milestones' key"


# ── AC4: docs/milestones/ files untouched ────────────────────────────────────

def test_ac4_docs_milestones_directory_intact():
    """docs/milestones/ must exist and contain at least one file."""
    milestones_dir = REPO_ROOT / "docs" / "milestones"
    assert milestones_dir.is_dir(), "docs/milestones/ directory is missing"
    files = list(milestones_dir.iterdir())
    assert len(files) > 0, "docs/milestones/ is empty — tracking files were deleted"

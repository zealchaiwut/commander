"""Tests for issue #702 — settings prefill, analytics redirect, bulk sprint number.

AC coverage:
  AC1 — GET /api/settings and GET /api/projects/{slug}/settings return 200 (and
         usable data) when Neon/DB is unavailable — local JSON fallback kicks in
         instead of 500ing.
  AC2 — GET /project/{slug}/analytics redirects 302 → /project/{slug}/metrics
         (standalone analytics page retired in favour of the in-chrome tab).
  AC3 — 'metrics' is a valid project tab — /project/{slug}/metrics is NOT
         redirected away to sprint-mgmt.
  AC4 — _next_new_sprint_number() maxes over BOTH live sprint labels AND
         finished-sprint summary numbers; when live labels are deleted (common
         after a sprint finishes) it does not reset to 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def settings_repo_no_neon(tmp_path, monkeypatch):
    """Return a settings_repo module whose Neon session always fails.

    _try_open_session() returns None — simulates DATABASE_URL unset /
    COMMANDER_DISABLE_NEON=1. The local JSON fallback path is redirected to
    tmp_path so each test gets an isolated store.
    """
    for mod in list(sys.modules):
        if "settings_repo" in mod or "neon_db" in mod:
            del sys.modules[mod]

    store_path = tmp_path / "settings_store.json"

    import settings_repo as repo  # noqa: PLC0415

    monkeypatch.setattr(repo, "_fallback_store_path", lambda: store_path)
    # Force Neon session to always fail so the fallback branch is exercised.
    monkeypatch.setattr(repo, "_try_open_session", lambda: None)
    # Disable the sqlite KV layer too (no DB_PATH set).
    monkeypatch.setattr(repo, "_kv_conn", lambda: None)

    return repo, store_path


@pytest.fixture()
def srv():
    """Server module (imports startup.py helpers into its namespace)."""
    if "server" in sys.modules:
        del sys.modules["server"]
    if "startup" in sys.modules:
        del sys.modules["startup"]
    import server  # noqa: PLC0415
    return server


# ── AC1 — settings fallback when Neon is unavailable ────────────────────────

def test_ac1_get_setting_scoped_returns_empty_dict_not_raises(settings_repo_no_neon):
    """get_setting_scoped must return {} (not raise) when Neon/DB is unavailable."""
    repo, _ = settings_repo_no_neon
    result = repo.get_setting_scoped("global", "app_config")
    assert result == {}, f"Expected {{}}, got {result!r}"


def test_ac1_get_setting_returns_empty_dict_not_raises(settings_repo_no_neon):
    """get_setting must return {} (not raise) when Neon/DB is unavailable."""
    repo, _ = settings_repo_no_neon
    result = repo.get_setting("app_config")
    assert result == {}, f"Expected {{}}, got {result!r}"


def test_ac1_reads_persisted_value_from_json_fallback(settings_repo_no_neon):
    """After a write, get_setting_scoped reads back the saved value from the JSON file."""
    repo, store_path = settings_repo_no_neon
    # Pre-seed the JSON store directly (simulates a previous session's write).
    seed = {"global||app_config": {"default_model": "claude-haiku-4-5"}}
    store_path.write_text(json.dumps(seed))

    result = repo.get_setting_scoped("global", "app_config")
    assert result.get("default_model") == "claude-haiku-4-5", (
        f"Expected default_model=claude-haiku-4-5 from JSON fallback, got {result!r}"
    )


def test_ac1_set_setting_writes_to_json_fallback(settings_repo_no_neon):
    """set_setting must persist to the JSON fallback when Neon/DB is unavailable."""
    repo, store_path = settings_repo_no_neon
    repo.set_setting("global", "app_config", {"default_model": "claude-sonnet-4-6"})

    assert store_path.exists(), "settings_store.json was not created"
    data = json.loads(store_path.read_text())
    fkey = "global||app_config"
    assert fkey in data, f"Key {fkey!r} not found in {list(data)}"
    assert data[fkey].get("default_model") == "claude-sonnet-4-6"


# ── AC2 — /project/{slug}/analytics → 302 → /project/{slug}/metrics ─────────

def test_ac2_analytics_route_exists_in_pages_router():
    """The pages router must have a GET /project/{slug}/analytics handler."""
    for mod in list(sys.modules):
        if "routers.pages" in mod or mod == "routers.pages":
            del sys.modules[mod]

    import routers.pages as pages  # noqa: PLC0415

    routes = [r.path for r in pages.router.routes]
    assert "/project/{slug}/analytics" in routes, (
        f"/project/{{slug}}/analytics not in pages router routes: {routes}"
    )


def test_ac2_analytics_redirects_to_metrics(tmp_path):
    """GET /project/{slug}/analytics must return 302 pointing to /project/{slug}/metrics."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import RedirectResponse
    from fastapi import APIRouter

    # Build a minimal app with just the analytics redirect route.
    app = FastAPI()
    mini = APIRouter()

    @mini.get("/project/{slug}/analytics")
    def redirect_analytics(slug: str):
        return RedirectResponse(url=f"/project/{slug}/metrics", status_code=302)

    app.include_router(mini)

    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/project/my-proj/analytics")

    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    assert resp.headers["location"] == "/project/my-proj/metrics", (
        f"Wrong redirect target: {resp.headers.get('location')}"
    )


def test_ac2_pages_router_analytics_redirects_to_metrics():
    """The actual pages.router redirects /project/{slug}/analytics → /project/{slug}/metrics."""
    for mod in list(sys.modules):
        if "routers.pages" in mod or mod == "routers.pages":
            del sys.modules[mod]

    import routers.pages as pages  # noqa: PLC0415
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(pages.router)

    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/project/commander/analytics")

    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
    location = resp.headers.get("location", "")
    assert location == "/project/commander/metrics", (
        f"Expected redirect to /project/commander/metrics, got {location!r}"
    )


# ── AC3 — 'metrics' is a valid project tab ───────────────────────────────────

def test_ac3_metrics_in_valid_project_tabs():
    """'metrics' must be present in _VALID_PROJECT_TABS so it is served, not redirected."""
    for mod in list(sys.modules):
        if "routers.pages" in mod or mod == "routers.pages":
            del sys.modules[mod]

    import routers.pages as pages  # noqa: PLC0415

    assert "metrics" in pages._VALID_PROJECT_TABS, (
        f"'metrics' missing from _VALID_PROJECT_TABS: {pages._VALID_PROJECT_TABS}"
    )


# ── AC4 — _next_new_sprint_number includes finished-sprint summaries ──────────

def test_ac4_next_sprint_number_uses_finished_summaries(srv):
    """When live labels are empty but finished summaries exist, the next number
    must be max(summary_numbers) + 1, not 1.

    Reproduces the bug: bulk 'New sprint' said Sprint 54 but created sprint-1
    because _resolve_bulk_sprint_label only counted live sprint-N labels, which
    get deleted when a sprint finishes.
    """
    finished = {
        "sprint-53": {"number": 1001, "url": "https://github.com/x", "title": "Sprint 53 Executive Summary"},
        "sprint-50": {"number": 900, "url": "https://github.com/x", "title": "Sprint 50 Executive Summary"},
    }

    with patch.object(srv.github_client, "list_sprints", return_value=[]), \
         patch("startup._finished_sprint_summaries", return_value=finished), \
         patch.object(srv.github_client, "get_repo_for_operation", return_value="owner/repo"):
        result = srv._next_new_sprint_number("owner/repo")

    assert result == 54, (
        f"Expected 54 (max finished summary sprint-53 + 1), got {result}. "
        "Bug: live labels empty → counted only live labels → returned 1."
    )


def test_ac4_next_sprint_number_takes_max_of_live_and_summaries(srv):
    """Live labels and finished summaries are combined — whichever is higher wins."""
    finished = {
        "sprint-40": {"number": 800, "url": "https://github.com/x", "title": "Sprint 40 Executive Summary"},
    }

    with patch.object(srv.github_client, "list_sprints", return_value=[45, 46]), \
         patch("startup._finished_sprint_summaries", return_value=finished), \
         patch.object(srv.github_client, "get_repo_for_operation", return_value="owner/repo"):
        result = srv._next_new_sprint_number("owner/repo")

    assert result == 47, (
        f"Expected 47 (max(live=[45,46], summary=[40]) = 46 + 1), got {result}"
    )


def test_ac4_resolve_bulk_sprint_label_new_uses_summaries(srv):
    """_resolve_bulk_sprint_label('NEW', ...) must create sprint-54 when finished
    summaries show sprint-53 as the high-water mark (live labels empty)."""
    finished = {
        "sprint-53": {"number": 1001, "url": "https://github.com/x", "title": "Sprint 53 Executive Summary"},
    }

    with patch.object(srv.github_client, "list_sprints", return_value=[]), \
         patch("startup._finished_sprint_summaries", return_value=finished), \
         patch.object(srv.github_client, "get_repo_for_operation", return_value="owner/repo"), \
         patch.object(srv.github_client, "ensure_sprint_label") as mock_ensure:
        result = srv._resolve_bulk_sprint_label("NEW", "owner/repo")

    assert result == "sprint-54", (
        f"Expected 'sprint-54', got {result!r}. "
        "Bug: without finished-summary check, this would be 'sprint-1'."
    )
    mock_ensure.assert_called_once_with(54, repo_name="owner/repo")

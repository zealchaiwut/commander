"""Tests for issue #880 — Show active milestone progress on home and sprint nav.

Each test is anchored to a specific acceptance criterion (AC) from the issue:

  AC1  Each home project card shows the project's active milestone name.
  AC2  The indicator shows a compact count: `X done + Y UAT / Z total`.
  AC3  No active milestone → no indicator (card renders normally).
  AC4  Clicking the home-card indicator navigates to that project's Roadmap tab.
  AC5  The project header (sprint nav) shows the milestone name + same count.
  AC6  Clicking the header indicator navigates to the Roadmap tab.
  AC7  Progress counts reflect real-time milestone issue states (done + UAT).
  AC8  Indicator is readable and visually distinct without dominating layout.

The backend route lives in ``apps/dashboard/routers/home_milestone.py`` and is
mounted via ``include_router`` so it never touches the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761). Tests run fully offline: GitHub access is
monkeypatched and the frontend is asserted statically.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(REPO_ROOT / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HOME_HTML = (DASHBOARD_DIR / "static" / "home.html").read_text()
PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()


# ── in-memory fakes ───────────────────────────────────────────────────────────

class _FakeSettings:
    """Project-scoped KV stand-in for settings_repo."""

    def __init__(self, store=None):
        self.store = store or {}

    def _key(self, scope, key, project):
        return f"{scope}|{project or ''}|{key}"

    def get_setting_scoped(self, scope, key, project=None):
        return self.store.get(self._key(scope, key, project))

    def set_setting(self, scope, key, value, project=None):
        self.store[self._key(scope, key, project)] = value


# milestone 7: 2 done (closed), 1 uat, 1 backlog, 1 sit  -> done=2, uat=1, total=5
_ISSUES = [
    {"number": 1, "state": "closed", "labels": []},
    {"number": 2, "state": "open", "labels": [{"name": "UAT-approved"}]},
    {"number": 3, "state": "open", "labels": [{"name": "UAT"}]},
    {"number": 4, "state": "open", "labels": [{"name": "in-progress"}]},
    {"number": 5, "state": "open", "labels": [{"name": "SIT"}]},
]


@pytest.fixture()
def svc(monkeypatch):
    """home_milestone_service with GitHub + settings faked; active milestone = 7."""
    import routers.home_milestone_service as service
    fake_settings = _FakeSettings()
    fake_settings.set_setting("project", service.ROADMAP_KEY,
                              {"active_milestone": 7}, project="zealchaiwut/commander")
    monkeypatch.setattr(service, "_settings", fake_settings)
    monkeypatch.setattr(service, "_fetch_milestone",
                        lambda repo, n: {"number": n, "title": "Roadmap v2"})
    monkeypatch.setattr(service, "_fetch_milestone_issues",
                        lambda repo, n: list(_ISSUES))
    return service, fake_settings


@pytest.fixture()
def client(svc):
    service, _ = svc
    from routers.home_milestone import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── AC2 / AC7: progress computation + compact format ──────────────────────────

def test_compute_progress_counts_done_and_uat(svc):
    # AC7: done + UAT are derived from real issue states via classify_issue.
    service, _ = svc
    progress = service.compute_progress(list(_ISSUES))
    assert progress["done"] == 2      # closed + UAT-approved
    assert progress["uat"] == 1       # UAT label
    assert progress["total"] == 5
    assert progress["completed"] == 3  # done + uat


def test_format_label_is_compact_x_done_y_uat_z_total(svc):
    # AC2: exact compact format `X done + Y UAT / Z total`.
    service, _ = svc
    label = service.format_label({"done": 2, "uat": 1, "total": 5, "completed": 3})
    assert label == "2 done + 1 UAT / 5 total"


# ── AC1 / AC5: active milestone name + progress surfaced ──────────────────────

def test_active_milestone_progress_returns_name_and_label(svc):
    # AC1/AC5: the active milestone's name and compact progress are returned.
    service, _ = svc
    result = service.active_milestone_progress("zealchaiwut/commander")
    assert result is not None
    assert result["title"] == "Roadmap v2"
    assert result["label"] == "2 done + 1 UAT / 5 total"
    assert result["progress"]["completed"] == 3


# ── AC3: no active milestone → no indicator ───────────────────────────────────

def test_no_active_milestone_returns_none(monkeypatch):
    # AC3: a project with no active milestone yields None (no indicator shown).
    import routers.home_milestone_service as service
    monkeypatch.setattr(service, "_settings", _FakeSettings())  # empty store
    assert service.active_milestone_progress("zealchaiwut/commander") is None


def test_endpoint_returns_empty_object_when_no_active_milestone(monkeypatch):
    # AC3: the endpoint returns {} so the frontend renders the card normally.
    import routers.home_milestone_service as service
    monkeypatch.setattr(service, "_settings", _FakeSettings())
    from routers.home_milestone import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/api/home/milestone", params={"repo": "zealchaiwut/commander"})
    assert r.status_code == 200
    assert r.json() == {}


# ── AC7: endpoint exposes live progress ───────────────────────────────────────

def test_endpoint_returns_live_progress_payload(client):
    # AC7: the endpoint surfaces real-time done/UAT/total counts.
    r = client.get("/api/home/milestone", params={"repo": "zealchaiwut/commander"})
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Roadmap v2"
    assert data["label"] == "2 done + 1 UAT / 5 total"
    assert data["progress"] == {"done": 2, "uat": 1, "total": 5, "completed": 3}


# ── AC1 / AC4: home card indicator + navigation ───────────────────────────────

def test_home_card_renders_milestone_indicator():
    # AC1: home card markup includes the milestone name + count elements,
    # hydrated from the /api/home/milestone endpoint.
    assert 'class="pb-milestone"' in HOME_HTML
    assert "pb-ms-name" in HOME_HTML
    assert "pb-ms-count" in HOME_HTML
    assert "/api/home/milestone" in HOME_HTML


def test_home_card_indicator_links_to_roadmap_tab():
    # AC4: the home-card indicator is an anchor to the project's Roadmap tab.
    assert "/project/${esc(slug)}/roadmap" in HOME_HTML


# ── AC5 / AC6: header indicator + navigation ──────────────────────────────────

def test_header_renders_milestone_indicator():
    # AC5: the project header carries the milestone name + count, hydrated from
    # the same endpoint.
    assert 'id="hnav-milestone"' in PROJECT_HTML
    assert "hnav-ms-name" in PROJECT_HTML
    assert "hnav-ms-count" in PROJECT_HTML
    assert "/api/home/milestone" in PROJECT_HTML


def test_header_indicator_links_to_roadmap_tab():
    # AC6: clicking the header indicator navigates to the Roadmap tab.
    assert "/project/${encodeURIComponent(_slug)}/roadmap" in PROJECT_HTML
    # roadmap must be a valid project tab so the link resolves, not redirect.
    server_py = (DASHBOARD_DIR / "server.py").read_text()
    assert '"roadmap"' in server_py


# ── AC8: readable + visually distinct without dominating ───────────────────────

def test_indicator_is_styled_and_constrained():
    # AC8: indicator has its own styled class (tokens, border, mono count) and a
    # max-width so it cannot dominate the card/header layout.
    assert ".pb-milestone{" in HOME_HTML
    assert "max-width:48%" in HOME_HTML
    assert ".hnav-milestone {" in PROJECT_HTML
    assert "max-width: 280px" in PROJECT_HTML

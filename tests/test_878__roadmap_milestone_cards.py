"""Tests for issue #878 — Roadmap tab with milestone cards.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

  AC1  A "Roadmap" tab appears at the top-level project navigation.
  AC2  Each open milestone renders as a card: title, description, ticket
       progress ((done + UAT) / total, from the mirror), due date.
  AC3  Exactly one milestone can be Active; active card is visually dominant.
  AC4  Active milestone designation is persisted in project settings.
  AC5  Card order is persisted in project settings (drag-and-drop reorder).
  AC6  Inline creation of a new milestone (no modal / page navigation).
  AC7  Inline editing of title, description, and due date.
  AC8  A milestone can be closed from the card (collapses to a history row).
  AC9  Closed milestones are history rows that can be expanded / re-opened.
  AC10 Ticket progress = (done + UAT) / total for that milestone, from mirror.
  AC11 Card UI follows the design system: tokens, 5–6px radii, no side stripes.
  AC12 Roadmap tab state (active milestone, card order) survives page reload
       (persisted server-side via the settings store).

The backend route lives in ``apps/dashboard/routers/roadmap.py`` and is mounted
via ``include_router`` so it never touches the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761 / #761).
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(REPO_ROOT / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()
SERVER_PY = (DASHBOARD_DIR / "server.py").read_text()


# ── Fixtures: in-memory fakes for github_client + settings_repo ──────────────

class _FakeGH:
    """Stand-in for github_client used by roadmap_service."""

    def __init__(self):
        self.created = []
        self.updated = []
        self._milestones = [
            {"number": 1, "title": "Alpha", "description": "first", "state": "open",
             "due_on": "2026-07-01T00:00:00Z"},
            {"number": 2, "title": "Beta", "description": "second", "state": "open",
             "due_on": None},
            {"number": 3, "title": "Gamma", "description": "shipped", "state": "closed",
             "due_on": None},
        ]
        # issue.column is derived by classify_issue; supply labels/state instead.
        self._issues = [
            # milestone 1: 2 done (closed), 1 uat, 1 backlog  -> 3/4
            {"number": 10, "state": "closed", "labels": [], "milestone": {"number": 1}},
            {"number": 11, "state": "closed", "labels": [], "milestone": {"number": 1}},
            {"number": 12, "state": "open", "labels": [{"name": "UAT"}], "milestone": {"number": 1}},
            {"number": 13, "state": "open", "labels": [{"name": "in-progress"}], "milestone": {"number": 1}},
            # milestone 2: 0 of 2
            {"number": 20, "state": "open", "labels": [{"name": "SIT"}], "milestone": {"number": 2}},
            {"number": 21, "state": "open", "labels": [], "milestone": {"number": 2}},
            # no milestone — must be ignored
            {"number": 30, "state": "open", "labels": [], "milestone": None},
        ]

    def classify_issue(self, issue):
        labels = {l["name"] for l in issue.get("labels", [])}
        if issue.get("state") == "closed" or "UAT-approved" in labels:
            return "done"
        if "UAT" in labels:
            return "uat"
        if "SIT" in labels:
            return "sit"
        if "in-progress" in labels:
            return "in-progress"
        return "backlog"

    def list_milestones(self, repo_name=None, state="all"):
        if state == "open":
            return [m for m in self._milestones if m["state"] == "open"]
        return list(self._milestones)

    def list_issues_with_milestone(self, repo_name=None):
        return list(self._issues)

    def create_milestone(self, title, description="", due_on=None, repo_name=None):
        m = {"number": 99, "title": title, "description": description,
             "state": "open", "due_on": due_on}
        self.created.append(m)
        self._milestones.append(m)
        return m

    def update_milestone(self, number, fields, repo_name=None):
        self.updated.append((number, fields))
        for m in self._milestones:
            if m["number"] == number:
                m.update(fields)
                return m
        return {}

    def set_milestone_state(self, number, state, repo_name=None):
        return self.update_milestone(number, {"state": state}, repo_name)


class _FakeSettings:
    """In-memory stand-in for settings_repo (project-scoped KV)."""

    def __init__(self):
        self.store = {}

    def _key(self, scope, key, project):
        return f"{scope}|{project or ''}|{key}"

    def get_setting_scoped(self, scope, key, project=None):
        return self.store.get(self._key(scope, key, project), {})

    def set_setting(self, scope, key, value, project=None):
        self.store[self._key(scope, key, project)] = value


@pytest.fixture()
def svc(monkeypatch):
    import routers.roadmap_service as service
    fake_gh = _FakeGH()
    fake_settings = _FakeSettings()
    monkeypatch.setattr(service, "gh", fake_gh)
    monkeypatch.setattr(service, "_settings", fake_settings)
    return service, fake_gh, fake_settings


@pytest.fixture()
def client(svc):
    """Minimal app mounting only the roadmap router, with fakes injected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.roadmap as roadmap_mod
    importlib.reload(roadmap_mod)
    # roadmap router calls into the (already monkeypatched) service module
    app = FastAPI()
    app.include_router(roadmap_mod.router)
    return TestClient(app)


# ── AC1: Roadmap tab present in the project navigation ───────────────────────

class TestRoadmapTabPresent:
    def test_stab_button_exists(self):
        assert 'id="stab-roadmap"' in PROJECT_HTML
        assert "switchTab('roadmap')" in PROJECT_HTML

    def test_pane_exists(self):
        assert 'id="pane-roadmap"' in PROJECT_HTML

    def test_roadmap_registered_in_switchtab(self):
        # 'roadmap' must be in BOTH the active-sync list and the pane show/hide
        # list inside switchTab, else the tab can't activate or show its pane.
        occurrences = len(re.findall(r"'roadmap'", PROJECT_HTML))
        assert occurrences >= 3  # stab onclick + 2 switchTab arrays (at least)

    def test_lazy_init_hook(self):
        assert "roadmapInit" in PROJECT_HTML


# ── AC2 / AC10: progress = (done + UAT) / total, from the mirror ─────────────

class TestProgressComputation:
    def test_done_plus_uat_over_total(self, svc):
        service, _gh, _ = svc
        issues = _gh.list_issues_with_milestone()
        ms1 = [i for i in issues if i.get("milestone") and i["milestone"]["number"] == 1]
        prog = service.compute_progress(ms1)
        assert prog["done"] == 2
        assert prog["uat"] == 1
        assert prog["total"] == 4
        assert prog["completed"] == 3  # done + uat

    def test_zero_progress(self, svc):
        service, _gh, _ = svc
        issues = _gh.list_issues_with_milestone()
        ms2 = [i for i in issues if i.get("milestone") and i["milestone"]["number"] == 2]
        prog = service.compute_progress(ms2)
        assert prog["completed"] == 0
        assert prog["total"] == 2

    def test_empty_milestone_no_zero_division(self, svc):
        service, _, _ = svc
        prog = service.compute_progress([])
        assert prog["total"] == 0
        assert prog["completed"] == 0


# ── AC2: open milestones render as cards with the required fields ────────────

class TestGetRoadmapCards:
    def test_open_milestones_returned_as_cards(self, svc):
        service, _, _ = svc
        data = service.get_roadmap("o/r")
        numbers = {c["number"] for c in data["milestones"]}
        assert numbers == {1, 2}  # only open ones are full cards

    def test_card_carries_required_fields(self, svc):
        service, _, _ = svc
        data = service.get_roadmap("o/r")
        card = next(c for c in data["milestones"] if c["number"] == 1)
        for field in ("title", "description", "due_on", "progress", "state"):
            assert field in card
        assert card["progress"]["completed"] == 3
        assert card["progress"]["total"] == 4

    def test_issues_without_milestone_ignored(self, svc):
        service, _, _ = svc
        data = service.get_roadmap("o/r")
        # milestone 1 total stays 4 (issue 30 has no milestone)
        card = next(c for c in data["milestones"] if c["number"] == 1)
        assert card["progress"]["total"] == 4


# ── AC9: closed milestones are history rows, not full cards ──────────────────

class TestClosedMilestonesHistory:
    def test_closed_milestone_separated(self, svc):
        service, _, _ = svc
        data = service.get_roadmap("o/r")
        open_numbers = {c["number"] for c in data["milestones"]}
        closed_numbers = {c["number"] for c in data["closed_milestones"]}
        assert 3 not in open_numbers
        assert 3 in closed_numbers


# ── AC3 / AC4 / AC12: active milestone persisted in project settings ─────────

class TestActiveMilestonePersistence:
    def test_set_active_persists(self, svc):
        service, _, _settings = svc
        service.set_active("o/r", 2)
        stored = _settings.get_setting_scoped("project", service.ROADMAP_KEY, project="o/r")
        assert stored["active_milestone"] == 2

    def test_active_reflected_in_cards(self, svc):
        service, _, _ = svc
        service.set_active("o/r", 2)
        data = service.get_roadmap("o/r")
        active_card = next(c for c in data["milestones"] if c["number"] == 2)
        assert active_card["active"] is True
        other = next(c for c in data["milestones"] if c["number"] == 1)
        assert other["active"] is False

    def test_only_one_active(self, svc):
        service, _, _ = svc
        service.set_active("o/r", 1)
        service.set_active("o/r", 2)  # switching active replaces, never adds
        data = service.get_roadmap("o/r")
        actives = [c for c in data["milestones"] if c["active"]]
        assert len(actives) == 1
        assert actives[0]["number"] == 2


# ── AC5 / AC12: card order persisted in project settings ─────────────────────

class TestOrderPersistence:
    def test_set_order_persists(self, svc):
        service, _, _settings = svc
        service.set_order("o/r", [2, 1])
        stored = _settings.get_setting_scoped("project", service.ROADMAP_KEY, project="o/r")
        assert stored["order"] == [2, 1]

    def test_order_applied_to_cards(self, svc):
        service, _, _ = svc
        service.set_order("o/r", [2, 1])
        data = service.get_roadmap("o/r")
        ordered = [c["number"] for c in data["milestones"]]
        assert ordered == [2, 1]


# ── AC6: inline creation of a milestone ──────────────────────────────────────

class TestCreateMilestone:
    def test_create_calls_github(self, svc):
        service, _gh, _ = svc
        service.create_milestone("o/r", "New", "desc", "2026-08-01T00:00:00Z")
        assert _gh.created and _gh.created[-1]["title"] == "New"

    def test_create_endpoint(self, client):
        resp = client.post("/api/roadmap/milestones?repo=o/r",
                           json={"title": "New", "description": "d", "due_on": None})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"


# ── AC7: inline editing of title, description, due date ──────────────────────

class TestEditMilestone:
    def test_update_calls_github(self, svc):
        service, _gh, _ = svc
        service.update_milestone("o/r", 1, {"title": "Alpha2", "description": "x"})
        assert (1, {"title": "Alpha2", "description": "x"}) in _gh.updated

    def test_edit_endpoint(self, client):
        resp = client.patch("/api/roadmap/milestones/1?repo=o/r",
                            json={"description": "updated"})
        assert resp.status_code == 200


# ── AC8 / AC9: close + reopen ────────────────────────────────────────────────

class TestCloseReopen:
    def test_close_sets_closed_state(self, svc):
        service, _gh, _ = svc
        service.close_milestone("o/r", 1)
        assert (1, {"state": "closed"}) in _gh.updated

    def test_reopen_sets_open_state(self, svc):
        service, _gh, _ = svc
        service.reopen_milestone("o/r", 3)
        assert (3, {"state": "open"}) in _gh.updated

    def test_close_endpoint(self, client):
        resp = client.post("/api/roadmap/milestones/1/close?repo=o/r")
        assert resp.status_code == 200

    def test_reopen_endpoint(self, client):
        resp = client.post("/api/roadmap/milestones/3/reopen?repo=o/r")
        assert resp.status_code == 200


# ── Endpoint surface: GET roadmap + PUT settings ─────────────────────────────

class TestEndpoints:
    def test_get_roadmap_endpoint(self, client):
        resp = client.get("/api/roadmap?repo=o/r")
        assert resp.status_code == 200
        body = resp.json()
        assert "milestones" in body and "closed_milestones" in body

    def test_put_settings_persists_active_and_order(self, client, svc):
        service, _, _settings = svc
        resp = client.put("/api/roadmap/settings?repo=o/r",
                          json={"active_milestone": 2, "order": [2, 1]})
        assert resp.status_code == 200
        stored = _settings.get_setting_scoped("project", service.ROADMAP_KEY, project="o/r")
        assert stored["active_milestone"] == 2
        assert stored["order"] == [2, 1]


# ── AC: router exported + mounted without growing the monolith ───────────────

class TestRouterWiring:
    def test_router_exported(self):
        routers = importlib.import_module("routers")
        assert "roadmap_router" in routers.__all__
        from fastapi import APIRouter
        assert isinstance(routers.roadmap_router, APIRouter)

    def test_router_mounted_in_server(self):
        assert "roadmap_router" in SERVER_PY
        assert "include_router(" in SERVER_PY

    def test_no_roadmap_route_defined_inline_in_server(self):
        # routes must live in the router module, never in the monolith
        assert '@app.get("/api/roadmap")' not in SERVER_PY
        assert '"/api/roadmap"' not in SERVER_PY


# ── AC11: design system — tokens, 5–6px radii, no side stripes ───────────────

class TestDesignSystem:
    def _roadmap_css(self):
        # Roadmap-specific CSS is fenced by the section comment marker.
        m = re.search(r"/\* ── Roadmap tab.*?\*/(.*?)(?:/\* ──|</style>)",
                      PROJECT_HTML, re.S)
        assert m, "Roadmap CSS section not found"
        return m.group(1)

    def test_card_radius_5_or_6_px(self):
        css = self._roadmap_css()
        radii = re.findall(r"border-radius:\s*([0-9]+)px", css)
        assert radii, "no border-radius declared in roadmap CSS"
        assert all(r in ("5", "6") for r in radii), f"off-spec radii: {radii}"

    def test_no_side_stripe_decoration(self):
        css = self._roadmap_css()
        # A side stripe is a thick colored left/right border on the card.
        assert not re.search(r"border-left:\s*[3-9]px", css)
        assert not re.search(r"border-right:\s*[3-9]px", css)

    def test_uses_design_tokens_not_hardcoded_hex(self):
        css = self._roadmap_css()
        # Components must reference CSS vars; no raw hex colors in the section.
        assert "var(--" in css
        assert not re.search(r":\s*#[0-9a-fA-F]{3,6}\b", css)

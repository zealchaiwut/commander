"""Tests for issue #879 — Add milestone selector and display across ticket flows.

Each test is anchored to a specific acceptance criterion (AC) from the issue:

  AC1  BA bulk-create dialog includes a milestone selector populated with the
       project's available GitHub milestones.
  AC2  The bulk-create milestone selector defaults to the Active milestone.
  AC3  The single new-ticket dialog includes the same selector + Active default.
  AC4  Submitting either dialog creates the GitHub issue with the selected
       milestone assigned.
  AC5  No milestone selected → the issue is created with no milestone (no error,
       no forced default).
  AC6  Sprint board ticket rows display the assigned milestone as a small chip.
  AC7  The ticket detail panel displays the assigned milestone as a small chip.
  AC8  The backlog panel exposes a milestone filter; selecting one shows only
       tickets assigned to it.
  AC9  Selecting "All" / clearing the backlog filter restores the full list.
  AC10 Milestone chips on the board and detail panel are read-only.

Backend additions live in github_client (milestone helpers), a new
``routers/milestones.py`` router (mounted via include_router so the server.py
monolith stays frozen — COMMANDER_GATE_MONOLITH / issue #761), and the existing
create / bulk-post / issue-serialization paths. Frontend additions live in
``static/project.html`` and the bundled ``static/src/sprint-board/board-render.js``.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(REPO_ROOT / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()
BOARD_RENDER_JS = (DASHBOARD_DIR / "static" / "src" / "sprint-board" / "board-render.js").read_text()
SERVER_PY = (DASHBOARD_DIR / "server.py").read_text()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def gc():
    import github_client
    importlib.reload(github_client)
    return github_client


@pytest.fixture()
def srv():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv  # noqa: WPS433
    return srv


# ── AC4 / AC5: github_client.create_issue threads the milestone to gh ─────────

class TestCreateIssueMilestoneArg:
    def test_milestone_passed_as_gh_flag(self, gc, monkeypatch):
        captured = {}

        def fake_run(*args):
            captured["args"] = args
            return "https://github.com/o/r/issues/123"

        monkeypatch.setattr(gc, "_run", fake_run)
        monkeypatch.setattr(gc, "invalidate", lambda *a, **k: None)
        num, _url = gc.create_issue("T", "B", ["backlog"], repo_name="o/r", milestone="Beta")
        assert num == 123
        args = captured["args"]
        assert "--milestone" in args
        # The milestone value follows the flag.
        assert args[args.index("--milestone") + 1] == "Beta"

    def test_no_milestone_omits_flag(self, gc, monkeypatch):
        captured = {}

        def fake_run(*args):
            captured["args"] = args
            return "https://github.com/o/r/issues/9"

        monkeypatch.setattr(gc, "_run", fake_run)
        monkeypatch.setattr(gc, "invalidate", lambda *a, **k: None)
        gc.create_issue("T", "B", ["backlog"], repo_name="o/r")
        assert "--milestone" not in captured["args"]

    def test_empty_milestone_omits_flag(self, gc, monkeypatch):
        captured = {}
        monkeypatch.setattr(gc, "_run", lambda *args: captured.update(args=args) or "https://x/y/issues/1")
        monkeypatch.setattr(gc, "invalidate", lambda *a, **k: None)
        gc.create_issue("T", "B", ["backlog"], repo_name="o/r", milestone="")
        assert "--milestone" not in captured["args"]


# ── AC1: github_client.list_milestones returns the repo's milestones ─────────

class TestListMilestones:
    def test_returns_number_and_title(self, gc, monkeypatch):
        fake = [
            {"number": 1, "title": "Alpha", "state": "open"},
            {"number": 2, "title": "Beta", "state": "open"},
        ]
        monkeypatch.setattr(gc, "_json", lambda *a, **k: fake)
        monkeypatch.setattr(gc, "_cached", lambda key, fn: fn())
        out = gc.list_milestones(repo_name="o/r")
        titles = {m["title"] for m in out}
        assert titles == {"Alpha", "Beta"}
        assert all("number" in m for m in out)


# ── milestone_view helper: shrink a gh milestone object to {number,title} ─────

class TestMilestoneView:
    def test_extracts_number_and_title(self, gc):
        v = gc.milestone_view({"milestone": {"number": 2, "title": "Beta", "state": "open"}})
        assert v == {"number": 2, "title": "Beta"}

    def test_none_when_unassigned(self, gc):
        assert gc.milestone_view({"milestone": None}) is None
        assert gc.milestone_view({}) is None


# ── AC1 / AC2: milestones endpoint populates options + marks the active one ──

@pytest.fixture()
def ms_client(monkeypatch):
    """Minimal app mounting only the milestones router, with fakes injected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.milestones_service as service
    importlib.reload(service)
    import routers.milestones as ms_mod
    importlib.reload(ms_mod)

    class _FakeGH:
        def list_milestones(self, repo_name=None, state="open"):
            return [
                {"number": 1, "title": "Alpha", "state": "open"},
                {"number": 2, "title": "Beta", "state": "open"},
            ]

    class _FakeSettings:
        def __init__(self, active):
            self._active = active

        def get_setting_scoped(self, scope, key, project=None):
            return {"active_milestone": self._active}

    monkeypatch.setattr(service, "gh", _FakeGH())

    def _make(active):
        monkeypatch.setattr(service, "_settings", _FakeSettings(active))
        app = FastAPI()
        app.include_router(ms_mod.router)
        return TestClient(app)

    return _make


class TestMilestonesEndpoint:
    def test_lists_all_milestones(self, ms_client):
        client = ms_client(active=2)
        resp = client.get("/api/milestones", params={"repo": "o/r"})
        assert resp.status_code == 200
        data = resp.json()
        titles = {m["title"] for m in data["milestones"]}
        assert titles == {"Alpha", "Beta"}

    def test_active_milestone_is_marked_and_defaulted(self, ms_client):
        client = ms_client(active=2)
        data = client.get("/api/milestones", params={"repo": "o/r"}).json()
        assert data["active"] == "Beta"
        active_flags = {m["title"]: m["active"] for m in data["milestones"]}
        assert active_flags["Beta"] is True
        assert active_flags["Alpha"] is False

    def test_no_active_milestone_returns_null(self, ms_client):
        client = ms_client(active=None)
        data = client.get("/api/milestones", params={"repo": "o/r"}).json()
        assert data["active"] is None
        assert all(m["active"] is False for m in data["milestones"])


# ── AC3 / AC4 / AC5: the single new-ticket endpoint assigns the milestone ────

@pytest.fixture()
def app_client(srv, monkeypatch):
    from fastapi.testclient import TestClient
    captured = {}

    def fake_create_issue(title, body, labels, repo_name=None, milestone=None):
        captured["milestone"] = milestone
        captured["labels"] = labels
        return 321, "https://github.com/o/r/issues/321"

    monkeypatch.setattr(srv.github_client, "create_issue", fake_create_issue)
    return TestClient(srv.app), captured


class TestCreateTicketEndpoint:
    def test_milestone_forwarded_to_create_issue(self, app_client):
        client, captured = app_client
        resp = client.post(
            "/api/tickets/create",
            data={"title": "A ticket", "body": "body", "project": "o/r", "milestone": "Beta"},
        )
        assert resp.status_code == 201, resp.text
        assert captured["milestone"] == "Beta"

    def test_no_milestone_creates_with_none(self, app_client):
        client, captured = app_client
        resp = client.post(
            "/api/tickets/create",
            data={"title": "A ticket", "body": "body", "project": "o/r"},
        )
        assert resp.status_code == 201, resp.text
        # AC5: absent / empty selection → no milestone (falsy, never a forced default).
        assert not captured["milestone"]


# ── AC4: the bulk-post flow resolves + threads the chosen milestone ──────────

class TestBulkMilestoneResolution:
    def test_body_model_accepts_milestone(self, srv):
        b = srv.BulkPostSelectedBody(tickets=[], milestone="Beta")
        assert b.milestone == "Beta"

    def test_body_milestone_defaults_none(self, srv):
        b = srv.BulkPostSelectedBody(tickets=[])
        assert b.milestone is None

    def test_resolve_prefers_request_over_job(self, srv):
        assert srv._resolve_bulk_milestone("Beta", {"milestone": "Alpha"}) == "Beta"

    def test_resolve_falls_back_to_job(self, srv):
        assert srv._resolve_bulk_milestone(None, {"milestone": "Alpha"}) == "Alpha"

    def test_resolve_none_when_neither(self, srv):
        assert srv._resolve_bulk_milestone(None, {}) is None

    def test_bulk_create_issue_calls_pass_milestone(self, srv):
        # Every create_issue call inside the bulk post path must forward a
        # milestone kwarg (AC4) — guards against a regression that drops it.
        import re
        body = SERVER_PY
        start = body.index("async def bulk_post_selected")
        end = body.index("class BulkRetryWithBodyBody")
        section = body[start:end]
        calls = re.findall(r"github_client\.create_issue\((.*?)\)", section, re.DOTALL)
        assert calls, "expected create_issue calls inside bulk_post_selected"
        assert all("milestone=" in c for c in calls)


# ── AC6 / AC7: issue payloads carry milestone for the chips ──────────────────

class TestIssueSerializationMilestone:
    def test_sprint_management_issues_include_milestone(self, srv, monkeypatch):
        from fastapi.testclient import TestClient

        issues = [
            {"number": 5, "title": "Has MS", "labels": [], "url": "u", "body": "",
             "createdAt": "2026-01-01T00:00:00Z",
             "milestone": {"number": 2, "title": "Beta", "state": "open"}},
            {"number": 6, "title": "No MS", "labels": [], "url": "u", "body": "",
             "createdAt": "2026-01-01T00:00:00Z", "milestone": None},
        ]
        monkeypatch.setattr(srv.github_client, "list_open_issues_with_body", lambda **k: issues)
        monkeypatch.setattr(srv.github_client, "list_sprints", lambda **k: [])
        monkeypatch.setattr(srv.github_client, "list_sprint_labels", lambda **k: [])
        client = TestClient(srv.app)
        data = client.get("/api/sprint-management/issues", params={"repo": "o/r"}).json()
        by_num = {i["number"]: i for i in data["issues"]}
        assert by_num[5]["milestone"] == {"number": 2, "title": "Beta"}
        assert by_num[6]["milestone"] is None

    def test_project_details_include_milestone(self, monkeypatch):
        import projects as projects_module
        importlib.reload(projects_module)

        issues = [
            {"number": 7, "title": "MS ticket", "url": "u", "assignees": [],
             "updatedAt": "2026-01-01T00:00:00Z", "labels": [], "body": "",
             "milestone": {"number": 1, "title": "Alpha", "state": "open"}},
        ]
        monkeypatch.setattr(projects_module, "load_projects",
                            lambda: [{"repo": "o/r", "active_sprints": {}}])
        monkeypatch.setattr(projects_module.github_client,
                            "list_open_issues_with_body", lambda **k: issues)
        monkeypatch.setattr(projects_module.github_client,
                            "list_feature_branches", lambda **k: {})
        monkeypatch.setattr(projects_module.db, "get_tokens_today",
                            lambda **k: {"input_tokens": 0, "output_tokens": 0})
        out = projects_module.get_project_details("o/r", [])
        t = out["tickets"][0]
        assert t["milestone"] == {"number": 1, "title": "Alpha"}


# ── AC1 / AC3: the selectors exist in both dialogs and submit the value ──────

class TestFrontendSelectors:
    def test_new_ticket_selector_present(self):
        assert 'id="nt-milestone"' in PROJECT_HTML

    def test_bulk_create_selector_present(self):
        assert 'id="bc-milestone"' in PROJECT_HTML

    def test_new_ticket_submits_milestone(self):
        assert "formData.append('milestone'" in PROJECT_HTML

    def test_bulk_post_includes_milestone(self):
        # bcPostSelected must put the chosen milestone in the request body.
        start = PROJECT_HTML.index("async function bcPostSelected")
        end = PROJECT_HTML.index("\n}", start)
        section = PROJECT_HTML[start:end]
        assert "milestone" in section and "bc-milestone" in section

    def test_selectors_fetch_milestones(self):
        assert "/api/milestones" in PROJECT_HTML


# ── AC6 / AC7 / AC10: read-only milestone chips on board, backlog, detail ────

class TestMilestoneChips:
    def test_board_row_renders_chip(self):
        assert "smgmt-ms-chip" in BOARD_RENDER_JS

    def test_chip_helper_used_in_board_and_backlog_rows(self):
        # The chip helper must be invoked from both the sprint-board row builder
        # and the backlog row builder (AC6 covers board; backlog rows carry it too).
        assert BOARD_RENDER_JS.count("_smgmtMilestoneChipHtml") >= 3  # def + 2 calls

    def test_detail_panel_renders_chip(self):
        start = PROJECT_HTML.index("function tdpOpen")
        end = PROJECT_HTML.index("function tdpClose")
        section = PROJECT_HTML[start:end]
        assert "ms-chip" in section

    def test_chip_css_defined_with_tokens(self):
        # Contrast-safe: chip styling references CSS variables, never hardcoded
        # hex (which would break dark mode and risk the low-contrast gate).
        assert ".smgmt-ms-chip" in PROJECT_HTML
        idx = PROJECT_HTML.index(".smgmt-ms-chip")
        block = PROJECT_HTML[idx:idx + 400]
        assert "var(--" in block

    def test_chip_is_read_only(self):
        # AC10: the chip is display-only — no onclick / edit affordance on it.
        start = BOARD_RENDER_JS.index("_smgmtMilestoneChipHtml")
        section = BOARD_RENDER_JS[start:start + 600]
        assert "onclick" not in section


# ── AC8 / AC9: backlog milestone filter ──────────────────────────────────────

class TestBacklogMilestoneFilter:
    def test_filter_control_present(self):
        assert 'id="bl-ms-filter"' in PROJECT_HTML

    def test_filter_state_in_apply(self):
        # _blApplyFilters must consider the milestone filter.
        start = PROJECT_HTML.index("function _blApplyFilters")
        end = PROJECT_HTML.index("\n}", start)
        section = PROJECT_HTML[start:end]
        assert "_blFilters.milestone" in section

    def test_clear_resets_milestone(self):
        # AC9: clearing restores the full list — _blClearFilters resets milestone.
        start = PROJECT_HTML.index("function _blClearFilters")
        end = PROJECT_HTML.index("\n}", start)
        section = PROJECT_HTML[start:end]
        assert "milestone" in section

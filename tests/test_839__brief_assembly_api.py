"""Tests for issue #839 — brief assembly API (project + home roll-up).

Each test is anchored to a specific acceptance criterion. No backend is mocked:
the DB is a real (temp) SQLite file seeded with real rows. The project list and
the on-disk sprint goal are the only seams, patched per-test. AC5 patches the
network/subprocess layer to PROVE neither endpoint makes an LLM/network call.

AC1  GET /api/projects/{slug}/brief returns shipped, in_progress, up_next,
     blocked, kpis, recent_activity sections.
AC2  GET /api/brief returns global_kpis, decisions, projects.
AC3  `date` windows all sprint/event queries; omitting it defaults to today.
AC4  A project with no activity in the window returns empty sections, HTTP 200.
AC5  Neither endpoint makes an LLM/network call.
AC6  shipped entries carry label, goal, features, done, skipped, duration,
     pr_number, summary_issue_number.
AC7  in_progress carries sprint_label, current_ticket, active_agent, progress
     (done/total/percent), elapsed.
AC8  up_next carries label, ticket_count, ready (False when no goal).
AC9  blocked lists rework/returned and failed tickets.
AC10 kpis carry sprints_shipped, tickets_done, in_progress (+percent), needs_you.
AC11 recent_activity carries the last N events, each with time, source, message.
AC12 home decisions aggregates run_ready/rework/branch_cleanup/empty_sprint,
     each with project, type, label, suggested_action.
AC13 home global_kpis sums across projects.
AC14 endpoint is gated on the project_events table; clear error if missing.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
SERVER_PY_SRC = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
SPRINTS_PY_SRC = (ROUTERS_DIR / "sprints.py").read_text(encoding="utf-8")

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

REPO = "owner/widget"
SLUG = "widget"
TODAY = datetime.now(timezone.utc).date().isoformat()
DATE = "2026-06-10"
START = f"{DATE}T08:00:00"
END = f"{DATE}T09:00:00"


def _load_module(name: str, path: Path):
    """Load a routers/*.py file as a submodule of a stub `routers` package."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


import db as db_module  # noqa: E402

svc = _load_module("routers.brief_service", ROUTERS_DIR / "brief_service.py")
router_mod = _load_module("routers.brief", ROUTERS_DIR / "brief.py")


@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_839.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture(autouse=True)
def one_project(monkeypatch):
    """Default project list: a single project `widget`."""
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Widget"}],
        raising=True,
    )
    # No goal on disk unless a test sets one.
    monkeypatch.setattr(svc, "_sprint_goal", lambda project_key, slug, label: "", raising=True)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


# ── seeding helpers (all real writes) ────────────────────────────────────────

def _sprint(db, label, state, *, project=REPO, started_at=None, ended_at=None,
            created_at=None):
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            (label, project, state, created_at, started_at, ended_at),
        )
        conn.commit()


def _issue(db, number, title, labels, *, repo=REPO, state="open"):
    db.upsert_issues(repo, [{
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": n} for n in labels],
        "updatedAt": "2026-06-10T00:00:00Z",
    }])


def _order(db, label, nums):
    db.set_sprint_ticket_order(label, nums)


def _agent_run(db, issue_number, label, agent, *, started_at, finished_at=None):
    with db.get_conn() as conn:
        db._create_agent_runs_table(conn)
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?)",
            (issue_number, label, agent, started_at, finished_at),
        )
        conn.commit()


def _event(db, *, project=REPO, source="agent", event_type="agent_finished",
           target=None, data=None, created_at=START):
    # Seed the `events` table — the real activity source (and what the Logs
    # timeline reads). The original seeded `project_events`, a table nothing
    # writes in production, which let recent_activity ship permanently empty.
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, "
            "target, action_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project, created_at, source, "test", event_type, target or "",
             None, json.dumps(data) if data is not None else "{}"),
        )
        conn.commit()


def _finished_sprint(db, label="sprint-50", *, done_titles=("Add login", "Fix bug"),
                     skipped_titles=("Defer cache",), pr_number=321,
                     summary_issue=400):
    """Seed a completed sprint inside the DATE window with done + skipped tickets."""
    _sprint(db, label, "completed", started_at=f"{DATE}T08:00:00",
            ended_at=f"{DATE}T08:45:00", created_at=f"{DATE}T07:00:00")
    nums = []
    n = 100
    for t in done_titles:
        _issue(db, n, t, ["done"], state="closed")
        nums.append(n)
        n += 1
    for t in skipped_titles:
        _issue(db, n, t, ["skipped"], state="closed")
        nums.append(n)
        n += 1
    _order(db, label, nums)
    # finish metadata lives in the Logs feed (project_events) keyed by sprint label
    _event(db, source="dashboard", event_type="sprint_finished", target=label,
           data={"pr_number": pr_number, "summary_issue_number": summary_issue},
           created_at=f"{DATE}T08:45:00")


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_route_in_new_router_not_server(client, fresh_db):
    paths = {r.path for r in router_mod.router.routes}
    assert "/api/projects/{slug}/brief" in paths
    assert "/api/brief" in paths
    # Routes are NOT defined in the server.py monolith.
    assert "/api/projects/{slug}/brief" not in SERVER_PY_SRC
    assert '"/api/brief"' not in SERVER_PY_SRC
    # The new router file exists and is mounted by an already-mounted router.
    assert (ROUTERS_DIR / "brief.py").exists()
    assert (ROUTERS_DIR / "brief_service.py").exists()
    assert "brief.router" in SPRINTS_PY_SRC or "brief_router" in SPRINTS_PY_SRC


def test_ac1_project_brief_sections(client, fresh_db):
    _finished_sprint(fresh_db)
    r = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE})
    assert r.status_code == 200
    body = r.json()
    for key in ("shipped", "in_progress", "up_next", "blocked", "kpis",
                "recent_activity"):
        assert key in body, f"missing section {key}"


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_home_brief_sections(client, fresh_db):
    _finished_sprint(fresh_db)
    r = client.get("/api/brief", params={"date": DATE})
    assert r.status_code == 200
    body = r.json()
    assert "global_kpis" in body
    assert "decisions" in body and isinstance(body["decisions"], list)
    assert "projects" in body and isinstance(body["projects"], list)
    # each project element matches the per-project brief shape
    assert body["projects"], "expected at least one project"
    for key in ("shipped", "in_progress", "up_next", "blocked", "kpis",
                "recent_activity"):
        assert key in body["projects"][0]


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_ac3_date_windows_queries(client, fresh_db):
    # A sprint that finished OUTSIDE the window must not appear.
    _sprint(fresh_db, "sprint-old", "completed",
            started_at="2026-06-01T08:00:00", ended_at="2026-06-01T09:00:00",
            created_at="2026-06-01T07:00:00")
    _finished_sprint(fresh_db, "sprint-50")
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    labels = {s["label"] for s in body["shipped"]}
    assert "sprint-50" in labels
    assert "sprint-old" not in labels


def test_ac3_default_date_is_today(client, fresh_db):
    # Omitting `date` must default to today and still return the standard shape.
    body = client.get(f"/api/projects/{SLUG}/brief").json()
    assert body["date"] == TODAY
    for key in ("shipped", "in_progress", "up_next", "blocked", "kpis",
                "recent_activity"):
        assert key in body


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_empty_project_returns_200_and_empty(client, fresh_db):
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE})
    assert body.status_code == 200
    j = body.json()
    assert j["shipped"] == []
    assert j["in_progress"] is None
    assert j["up_next"] is None
    assert j["blocked"] == []
    assert j["recent_activity"] == []
    assert isinstance(j["kpis"], dict)


# ── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_no_llm_or_network_call(fresh_db, monkeypatch):
    _finished_sprint(fresh_db)

    import http.client
    import socket
    import subprocess
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("network/subprocess call attempted by brief API")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", _boom, raising=True)
    monkeypatch.setattr(urllib.request, "urlopen", _boom, raising=True)
    monkeypatch.setattr(subprocess, "run", _boom, raising=True)
    monkeypatch.setattr(subprocess, "check_output", _boom, raising=True)

    proj = svc.build_project_brief(SLUG, date=DATE)
    home = svc.build_home_brief(date=DATE)
    assert proj["shipped"]
    assert home["projects"]


# ── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_shipped_fields(client, fresh_db, monkeypatch):
    monkeypatch.setattr(svc, "_sprint_goal",
                        lambda project_key, slug, label: "Ship the login flow",
                        raising=True)
    _finished_sprint(fresh_db, "sprint-50",
                     done_titles=("Add login", "Fix bug"),
                     skipped_titles=("Defer cache",),
                     pr_number=321, summary_issue=400)
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    assert len(body["shipped"]) == 1
    s = body["shipped"][0]
    for key in ("label", "goal", "features", "done", "skipped", "duration",
                "pr_number", "summary_issue_number"):
        assert key in s, f"missing {key}"
    assert s["label"] == "sprint-50"
    assert s["goal"] == "Ship the login flow"
    assert set(s["features"]) == {"Add login", "Fix bug"}
    assert s["done"] == 2
    assert s["skipped"] == 1
    assert s["duration"] == 45 * 60  # 45 minutes
    assert s["pr_number"] == 321
    assert s["summary_issue_number"] == 400


# ── AC7 ──────────────────────────────────────────────────────────────────────

def test_ac7_in_progress_fields(client, fresh_db):
    _sprint(fresh_db, "sprint-51", "running",
            started_at="2026-06-10T08:00:00", created_at="2026-06-10T07:00:00")
    _issue(fresh_db, 200, "Build dashboard", ["done"], state="closed")
    _issue(fresh_db, 201, "Wire SSE", ["in-progress"])
    _order(fresh_db, "sprint-51", [200, 201])
    _agent_run(fresh_db, 201, "sprint-51", "coder", started_at="2026-06-10T08:10:00")
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    ip = body["in_progress"]
    assert ip is not None
    assert ip["sprint_label"] == "sprint-51"
    assert ip["current_ticket"]["issue_number"] == 201
    assert ip["current_ticket"]["title"] == "Wire SSE"
    assert ip["active_agent"] == "coder"
    assert ip["progress"]["done"] == 1
    assert ip["progress"]["total"] == 2
    assert ip["progress"]["percent"] == 50
    assert "elapsed" in ip


# ── AC8 ──────────────────────────────────────────────────────────────────────

def test_ac8_up_next_ready_false_without_goal(client, fresh_db):
    _sprint(fresh_db, "sprint-52", "planning", created_at="2026-06-10T07:00:00")
    _order(fresh_db, "sprint-52", [300, 301, 302])
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    un = body["up_next"]
    assert un is not None
    assert un["label"] == "sprint-52"
    assert un["ticket_count"] == 3
    assert un["ready"] is False


def test_ac8_up_next_ready_true_with_goal(client, fresh_db, monkeypatch):
    monkeypatch.setattr(svc, "_sprint_goal",
                        lambda project_key, slug, label: "Real goal", raising=True)
    _sprint(fresh_db, "sprint-52", "planning", created_at="2026-06-10T07:00:00")
    _order(fresh_db, "sprint-52", [300])
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    assert body["up_next"]["ready"] is True


# ── AC9 ──────────────────────────────────────────────────────────────────────

def test_ac9_blocked_lists_rework_and_failed(client, fresh_db):
    _issue(fresh_db, 500, "Rework auth", ["rework"])
    _issue(fresh_db, 501, "Returned ticket", ["returned-from-qa"])
    _issue(fresh_db, 502, "Failed ticket", ["failed"])
    _issue(fresh_db, 503, "Healthy ticket", ["in-progress"])
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    blocked = body["blocked"]
    nums = {b["issue_number"] for b in blocked}
    assert {500, 501, 502} <= nums
    assert 503 not in nums
    for b in blocked:
        assert "title" in b and "type" in b
    types_by_num = {b["issue_number"]: b["type"] for b in blocked}
    assert types_by_num[502] == "failed"
    assert types_by_num[500] == "rework"


# ── AC10 ─────────────────────────────────────────────────────────────────────

def test_ac10_kpis_scoped_to_project_and_window(client, fresh_db):
    _finished_sprint(fresh_db, "sprint-50",
                     done_titles=("A", "B"), skipped_titles=("C",))
    _issue(fresh_db, 900, "Awaiting sign-off", ["uat"])
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    k = body["kpis"]
    assert k["sprints_shipped"] == 1
    assert k["tickets_done"] == 2
    assert k["in_progress"] is False
    assert "in_progress_percent" in k
    assert k["needs_you"] == 1


# ── AC11 ─────────────────────────────────────────────────────────────────────

def test_ac11_recent_activity_shape(client, fresh_db):
    _event(fresh_db, source="agent", event_type="agent_finished",
           data={"message": "coder finished #1"}, created_at=f"{DATE}T08:30:00")
    _event(fresh_db, source="github", event_type="pr_merged",
           data={"message": "merged PR 12"}, created_at=f"{DATE}T08:40:00")
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    acts = body["recent_activity"]
    assert len(acts) == 2
    for a in acts:
        assert "time" in a and "source" in a and "message" in a
    # newest first
    assert acts[0]["message"] == "merged PR 12"


# ── AC12 ─────────────────────────────────────────────────────────────────────

def test_ac12_decisions_shape_and_types(client, fresh_db, monkeypatch):
    # run_ready: planning sprint WITH a goal + tickets
    monkeypatch.setattr(
        svc, "_sprint_goal",
        lambda project_key, slug, label: "Goal" if label == "sprint-ready" else "",
        raising=True)
    _sprint(fresh_db, "sprint-ready", "planning", created_at="2026-06-10T07:00:00")
    _order(fresh_db, "sprint-ready", [600])
    # empty_sprint: planning sprint with no tickets — different project would be
    # cleaner, but a second planning sprint with zero tickets exercises the type.
    _sprint(fresh_db, "sprint-empty", "planning", created_at="2026-06-10T06:00:00")
    # rework: a blocked rework ticket
    _issue(fresh_db, 601, "Rework me", ["rework"])
    # branch_cleanup: a shipped sprint with a PR number
    _finished_sprint(fresh_db, "sprint-shipped", pr_number=77, summary_issue=88)

    body = client.get("/api/brief", params={"date": DATE}).json()
    decisions = body["decisions"]
    assert decisions, "expected decisions"
    for d in decisions:
        for key in ("project", "type", "label", "suggested_action"):
            assert key in d, f"decision missing {key}: {d}"
        assert d["type"] in {"run_ready", "rework", "branch_cleanup", "empty_sprint"}
    seen_types = {d["type"] for d in decisions}
    assert "run_ready" in seen_types
    assert "rework" in seen_types
    assert "branch_cleanup" in seen_types


# ── AC13 ─────────────────────────────────────────────────────────────────────

def test_ac13_global_kpis_sums_across_projects(client, fresh_db, monkeypatch):
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": "owner/widget", "name": "Widget"},
                 {"repo": "owner/gadget", "name": "Gadget"}],
        raising=True)
    # widget: one shipped sprint (2 done) + 1 uat needs-you
    _finished_sprint(fresh_db, "sprint-50", done_titles=("A", "B"),
                     skipped_titles=())
    _issue(fresh_db, 900, "Sign off widget", ["uat"])
    # gadget: one shipped sprint (1 done)
    _sprint(fresh_db, "g-sprint", "completed", project="owner/gadget",
            started_at=f"{DATE}T08:00:00", ended_at=f"{DATE}T08:30:00",
            created_at=f"{DATE}T07:00:00")
    _issue(fresh_db, 950, "Gadget feature", ["done"], repo="owner/gadget",
           state="closed")
    _order(fresh_db, "g-sprint", [950])

    body = client.get("/api/brief", params={"date": DATE}).json()
    g = body["global_kpis"]
    assert g["sprints_shipped"] == 2
    assert g["tickets_done"] == 3
    assert "in_progress" in g
    assert g["needs_your_call"] == 1


# ── AC14 ─────────────────────────────────────────────────────────────────────

def test_ac14_gated_on_project_events_table(client, fresh_db):
    # Drop the dependency table — the brief reads the `events` activity feed.
    with fresh_db.get_conn() as conn:
        conn.execute("DROP TABLE IF EXISTS events")
        conn.commit()
    r = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE})
    assert r.status_code == 503
    detail = r.json()["detail"].lower()
    assert "events" in detail or "logs" in detail

    r2 = client.get("/api/brief", params={"date": DATE})
    assert r2.status_code == 503

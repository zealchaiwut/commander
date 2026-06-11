"""Tester verification for issue #839 — brief assembly API (runs against a LIVE server).

The shared UAT instance (port 8001) is pinned to a stale branch and 404s on the
brief routes, so this suite boots the *feature branch* code as a real uvicorn
subprocess against a seeded temp SQLite DB and drives it over HTTP with httpx —
exactly as a deployed UAT box would be exercised. This proves the routes are
actually mounted/reachable at runtime (the include_router wiring works, no import
errors at startup), which an in-process TestClient cannot prove.

One test per acceptance criterion (MEDIUM risk → focused, mostly happy-path with
the obvious edges). Data preconditions are seeded keyed by the project *slug*
(``_resolve_project_key`` falls back to the slug and its match-set includes it),
so the live endpoint returns deterministic data without touching the real
projects store.

AC5 (no LLM/network) is verified by static inspection of the router source — a
positive proof over HTTP would require a negative-traffic assertion the live
server can't give; the coder's in-process suite monkeypatches the network layer
to prove it dynamically.
AC14's *missing-table* branch is likewise covered in-process (init_db always
creates project_events, so a live box always has it); here we assert the
positive path (200, not 503).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

SLUG = "brieftest839"            # not a real tracked project → isolated rows
REPO_KEY = SLUG                  # rows are seeded keyed by the slug itself
DATE = "2026-06-10"
WIN_START = f"{DATE}T08:00:00"
TODAY = datetime.now(timezone.utc).date().isoformat()

# --- temp DB wired in before importing db (db reads DB_PATH at import) ---------
_TMPDIR = tempfile.mkdtemp(prefix="brief_uat_839_")
DB_FILE = str(Path(_TMPDIR) / "brief_839.db")
os.environ["DB_PATH"] = DB_FILE
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import db  # noqa: E402  (import after DB_PATH is set)


# ── seeding (setup only — never the assertion path) ───────────────────────────

def _seed():
    db.init_db()
    # Completed sprint inside the window: 2 done + 1 skipped ticket.
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sprint-bt-50", REPO_KEY, "completed", f"{DATE}T07:00:00",
             f"{DATE}T08:00:00", f"{DATE}T08:45:00"),
        )
        # Running sprint (today-ish window not required: in_progress isn't windowed).
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sprint-bt-51", REPO_KEY, "running", f"{DATE}T09:00:00",
             f"{DATE}T09:30:00", None),
        )
        # Planning sprint (up_next) with NO goal on disk → ready False.
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sprint-bt-52", REPO_KEY, "planning", f"{DATE}T10:00:00", None, None),
        )
        conn.commit()

    # Mirrored issues.
    db.upsert_issues(REPO_KEY, [
        {"number": 100, "title": "Add login", "state": "closed",
         "labels": [{"name": "done"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 101, "title": "Fix bug", "state": "closed",
         "labels": [{"name": "done"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 102, "title": "Defer cache", "state": "closed",
         "labels": [{"name": "skipped"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 200, "title": "Rework needed", "state": "open",
         "labels": [{"name": "rework"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 201, "title": "Build broke", "state": "open",
         "labels": [{"name": "failed"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 300, "title": "Active work", "state": "open",
         "labels": [], "updatedAt": f"{DATE}T08:00:00Z"},
    ])
    db.set_sprint_ticket_order("sprint-bt-50", [100, 101, 102])
    db.set_sprint_ticket_order("sprint-bt-51", [300])
    db.set_sprint_ticket_order("sprint-bt-52", [200, 201])

    # Open agent run on the running sprint → current_ticket + active_agent.
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?)",
            (300, "sprint-bt-51", "coder", f"{DATE}T09:30:00", None),
        )
        # sprint_finished event carries pr_number / summary_issue_number.
        conn.execute(
            "INSERT INTO project_events (project, created_at, source, event_type, "
            "target, actor, action_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (REPO_KEY, f"{DATE}T08:45:00", "dashboard", "sprint_finished",
             "sprint-bt-50", None, None,
             json.dumps({"pr_number": 321, "summary_issue_number": 400})),
        )
        # A plain activity event in the window for recent_activity.
        conn.execute(
            "INSERT INTO project_events (project, created_at, source, event_type, "
            "target, actor, action_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (REPO_KEY, WIN_START, "agent", "agent_finished", "sprint-bt-50",
             None, None, json.dumps({"message": "coder finished #100"})),
        )
        conn.commit()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def base_url():
    _seed()
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "DB_PATH": DB_FILE,
        "ENVIRONMENT": "uat",
        "COMMANDER_DISABLE_NEON": "1",
        "PYTHONPATH": str(DASHBOARD_DIR),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(DASHBOARD_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                raise RuntimeError(f"server exited early (code {proc.returncode}):\n{out[-3000:]}")
            try:
                r = httpx.get(f"{url}/api/brief?date={DATE}", timeout=2.0)
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        if not ready:
            raise RuntimeError("server did not become ready within 40s")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        yield c


# ── Acceptance Criteria ───────────────────────────────────────────────────────

def test_brief_assembly_api__project_brief_has_all_sections(client):
    # AC1: per-project brief returns all six sections, populated from real data.
    r = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("shipped", "in_progress", "up_next", "blocked", "kpis", "recent_activity"):
        assert key in body, f"missing section {key}"


def test_brief_assembly_api__home_brief_has_all_sections(client):
    # AC2: home roll-up returns global_kpis, decisions, projects.
    r = client.get("/api/brief", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(("global_kpis", "decisions", "projects")) <= set(body)
    assert isinstance(body["projects"], list)
    assert isinstance(body["decisions"], list)


def test_brief_assembly_api__date_defaults_to_today(client):
    # AC3: omitting `date` defaults to today and returns the same shape.
    r = client.get(f"/api/projects/{SLUG}/brief")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == TODAY
    for key in ("shipped", "in_progress", "up_next", "blocked", "kpis", "recent_activity"):
        assert key in body


def test_brief_assembly_api__empty_project_returns_200_empty(client):
    # AC4: a project with no activity returns empty sections, never 4xx/5xx.
    r = client.get("/api/projects/no-such-project-xyz/brief", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shipped"] == []
    assert body["blocked"] == []
    assert body["in_progress"] is None
    assert body["up_next"] is None
    assert body["recent_activity"] == []


def test_brief_assembly_api__no_llm_or_network_imports(client):
    # AC5: static proof neither router module imports an LLM/network client.
    forbidden = ("import anthropic", "import openai", "import requests",
                 "from anthropic", "from openai", "import urllib.request")
    for fname in ("brief.py", "brief_service.py"):
        src = (ROUTERS_DIR / fname).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{fname} unexpectedly references {token!r}"


def test_brief_assembly_api__shipped_fields(client):
    # AC6: shipped entry carries all required fields with correct values.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    shipped = body["shipped"]
    assert len(shipped) >= 1, "expected a completed sprint in the window"
    entry = next(s for s in shipped if s["label"] == "sprint-bt-50")
    for key in ("label", "goal", "features", "done", "skipped", "duration",
                "pr_number", "summary_issue_number"):
        assert key in entry, f"shipped entry missing {key}"
    assert entry["done"] == 2
    assert entry["skipped"] == 1
    assert sorted(entry["features"]) == ["Add login", "Fix bug"]
    assert entry["pr_number"] == 321
    assert entry["summary_issue_number"] == 400
    assert entry["duration"] == 45 * 60  # 08:00 → 08:45


def test_brief_assembly_api__in_progress_fields(client):
    # AC7: in_progress carries sprint_label, current_ticket, agent, progress, elapsed.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    ip = body["in_progress"]
    assert ip is not None
    assert ip["sprint_label"] == "sprint-bt-51"
    assert ip["current_ticket"]["issue_number"] == 300
    assert ip["current_ticket"]["title"] == "Active work"
    assert ip["active_agent"] == "coder"
    prog = ip["progress"]
    assert set(("done", "total", "percent")) <= set(prog)
    assert prog["total"] == 1
    assert "elapsed" in ip


def test_brief_assembly_api__up_next_ready_false_without_goal(client):
    # AC8: up_next carries label, ticket_count, ready=False when no goal set.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    un = body["up_next"]
    assert un is not None
    assert un["label"] == "sprint-bt-52"
    assert un["ticket_count"] == 2
    assert un["ready"] is False


def test_brief_assembly_api__blocked_lists_rework_and_failed(client):
    # AC9: blocked lists rework + failed tickets with issue numbers and titles.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    blocked = body["blocked"]
    nums = {b["issue_number"] for b in blocked}
    types = {b["type"] for b in blocked}
    assert 200 in nums and 201 in nums
    assert {"rework", "failed"} <= types
    assert all(b["title"] for b in blocked)


def test_brief_assembly_api__kpis_scoped_to_project_and_window(client):
    # AC10: kpis carry shipped count, tickets done, in_progress flag + percent, needs_you.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    kpis = body["kpis"]
    for key in ("sprints_shipped", "tickets_done", "in_progress",
                "in_progress_percent", "needs_you"):
        assert key in kpis, f"kpis missing {key}"
    assert kpis["sprints_shipped"] >= 1
    assert kpis["tickets_done"] == 2
    assert kpis["in_progress"] is True


def test_brief_assembly_api__recent_activity_shape(client):
    # AC11: recent_activity items carry time, source, message.
    body = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    acts = body["recent_activity"]
    assert len(acts) >= 1
    for a in acts:
        assert set(("time", "source", "message")) <= set(a)


def test_brief_assembly_api__decisions_shape_and_types(client):
    # AC12: every home decision carries project/type/label/suggested_action and a known type.
    body = client.get("/api/brief", params={"date": DATE}).json()
    allowed = {"run_ready", "rework", "branch_cleanup", "empty_sprint"}
    for d in body["decisions"]:
        assert set(("project", "type", "label", "suggested_action")) <= set(d)
        assert d["type"] in allowed, f"unexpected decision type {d['type']}"


def test_brief_assembly_api__global_kpis_sums(client):
    # AC13: home global_kpis carries the four summed integer fields.
    body = client.get("/api/brief", params={"date": DATE}).json()
    gk = body["global_kpis"]
    for key in ("sprints_shipped", "tickets_done", "in_progress", "needs_your_call"):
        assert key in gk
        assert isinstance(gk[key], int)


def test_brief_assembly_api__dependency_gate_positive(client):
    # AC14 (positive path): with project_events present, endpoint serves 200, not 503.
    r = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE})
    assert r.status_code == 200, r.text
    assert r.status_code != 503

"""Tests for issue #841 — store and serve daily brief as a persistent artifact.

Each test is anchored to a specific acceptance criterion. A real (temp) SQLite
file is seeded with real rows; the only seam patched is
``brief_summary._call_model`` (the subprocess boundary) so generation never
spawns ``claude``, plus ``brief_artifact._now_iso`` so generation timestamps are
deterministic and distinct.

AC1  Each brief is generated and stored keyed by ``(project, date)`` in a
     persistent store.
AC2  On the first request of the day for a project (lazy), a new brief is
     generated and saved — not on every page load.
AC3  Subsequent loads for the same ``(project, date)`` return the stored brief
     without recomputation.
AC4  The date-navigation path returns the stored brief for past dates when
     browsed (and does NOT live-recompute one that was never stored).
AC5  The brief includes a computed "covering since" label reflecting the start
     of the daily window (e.g. "Covering activity since Jun 10, 6:00 AM").
AC6  The daily window is a fixed 24h window anchored at a configured time
     (default: 6 AM).
AC7  A "Regenerate" action updates the stored summary for the current date and
     records the new generation timestamp.
AC8  Dates with no stored brief show a clear empty state rather than an error.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

REPO = "owner/widget"
SLUG = "widget"

# Brief date "today" for the tests, and the date before it.
TODAY = "2026-06-11"
YESTERDAY = "2026-06-10"
# With a 6 AM anchor, the window for brief date TODAY is
# [2026-06-10T06:00:00, 2026-06-11T06:00:00). Seed activity inside it.
IN_WINDOW = "2026-06-10T08:00:00"     # after yesterday 6 AM, before today 6 AM
BEFORE_WINDOW = "2026-06-10T05:00:00"  # before yesterday 6 AM — excluded


def _load_module(name: str, path: Path):
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
summ = _load_module("routers.brief_summary", ROUTERS_DIR / "brief_summary.py")
art = _load_module("routers.brief_artifact", ROUTERS_DIR / "brief_artifact.py")
router_mod = _load_module("routers.brief", ROUTERS_DIR / "brief.py")


@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_841.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture(autouse=True)
def project_and_config(monkeypatch):
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Widget"}],
        raising=True,
    )
    monkeypatch.setattr(svc, "_sprint_goal",
                        lambda project_key, slug, label: "Ship the login flow",
                        raising=True)
    monkeypatch.delenv("COMMANDER_DISABLE_BRIEF_LLM", raising=False)
    # Default anchor (6 AM) unless a test overrides it.
    monkeypatch.delenv("BRIEF_WINDOW_ANCHOR_HOUR", raising=False)
    art.ANCHOR_HOUR = art._anchor_hour()
    # Pin "today" so the lazy-vs-stored distinction is deterministic.
    monkeypatch.setattr(art, "current_brief_date", lambda: TODAY, raising=True)


@pytest.fixture(autouse=True)
def model_seam(monkeypatch):
    """Model seam returns a fixed sentence and records every call."""
    calls: list[str] = []

    def _fake(prompt: str):
        calls.append(prompt)
        return "Shipped one sprint. One sprint is running. A few items need attention."

    monkeypatch.setattr(summ, "_call_model", _fake, raising=True)
    return calls


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Deterministic, strictly-increasing generation timestamps."""
    state = {"n": 0}

    def _tick():
        state["n"] += 1
        return f"2026-06-11T12:00:{state['n']:02d}"

    monkeypatch.setattr(art, "_now_iso", _tick, raising=True)
    return state


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


# ── seeding helpers (real writes) ─────────────────────────────────────────────

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


def _event(db, *, project=REPO, source="dashboard", event_type="sprint_finished",
           target=None, data=None, created_at=IN_WINDOW):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO project_events (project, created_at, source, event_type, "
            "target, actor, action_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project, created_at, source, event_type, target, None, None,
             json.dumps(data) if data is not None else None),
        )
        conn.commit()


def _seed_rich_project(db):
    """Shipped sprint + running sprint + a blocked ticket, all inside the window."""
    _sprint(db, "sprint-50", "completed", started_at=f"{YESTERDAY}T07:00:00",
            ended_at=IN_WINDOW, created_at=f"{YESTERDAY}T06:30:00")
    _issue(db, 100, "Add login", ["done"], state="closed")
    _issue(db, 101, "Fix bug", ["done"], state="closed")
    _issue(db, 102, "Defer cache", ["skipped"], state="closed")
    _order(db, "sprint-50", [100, 101, 102])
    _event(db, target="sprint-50",
           data={"pr_number": 321, "summary_issue_number": 400},
           created_at=IN_WINDOW)
    _sprint(db, "sprint-51", "running", started_at=f"{YESTERDAY}T09:00:00",
            created_at=f"{YESTERDAY}T08:50:00")
    _issue(db, 200, "Build dashboard", ["done"], state="closed")
    _issue(db, 201, "Wire API", [], state="open")
    _issue(db, 202, "Write docs", [], state="open")
    _issue(db, 203, "Polish UI", [], state="open")
    _order(db, "sprint-51", [200, 201, 202, 203])
    _issue(db, 300, "Broken thing", ["rework"], state="open")
    _issue(db, 301, "Awaiting signoff", ["uat"], state="open")


# ══ AC1 ═══════════════════════════════════════════════════════════════════════

def test_ac1_brief_stored_keyed_by_project_and_date(fresh_db):
    _seed_rich_project(fresh_db)
    result = art.get_or_create_project_artifact(SLUG, date=TODAY)
    assert result["available"] is True
    # A persistent row now exists keyed by (scope, project, date).
    stored = db_module.get_brief_artifact("project", SLUG, TODAY)
    assert stored is not None
    assert stored["payload"]["brief"]["project"] == SLUG
    assert stored["payload"]["date"] == TODAY
    assert stored["generated_at"]


# ══ AC2 ═══════════════════════════════════════════════════════════════════════

def test_ac2_first_request_generates_and_saves(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    # Nothing stored yet.
    assert db_module.get_brief_artifact("project", SLUG, TODAY) is None

    calls = {"n": 0}
    orig = art._generate_project_artifact

    def _spy(slug, date):
        calls["n"] += 1
        return orig(slug, date)

    monkeypatch.setattr(art, "_generate_project_artifact", _spy, raising=True)
    art.get_or_create_project_artifact(SLUG, date=TODAY)
    assert calls["n"] == 1
    assert db_module.get_brief_artifact("project", SLUG, TODAY) is not None


# ══ AC3 ═══════════════════════════════════════════════════════════════════════

def test_ac3_subsequent_loads_served_from_store(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    calls = {"n": 0}
    orig = art._generate_project_artifact

    def _spy(slug, date):
        calls["n"] += 1
        return orig(slug, date)

    monkeypatch.setattr(art, "_generate_project_artifact", _spy, raising=True)
    first = art.get_or_create_project_artifact(SLUG, date=TODAY)
    second = art.get_or_create_project_artifact(SLUG, date=TODAY)
    # Generated exactly once; second load is served verbatim from the store.
    assert calls["n"] == 1
    assert second["generated_at"] == first["generated_at"]
    assert second["brief"] == first["brief"]
    assert second["summary"] == first["summary"]


# ══ AC4 ═══════════════════════════════════════════════════════════════════════

def test_ac4_past_date_returns_stored_without_recompute(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    # Pre-store a brief for yesterday (a "past" date relative to TODAY).
    payload = {
        "scope": "project", "project": SLUG, "date": YESTERDAY,
        "covering_since": art.covering_since_label(YESTERDAY),
        "window_start": art.daily_window(YESTERDAY)[0],
        "window_end": art.daily_window(YESTERDAY)[1],
        "summary": "Yesterday's stored summary.", "summary_source": "model",
        "brief": {"project": SLUG, "date": YESTERDAY, "shipped": []},
    }
    db_module.set_brief_artifact("project", SLUG, YESTERDAY, payload,
                                 generated_at="2026-06-10T12:00:00")

    def _boom(slug, date):
        raise AssertionError("must not regenerate a stored past-date brief")

    monkeypatch.setattr(art, "_generate_project_artifact", _boom, raising=True)
    result = art.get_or_create_project_artifact(SLUG, date=YESTERDAY)
    assert result["available"] is True
    assert result["summary"] == "Yesterday's stored summary."
    assert result["generated_at"] == "2026-06-10T12:00:00"


# ══ AC5 ═══════════════════════════════════════════════════════════════════════

def test_ac5_covering_since_label(fresh_db):
    _seed_rich_project(fresh_db)
    result = art.get_or_create_project_artifact(SLUG, date=TODAY)
    # Window starts at yesterday's anchor; label reads e.g. "Jun 10, 6:00 AM".
    assert result["covering_since"] == "Jun 10, 6:00 AM"


def test_ac5_covering_since_label_pure():
    assert art.covering_since_label("2026-06-11") == "Jun 10, 6:00 AM"
    # 12-hour formatting around noon/midnight stays correct.
    assert art.covering_since_label("2026-01-02") == "Jan 1, 6:00 AM"


# ══ AC6 ═══════════════════════════════════════════════════════════════════════

def test_ac6_window_is_24h_anchored_at_6am():
    start, end = art.daily_window(TODAY)
    assert start == "2026-06-10T06:00:00"
    assert end == "2026-06-11T06:00:00"


def test_ac6_anchor_hour_is_configurable(monkeypatch):
    monkeypatch.setenv("BRIEF_WINDOW_ANCHOR_HOUR", "9")
    monkeypatch.setattr(art, "ANCHOR_HOUR", art._anchor_hour(), raising=True)
    start, end = art.daily_window(TODAY)
    assert start == "2026-06-10T09:00:00"
    assert end == "2026-06-11T09:00:00"
    assert art.covering_since_label(TODAY) == "Jun 10, 9:00 AM"


def test_ac6_assembly_respects_window(fresh_db):
    """Activity inside the 6 AM window counts; activity before it is excluded."""
    # Shipped sprint ended inside the window.
    _sprint(fresh_db, "sprint-60", "completed", started_at=f"{YESTERDAY}T06:30:00",
            ended_at=IN_WINDOW, created_at=f"{YESTERDAY}T06:00:00")
    _issue(fresh_db, 500, "In-window feature", ["done"], state="closed")
    _order(fresh_db, "sprint-60", [500])
    # A second sprint ended BEFORE the window start — must be excluded.
    _sprint(fresh_db, "sprint-59", "completed", started_at=f"{YESTERDAY}T04:00:00",
            ended_at=BEFORE_WINDOW, created_at=f"{YESTERDAY}T03:00:00")
    _issue(fresh_db, 499, "Pre-window feature", ["done"], state="closed")
    _order(fresh_db, "sprint-59", [499])

    result = art.get_or_create_project_artifact(SLUG, date=TODAY)
    labels = [s["label"] for s in result["brief"]["shipped"]]
    assert "sprint-60" in labels
    assert "sprint-59" not in labels


# ══ AC7 ═══════════════════════════════════════════════════════════════════════

def test_ac7_regenerate_updates_summary_and_timestamp(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    counter = {"n": 0}

    def _fake(prompt):
        counter["n"] += 1
        return f"Summary version {counter['n']}."

    monkeypatch.setattr(summ, "_call_model", _fake, raising=True)

    first = art.get_or_create_project_artifact(SLUG, date=TODAY)
    assert first["summary"] == "Summary version 1."
    t1 = first["generated_at"]

    regen = art.get_or_create_project_artifact(SLUG, date=TODAY, force=True)
    assert regen["summary"] == "Summary version 2."
    assert regen["generated_at"] != t1, "generation timestamp must advance"

    # The regenerated brief persists; a later load does not re-invoke the model.
    again = art.get_or_create_project_artifact(SLUG, date=TODAY)
    assert again["summary"] == "Summary version 2."
    assert again["generated_at"] == regen["generated_at"]
    assert counter["n"] == 2


def test_ac7_regenerate_endpoint(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    first = client.get(f"/api/projects/{SLUG}/brief/daily",
                       params={"date": TODAY}).json()
    regen = client.post(f"/api/projects/{SLUG}/brief/daily/regenerate",
                        params={"date": TODAY}).json()
    assert regen["available"] is True
    assert regen["generated_at"] != first["generated_at"]


# ══ AC8 ═══════════════════════════════════════════════════════════════════════

def test_ac8_empty_state_for_unstored_past_date(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    old_date = "2026-01-01"  # before the feature; nothing stored

    def _boom(slug, date):
        raise AssertionError("must not live-recompute an unstored past date")

    monkeypatch.setattr(art, "_generate_project_artifact", _boom, raising=True)
    result = art.get_or_create_project_artifact(SLUG, date=old_date)
    assert result["available"] is False
    assert result["brief"] is None
    assert result["message"]
    assert db_module.get_brief_artifact("project", SLUG, old_date) is None


def test_ac8_empty_state_endpoint_is_200_not_error(client, fresh_db):
    r = client.get(f"/api/projects/{SLUG}/brief/daily",
                   params={"date": "2026-01-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["message"]


# ══ Endpoint happy paths + home roll-up ═══════════════════════════════════════

def test_project_daily_endpoint(client, fresh_db):
    _seed_rich_project(fresh_db)
    r = client.get(f"/api/projects/{SLUG}/brief/daily", params={"date": TODAY})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["covering_since"] == "Jun 10, 6:00 AM"
    assert body["brief"]["project"] == SLUG
    assert body["summary"]
    assert body["generated_at"]


def test_home_daily_artifact_stored_and_served(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    calls = {"n": 0}
    orig = art._generate_home_artifact

    def _spy(date):
        calls["n"] += 1
        return orig(date)

    monkeypatch.setattr(art, "_generate_home_artifact", _spy, raising=True)
    first = client.get("/api/brief/daily", params={"date": TODAY}).json()
    assert first["available"] is True
    assert first["covering_since"] == "Jun 10, 6:00 AM"
    assert first["summary"]
    # Stored + served on the next load (no regeneration).
    second = client.get("/api/brief/daily", params={"date": TODAY}).json()
    assert calls["n"] == 1
    assert second["generated_at"] == first["generated_at"]
    assert db_module.get_brief_artifact("home", "", TODAY) is not None

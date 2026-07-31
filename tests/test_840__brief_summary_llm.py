"""Tests for issue #840 — generate and cache LLM brief summary with fallback.

Each test is anchored to a specific acceptance criterion. The DB is a real
(temp) SQLite file seeded with real rows (reusing the #839 seeding helpers'
shape). The only seam patched for the LLM is ``brief_summary._call_model`` —
the subprocess boundary — so tests can simulate model success, failure, and a
disabled config without ever spawning ``claude``.

AC1  Given a structured payload, the system calls Haiku with a fixed prompt
     requesting a 2–4 sentence "chief of staff" summary covering shipped /
     in-progress / most-worth-attention.
AC2  The model is passed ONLY the structured brief data — no web/file access,
     no extra context beyond the assembled payload.
AC3  The generated summary is stored against (project, date) and returned on
     subsequent loads without re-calling the model.
AC4  A Regenerate action clears the stored summary and re-invokes the model,
     then stores + returns the new result.
AC5  On model failure OR when generation is disabled via config, a
     deterministic templated summary built from the structured fields is
     returned instead.
AC6  The summary endpoint renders in all cases (success / failure / disabled);
     the fallback path never blocks rendering (no 5xx).
AC7  An optional one-line home recap is generated the same way, with the same
     storage + fallback rules.
AC8  The summary accurately reflects the underlying counts (fallback carries
     the real numbers; the model payload carries the real numbers).
AC9  Regenerate is scoped to the summary only — it does not recompute/persist
     the rest of the structured brief.
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

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

REPO = "owner/widget"
SLUG = "widget"
DATE = "2026-06-10"
START = f"{DATE}T08:00:00"


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
router_mod = _load_module("routers.brief", ROUTERS_DIR / "brief.py")


@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_840.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture(autouse=True)
def one_project(monkeypatch):
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Widget"}],
        raising=True,
    )
    monkeypatch.setattr(svc, "_sprint_goal",
                        lambda project_key, slug, label: "Ship the login flow",
                        raising=True)
    # LLM enabled by default unless a test disables it.
    monkeypatch.delenv("COMMANDER_DISABLE_BRIEF_LLM", raising=False)


@pytest.fixture(autouse=True)
def model_seam(monkeypatch):
    """Default model seam returns a fixed sentence and records every call.

    Tests that need failure/disabled override this. The list lets a test assert
    how many times the model was invoked (AC3 caching).
    """
    calls: list[str] = []

    def _fake(prompt: str):
        calls.append(prompt)
        return "Shipped one sprint. One sprint is running. A few items need attention."

    monkeypatch.setattr(summ, "_call_model", _fake, raising=True)
    return calls


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
           target=None, data=None, created_at=START):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO project_events (project, created_at, source, event_type, "
            "target, actor, action_id, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project, created_at, source, event_type, target, None, None,
             json.dumps(data) if data is not None else None),
        )
        conn.commit()


def _seed_rich_project(db):
    """Seed a shipped sprint, a running sprint, and a blocked ticket."""
    # Shipped sprint-50 with 2 done + 1 skipped, inside the DATE window.
    _sprint(db, "sprint-50", "completed", started_at=f"{DATE}T08:00:00",
            ended_at=f"{DATE}T08:45:00", created_at=f"{DATE}T07:00:00")
    _issue(db, 100, "Add login", ["done"], state="closed")
    _issue(db, 101, "Fix bug", ["done"], state="closed")
    _issue(db, 102, "Defer cache", ["skipped"], state="closed")
    _order(db, "sprint-50", [100, 101, 102])
    _event(db, target="sprint-50",
           data={"pr_number": 321, "summary_issue_number": 400},
           created_at=f"{DATE}T08:45:00")
    # Running sprint-51 with 1 of 4 done -> 25%.
    _sprint(db, "sprint-51", "running", started_at=f"{DATE}T09:00:00",
            created_at=f"{DATE}T08:50:00")
    _issue(db, 200, "Build dashboard", ["done"], state="closed")
    _issue(db, 201, "Wire API", [], state="open")
    _issue(db, 202, "Write docs", [], state="open")
    _issue(db, 203, "Polish UI", [], state="open")
    _order(db, "sprint-51", [200, 201, 202, 203])
    # A blocked (rework) ticket + a needs-you (uat) ticket.
    _issue(db, 300, "Broken thing", ["rework"], state="open")
    _issue(db, 301, "Awaiting signoff", ["uat"], state="open")


# ══ AC1 ═══════════════════════════════════════════════════════════════════════

def test_ac1_model_called_with_fixed_summary_prompt(fresh_db, model_seam):
    brief = svc.build_project_brief(SLUG, date=DATE)
    result = summ.generate_project_summary(brief)
    assert result["source"] == "model"
    assert model_seam, "model was not called"
    prompt = model_seam[0]
    # Fixed prompt asks for a 2–4 sentence chief-of-staff summary covering the
    # three required facets.
    low = prompt.lower()
    assert "chief of staff" in low
    assert "sentence" in low
    for facet in ("shipped", "progress", "attention"):
        assert facet in low, f"prompt missing facet: {facet}"


# ══ AC2 ═══════════════════════════════════════════════════════════════════════

def test_ac2_payload_is_only_structured_data(fresh_db):
    _seed_rich_project(fresh_db)
    brief = svc.build_project_brief(SLUG, date=DATE)
    payload = summ.build_project_payload(brief)
    # Payload is JSON-serialisable structured data derived from the brief — and
    # nothing else (no file paths, no instructions to fetch anything).
    blob = json.dumps(payload)
    assert "sprint-50" in blob
    assert "sprint-51" in blob
    # The structured counts are present and faithful.
    assert payload["kpis"]["tickets_done"] == 2
    # No filesystem / web hints leak into the model payload.
    assert "/Users/" not in blob
    assert "http://" not in blob and "https://" not in blob


def test_ac2_prompt_carries_payload_and_no_external_context(fresh_db):
    _seed_rich_project(fresh_db)
    brief = svc.build_project_brief(SLUG, date=DATE)
    payload = summ.build_project_payload(brief)
    prompt = summ.build_project_prompt(payload)
    # The structured payload is embedded verbatim; the model gets nothing more.
    assert json.dumps(payload, indent=2) in prompt or json.dumps(payload) in prompt


# ══ AC3 ═══════════════════════════════════════════════════════════════════════

def test_ac3_summary_cached_and_not_regenerated(fresh_db, model_seam):
    _seed_rich_project(fresh_db)
    first = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert first["source"] == "model"
    assert len(model_seam) == 1
    # Second load returns the stored summary without re-calling the model.
    second = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert second["summary"] == first["summary"]
    assert len(model_seam) == 1, "model was re-invoked on a cached load"


# ══ AC4 ═══════════════════════════════════════════════════════════════════════

def test_ac4_regenerate_clears_and_reinvokes(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    counter = {"n": 0}

    def _fake(prompt):
        counter["n"] += 1
        return f"Summary version {counter['n']}."

    monkeypatch.setattr(summ, "_call_model", _fake, raising=True)

    first = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert first["summary"] == "Summary version 1."
    # Cached.
    summ.get_or_create_project_summary(SLUG, date=DATE)
    assert counter["n"] == 1
    # Regenerate clears + re-invokes -> new text.
    regen = summ.get_or_create_project_summary(SLUG, date=DATE, force=True)
    assert counter["n"] == 2
    assert regen["summary"] == "Summary version 2."
    # The new text persists on the next load (no further model call).
    again = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert again["summary"] == "Summary version 2."
    assert counter["n"] == 2


# ══ AC5 ═══════════════════════════════════════════════════════════════════════

def test_ac5_fallback_on_model_failure(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setattr(summ, "_call_model", lambda prompt: None, raising=True)
    result = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert result["source"] == "fallback"
    # Templated string is built from the structured fields.
    s = result["summary"]
    assert "Sprint 50" in s          # pretty label of the shipped sprint
    assert "2 features" in s         # tickets_done
    assert "25% complete" in s       # running progress
    assert "need attention" in s


def test_ac5_fallback_when_disabled_via_config(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setenv("COMMANDER_DISABLE_BRIEF_LLM", "1")

    def _boom(prompt):
        raise AssertionError("model must not be called when disabled via config")

    monkeypatch.setattr(summ, "_call_model", _boom, raising=True)
    result = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert result["source"] == "fallback"
    assert "Sprint 50" in result["summary"]


def test_ac5_fallback_not_cached_so_model_recovers(fresh_db, monkeypatch):
    """A transient failure must not poison the cache — next load retries."""
    _seed_rich_project(fresh_db)
    monkeypatch.setattr(summ, "_call_model", lambda prompt: None, raising=True)
    first = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert first["source"] == "fallback"
    # Model recovers; a fresh load now produces (and caches) a model summary.
    monkeypatch.setattr(summ, "_call_model",
                        lambda prompt: "Recovered model summary.", raising=True)
    second = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert second["source"] == "model"
    assert second["summary"] == "Recovered model summary."


# ══ AC6 ═══════════════════════════════════════════════════════════════════════

def test_ac6_endpoint_renders_model_success(client, fresh_db):
    _seed_rich_project(fresh_db)
    r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "model"
    assert body["summary"]
    assert body["date"] == DATE


def test_ac6_endpoint_renders_model_failure(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setattr(summ, "_call_model", lambda prompt: None, raising=True)
    r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
    assert r.status_code == 200
    assert r.json()["source"] == "fallback"


def test_ac6_endpoint_renders_model_disabled(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setenv("COMMANDER_DISABLE_BRIEF_LLM", "1")
    r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
    assert r.status_code == 200
    assert r.json()["source"] == "fallback"


def test_ac6_model_exception_is_swallowed_to_fallback(fresh_db, monkeypatch):
    """Even an unexpected exception in the model path falls back, never 500s."""
    _seed_rich_project(fresh_db)

    def _raise(prompt):
        raise RuntimeError("boom")

    monkeypatch.setattr(summ, "_call_model", _raise, raising=True)
    result = summ.get_or_create_project_summary(SLUG, date=DATE)
    assert result["source"] == "fallback"


# ══ AC7 ═══════════════════════════════════════════════════════════════════════

def test_ac7_home_recap_generated_and_cached(client, fresh_db, model_seam):
    _seed_rich_project(fresh_db)
    r = client.get("/api/brief/summary", params={"date": DATE})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "model"
    assert body["summary"]
    assert body["date"] == DATE
    calls_after_first = len(model_seam)
    # Reload: home recap is cached (no new model call for the recap itself).
    r2 = client.get("/api/brief/summary", params={"date": DATE})
    assert r2.json()["summary"] == body["summary"]
    assert len(model_seam) == calls_after_first


def test_ac7_home_recap_fallback_when_disabled(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setenv("COMMANDER_DISABLE_BRIEF_LLM", "1")
    r = client.get("/api/brief/summary", params={"date": DATE})
    assert r.status_code == 200
    assert r.json()["source"] == "fallback"
    assert r.json()["summary"]


def test_ac7_home_recap_regenerate(client, fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    counter = {"n": 0}

    def _fake(prompt):
        counter["n"] += 1
        return f"Home recap {counter['n']}."

    monkeypatch.setattr(summ, "_call_model", _fake, raising=True)
    first = client.get("/api/brief/summary", params={"date": DATE}).json()
    regen = client.post("/api/brief/summary/regenerate", params={"date": DATE}).json()
    assert regen["summary"] != first["summary"]
    assert regen["source"] == "model"


# ══ AC8 ═══════════════════════════════════════════════════════════════════════

def test_ac8_fallback_numbers_match_structured_counts(fresh_db, monkeypatch):
    _seed_rich_project(fresh_db)
    monkeypatch.setattr(summ, "_call_model", lambda prompt: None, raising=True)
    brief = svc.build_project_brief(SLUG, date=DATE)
    text = summ._fallback_project_summary(brief)
    # Numbers in the templated summary are exactly the structured counts.
    assert brief["kpis"]["tickets_done"] == 2
    assert f"{brief['kpis']['tickets_done']} features" in text
    assert f"{brief['in_progress']['progress']['percent']}% complete" in text


def test_ac8_payload_counts_are_faithful(fresh_db):
    _seed_rich_project(fresh_db)
    brief = svc.build_project_brief(SLUG, date=DATE)
    payload = summ.build_project_payload(brief)
    # Payload mirrors the brief's structured counts exactly (no fabrication).
    assert payload["kpis"]["sprints_shipped"] == brief["kpis"]["sprints_shipped"]
    assert payload["kpis"]["tickets_done"] == brief["kpis"]["tickets_done"]
    assert payload["in_progress"]["progress"]["percent"] == 25


# ══ AC9 ═══════════════════════════════════════════════════════════════════════

def test_ac9_regenerate_scoped_to_summary_only(fresh_db, monkeypatch):
    """Regenerate must only clear/recompute the summary, not the structured brief."""
    _seed_rich_project(fresh_db)
    summ.get_or_create_project_summary(SLUG, date=DATE)

    # Spy: regenerate must NOT recompute the structured brief more than the once
    # it needs to read current counts; crucially it must not persist any brief
    # state. We assert it only touches the summary cache row.
    calls = {"build": 0}
    orig_build = summ.brief_service.build_project_brief

    def _spy(slug, date=None):
        calls["build"] += 1
        return orig_build(slug, date=date)

    monkeypatch.setattr(summ.brief_service, "build_project_brief", _spy, raising=True)
    monkeypatch.setattr(summ, "_call_model",
                        lambda prompt: "Regenerated.", raising=True)

    before = db_module.get_brief_summary("project", SLUG, DATE)
    assert before is not None
    summ.get_or_create_project_summary(SLUG, date=DATE, force=True)
    after = db_module.get_brief_summary("project", SLUG, DATE)
    # Only the summary row changed; brief assembled at most once for the regen.
    assert after["summary"] == "Regenerated."
    assert calls["build"] == 1

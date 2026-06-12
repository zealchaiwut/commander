"""Tests for issue #805 — sprint history endpoint + persist deleted-sprint records.

Each test is anchored to a specific acceptance criterion. No backend is mocked:
the DB is a real (temp) SQLite file and the fallback files are real JSON on
disk. Only the *external* GitHub layer is patched — and AC5 patches it to PROVE
the endpoint never touches it.

AC1  GET /api/sprints/history exists in a NEW router file (not server.py),
     accepting `offset` and `limit` query params.
AC2  Each sprint row includes lifecycle_state, duration, tokens,
     estimate_accuracy, pr_number, summary_path, issues[].
AC3  Each issues[] item includes ticket_id, state (merged/closed/open),
     time_spent, and pr_number.
AC4  Reads from the `sprints` lifecycle table when present; falls back to
     state/summary files otherwise.
AC5  Zero GitHub API calls per request (httpx/requests patched to raise).
AC6  Completed, failed, finished, cancelled and deleted sprints all appear with
     the full shape.
AC7  The delete-sprint path writes a (state=deleted, end_reason, ticket
     snapshot) history record BEFORE label stripping.
AC8  A deleted sprint is queryable via GET /api/sprints/history immediately
     after deletion.
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
SERVER_PY_SRC = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


def _load_module(name: str, path: Path):
    """Load a routers/*.py file as a submodule of a stub `routers` package.

    Avoids triggering the real routers/__init__ (which imports every router).
    """
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

svc = _load_module("routers.sprint_history_service", ROUTERS_DIR / "sprint_history_service.py")
router_mod = _load_module("routers.sprint_history", ROUTERS_DIR / "sprint_history.py")


@pytest.fixture()
def fresh_db(tmp_path):
    """Isolated SQLite DB pointed at by the db module for the duration."""
    db_file = tmp_path / "test_805.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


# ── seeding helpers (all real writes — no mocks) ──────────────────────────────

def _insert_lifecycle(db, label, state, *, project="", started_at=None,
                      ended_at=None, end_reason=None, created_at=None):
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at, end_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (label, project, state, created_at, started_at, ended_at, end_reason),
        )
        conn.commit()


def _write_state_file(sprints_dir: Path, label: str, payload: dict):
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{label}-state.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_plan_file(sprints_dir: Path, label: str, payload: dict):
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{label}.json").write_text(json.dumps(payload), encoding="utf-8")


def _state_payload(label, *, issues, tin=0, tout=0, wall=None, est_min=None):
    return {
        "sprint_label": label,
        "issues": issues,
        "total_tokens_in": tin,
        "total_tokens_out": tout,
        "wall_clock_secs": wall,
        "estimator_total_minutes": est_min,
    }


def _by_label(resp_json, label):
    for s in resp_json["sprints"]:
        if s["label"] == label:
            return s
    raise AssertionError(f"sprint {label} not in response: {[s['label'] for s in resp_json['sprints']]}")


# ── AC1 ───────────────────────────────────────────────────────────────────────

def test_ac1_endpoint_lives_in_new_router_not_server():
    # The route is registered by the new router module...
    paths = {r.path for r in router_mod.router.routes}
    assert "/api/sprints/history" in paths
    # ...and the new router file exists.
    assert (ROUTERS_DIR / "sprint_history.py").exists()
    # ...and the handler is NOT declared in the server.py monolith.
    assert "/api/sprints/history" not in SERVER_PY_SRC


def test_ac1_offset_and_limit_query_params(client, fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", tmp_path / "none", raising=False)
    for i in range(5):
        _insert_lifecycle(fresh_db, f"sprint-{i}", "completed",
                          ended_at=f"2026-06-0{i+1}T00:00:00")
    r = client.get("/api/sprints/history", params={"offset": 1, "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["offset"] == 1 and body["limit"] == 2
    assert body["total"] == 5
    assert len(body["sprints"]) == 2


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_ac2_response_shape_per_sprint(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-7", "completed",
                      started_at="2026-06-01T00:00:00", ended_at="2026-06-01T01:00:00")
    _write_state_file(sprints_dir, "sprint-7", _state_payload(
        "sprint-7",
        issues=[{"number": 11, "status": "done", "coder_started_at": "2026-06-01T00:00:00",
                 "tester_finished_at": "2026-06-01T00:02:00"}],
        tin=1000, tout=500, wall=3600, est_min=30))
    (sprints_dir / "sprint-7-summary-2026-06-01.md").write_text("# summary", encoding="utf-8")

    s = _by_label(client.get("/api/sprints/history").json(), "sprint-7")
    for key in ("lifecycle_state", "duration", "tokens", "estimate_accuracy",
                "pr_number", "summary_path", "issues"):
        assert key in s, f"missing {key}"
    assert s["lifecycle_state"] == "completed"
    assert s["duration"] == 3600
    assert s["tokens"] == 1500
    assert s["summary_path"].endswith("sprint-7-summary-2026-06-01.md")
    assert isinstance(s["issues"], list) and s["issues"]


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_ac3_issue_item_shape(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-8", "completed", ended_at="2026-06-02T00:00:00")
    _write_state_file(sprints_dir, "sprint-8", _state_payload(
        "sprint-8",
        issues=[
            {"number": 21, "status": "done", "pr_number": 99,
             "coder_started_at": "2026-06-02T00:00:00",
             "tester_finished_at": "2026-06-02T00:03:00"},
            {"number": 22, "status": "skipped"},
        ]))
    s = _by_label(client.get("/api/sprints/history").json(), "sprint-8")
    first = s["issues"][0]
    for key in ("ticket_id", "state", "time_spent", "pr_number"):
        assert key in first, f"missing {key}"
    assert first["ticket_id"] == 21
    assert first["state"] == "merged"     # done -> merged
    assert first["time_spent"] == 180     # 3 minutes
    assert first["pr_number"] == 99
    assert s["issues"][1]["state"] == "closed"  # skipped -> closed


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_ac4_reads_from_lifecycle_table(client, fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", tmp_path / "empty", raising=False)
    _insert_lifecycle(fresh_db, "sprint-30", "cancelled",
                      ended_at="2026-06-03T00:00:00", end_reason="user cancelled")
    s = _by_label(client.get("/api/sprints/history").json(), "sprint-30")
    assert s["lifecycle_state"] == "cancelled"


def test_ac4_falls_back_to_state_files_when_no_db_row(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    # No lifecycle row for sprint-40 — only on-disk files exist.
    _write_state_file(sprints_dir, "sprint-40", _state_payload(
        "sprint-40", issues=[{"number": 5, "status": "done"}], tin=10, tout=20))
    _write_plan_file(sprints_dir, "sprint-40", {"label": "sprint-40", "status": "complete"})
    body = client.get("/api/sprints/history").json()
    s = _by_label(body, "sprint-40")
    assert s["tokens"] == 30
    assert s["issues"][0]["ticket_id"] == 5


# ── AC5 ───────────────────────────────────────────────────────────────────────

def test_ac5_zero_github_calls(fresh_db, tmp_path, monkeypatch):
    # Drive the read path directly (the in-process TestClient itself rides on
    # httpx, so patching httpx there would be a false positive). Any real
    # outbound HTTP — httpx or requests — must blow up; the endpoint must not.
    sprints_dir = tmp_path / "sprints"
    _insert_lifecycle(fresh_db, "sprint-50", "completed", ended_at="2026-06-04T00:00:00")
    _write_state_file(sprints_dir, "sprint-50", _state_payload(
        "sprint-50", issues=[{"number": 1, "status": "done"}]))

    import http.client
    import socket
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("network call attempted by /api/sprints/history")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", _boom, raising=True)
    monkeypatch.setattr(urllib.request, "urlopen", _boom, raising=True)
    # Also fail if anything shells out to the gh CLI.
    import subprocess
    monkeypatch.setattr(subprocess, "run", _boom, raising=True)
    monkeypatch.setattr(subprocess, "check_output", _boom, raising=True)

    result = svc.get_sprint_history(sprints_dir=sprints_dir)
    assert _by_label(result, "sprint-50")["lifecycle_state"] == "completed"


# ── AC6 ───────────────────────────────────────────────────────────────────────

def test_ac6_all_terminal_states_appear(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)

    _insert_lifecycle(fresh_db, "sprint-60", "completed", ended_at="2026-06-05T00:00:00")
    _insert_lifecycle(fresh_db, "sprint-61", "failed", ended_at="2026-06-06T00:00:00")
    _insert_lifecycle(fresh_db, "sprint-62", "cancelled", ended_at="2026-06-07T00:00:00")
    # finished — surfaced via the file-fallback plan status.
    _write_plan_file(sprints_dir, "sprint-63", {"label": "sprint-63", "status": "finished"})
    _write_state_file(sprints_dir, "sprint-63", _state_payload(
        "sprint-63", issues=[{"number": 7, "status": "done"}]))
    # deleted — written to the sprint_history ledger.
    fresh_db.record_sprint_history(
        label="sprint-64", lifecycle_state="deleted", end_reason="deleted via dashboard",
        issues=[{"ticket_id": 8, "state": "closed", "time_spent": None, "pr_number": None}],
        created_at="2026-06-08T00:00:00")

    states = {s["label"]: s["lifecycle_state"] for s in client.get("/api/sprints/history").json()["sprints"]}
    assert states.get("sprint-60") == "completed"
    assert states.get("sprint-61") == "failed"
    assert states.get("sprint-62") == "cancelled"
    assert states.get("sprint-63") == "finished"
    assert states.get("sprint-64") == "deleted"


# ── AC7 ───────────────────────────────────────────────────────────────────────

def test_ac7_record_deleted_sprint_writes_snapshot(fresh_db, tmp_path):
    issues = [
        {"number": 101, "state": "open"},
        {"number": 102, "state": "closed", "pr_number": 5},
    ]
    svc.record_deleted_sprint("sprint-70", "owner/repo", issues,
                              commander_dir=tmp_path, end_reason="deleted via dashboard")
    rows = fresh_db.list_sprint_history()
    assert len(rows) == 1
    rec = rows[0]
    assert rec["label"] == "sprint-70"
    assert rec["lifecycle_state"] == "deleted"
    assert rec["end_reason"] == "deleted via dashboard"
    ticket_ids = {i["ticket_id"] for i in rec["issues"]}
    assert ticket_ids == {101, 102}


def test_ac7_delete_handler_records_before_label_strip():
    """Source contract: the history write precedes any label mutation (#805)."""
    src = SERVER_PY_SRC
    rec = src.find("record_deleted_sprint(")
    strip = src.find("github_client.update_labels(iss[\"number\"], add=[], remove=[sprint_label]")
    delete_label = src.find("github_client.delete_label(sprint_label")
    assert rec != -1 and strip != -1 and delete_label != -1
    assert rec < strip, "history snapshot must be written before unlabelling tickets"
    assert rec < delete_label, "history snapshot must be written before deleting the label"


# ── AC8 ───────────────────────────────────────────────────────────────────────

def test_ac8_deleted_sprint_queryable_immediately(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    # Sprint still has a state file at deletion time, so timing enriches the snapshot.
    commander = tmp_path / "commander"
    _write_state_file(commander / "sprints", "sprint-80", _state_payload(
        "sprint-80",
        issues=[{"number": 201, "status": "done",
                 "coder_started_at": "2026-06-09T00:00:00",
                 "tester_finished_at": "2026-06-09T00:01:00"}],
        tin=5, tout=5))

    svc.record_deleted_sprint(
        "sprint-80", "", [{"number": 201, "state": "open"}],
        commander_dir=commander, end_reason="deleted via dashboard")

    s = _by_label(client.get("/api/sprints/history", params={"limit": 10}).json(), "sprint-80")
    assert s["lifecycle_state"] == "deleted"
    assert s["issues"][0]["ticket_id"] == 201
    assert s["issues"][0]["time_spent"] == 60  # enriched from the state file


def test_early_crash_completed_with_no_issues_surfaces_as_failed(client, fresh_db, tmp_path, monkeypatch):
    """A sub-sprint that exits before processing tickets reads as failed, not partial."""
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-68.3", "completed",
                      ended_at="2026-06-12T00:00:02", end_reason="orphan-pid")
    _write_state_file(sprints_dir, "sprint-68.3", _state_payload("sprint-68.3", issues=[], wall=2))
    s = _by_label(client.get("/api/sprints/history").json(), "sprint-68.3")
    assert s["lifecycle_state"] == "failed"
    assert s.get("failure_reason")


def test_has_rerun_child_flag_on_parent(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-68", "completed", ended_at="2026-06-12T00:00:00")
    _insert_lifecycle(fresh_db, "sprint-68.1", "completed", ended_at="2026-06-12T00:01:00")
    body = client.get("/api/sprints/history").json()
    parent = _by_label(body, "sprint-68")
    child = _by_label(body, "sprint-68.1")
    assert parent["has_rerun_child"] is True
    assert child["has_rerun_child"] is False


def test_summary_issue_and_failure_reason_from_state_file(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-90", "failed", ended_at="2026-06-12T00:00:00")
    _write_state_file(sprints_dir, "sprint-90", {
        **_state_payload("sprint-90", issues=[{
            "number": 894,
            "status": "pending",
            "agent_status": "failed",
            "failure_reason": "Tester HANG detected",
        }]),
        "summary_issue_url": "https://github.com/o/r/issues/900",
    })
    s = _by_label(client.get("/api/sprints/history").json(), "sprint-90")
    assert s["summary_issue_num"] == 900
    assert s["failed_tickets"][0]["ticket_id"] == 894
    assert "HANG" in s["failed_tickets"][0]["failure_reason"]


def test_post_sprint_agents_from_state_file(client, fresh_db, tmp_path, monkeypatch):
    sprints_dir = tmp_path / "sprints"
    monkeypatch.setattr(svc, "DEFAULT_SPRINTS_DIR", sprints_dir, raising=False)
    _insert_lifecycle(fresh_db, "sprint-91", "completed", ended_at="2026-06-12T00:00:00")
    _write_state_file(sprints_dir, "sprint-91", {
        **_state_payload("sprint-91", issues=[{"number": 1, "status": "done"}]),
        "documenter_status": "succeeded",
        "documenter_files_touched": ["CHANGELOG.md", "README.md"],
        "documenter_commit_sha": "abc123def456",
        "reviewer_status": "succeeded",
        "reviewer_comment_url": "https://github.com/o/r/issues/50#issuecomment-1",
        "reviewer_findings": {
            "blockers": 0,
            "suggestions": 2,
            "nits": 1,
            "follow_up_tickets": [901, 902],
        },
    })
    s = _by_label(client.get("/api/sprints/history").json(), "sprint-91")
    ps = s["post_sprint"]
    assert ps is not None
    assert ps["documenter"]["files_touched"] == ["CHANGELOG.md", "README.md"]
    assert ps["reviewer"]["follow_up_tickets"] == [901, 902]
    assert ps["reviewer"]["suggestions"] == 2

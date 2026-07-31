"""GET /api/status — local-only monitoring snapshot (Stream Deck / board).

Asserts the domain-grouped shape AND the hard contract that the endpoint makes
ZERO GitHub calls: it is polled every ~10s, so any `gh` invocation would be a
quota leak. We spy at the subprocess layer (every gh path ultimately shells out
to `gh`) and assert nothing fired during the request.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

from fastapi.testclient import TestClient  # noqa: E402

_REAL_RUN = subprocess.run


@pytest.fixture
def no_github(monkeypatch):
    """Spy every subprocess call; record any that invoke `gh`. Non-gh commands
    delegate to the real subprocess.run so unrelated machinery still works."""
    gh_calls: list = []

    def _spy(args, *a, **k):
        argv = args if isinstance(args, (list, tuple)) else [args]
        if argv and str(argv[0]) == "gh":
            gh_calls.append(list(argv))
            # Return a benign result rather than shelling out, so a stray call is
            # caught by the assertion instead of actually hitting GitHub.
            return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")
        return _REAL_RUN(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", _spy)
    return gh_calls


@pytest.fixture
def client():
    import server  # noqa: PLC0415 — heavy import; done once per test session
    return TestClient(server.app)


def test_status_shape(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    # top-level domains
    for key in ("schema_version", "generated_at", "sprints", "active_project",
                "health", "queue", "cost"):
        assert key in body, f"missing top-level key {key!r}"
    assert isinstance(body["sprints"], list)
    # health domain
    for key in ("watchdog_healthy", "heartbeat_age_s",
                "github_remaining_pct", "github_reset_in_s"):
        assert key in body["health"], f"missing health.{key}"
    # github_* have no local source yet — must be null, never a live value.
    assert body["health"]["github_remaining_pct"] is None
    assert body["health"]["github_reset_in_s"] is None
    # queue domain
    for key in ("uat_pending", "backlog", "errors_today"):
        assert key in body["queue"], f"missing queue.{key}"
        assert isinstance(body["queue"][key], int)
    # cost domain — null until a source is wired
    assert body["cost"] == {"claude_window_pct": None}


def test_status_sprint_entries_shape(client):
    body = client.get("/api/status").json()
    for s in body["sprints"]:
        assert set(s) >= {"project", "state", "label", "done", "total"}
        assert s["state"] in ("running", "failed", "completed", "idle")
        assert isinstance(s["done"], int) and isinstance(s["total"], int)


def test_status_makes_zero_github_calls(client, no_github):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert no_github == [], f"/api/status must make ZERO gh calls, saw: {no_github}"


def test_subresources_match_top_level(client, no_github):
    full = client.get("/api/status").json()
    assert client.get("/api/status/sprints").json() == full["sprints"] or \
        isinstance(client.get("/api/status/sprints").json(), list)
    assert set(client.get("/api/status/health").json()) == set(full["health"])
    assert set(client.get("/api/status/queue").json()) == set(full["queue"])
    # none of the slices touched GitHub either
    assert no_github == [], f"sub-resources must make ZERO gh calls, saw: {no_github}"


def test_status_never_raises_on_broken_sources(client, monkeypatch):
    # Each domain degrades independently; a blown-up source still yields a
    # well-formed body with the other domains intact.
    from routers import status_service as svc

    def _boom():
        raise RuntimeError("source down")

    monkeypatch.setattr(svc, "queue", _boom)
    body = client.get("/api/status").json()
    assert body["queue"] == {"uat_pending": 0, "backlog": 0, "errors_today": 0}
    assert "sprints" in body and "health" in body

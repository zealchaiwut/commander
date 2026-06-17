"""GitHub CLI login from Global Settings — API + service (issue gh-auth UI)."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

DASHBOARD_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "apps" / "dashboard"


@pytest.fixture
def client():
    import server as srv

    return TestClient(srv.app, raise_server_exceptions=False)


def test_gh_auth_status_endpoint_rechecks_live(client):
    import server as srv

    srv._GH_AUTH_STATUS = {"ok": False, "message": "stale"}
    with patch.object(srv, "_check_gh_auth") as chk:
        def _refresh():
            srv._GH_AUTH_STATUS = {"ok": True, "message": ""}

        chk.side_effect = _refresh
        res = client.get("/api/gh-auth-status")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    chk.assert_called_once()


def test_login_routes_exist():
    mod = importlib.import_module("routers.system")
    paths = {getattr(r, "path", None) for r in mod.router.routes}
    assert "/api/gh-auth/login/start" in paths
    assert "/api/gh-auth/login/status" in paths
    assert "/api/gh-auth/login/input" in paths
    assert "/api/gh-auth/login/cancel" in paths
    assert "/api/gh-auth/login/token" in paths


def test_login_with_token_success(client):
    gas = importlib.import_module("routers.gh_auth_service")
    proc = MagicMock(returncode=0, stdout="Logged in\n", stderr="")
    with patch.object(gas.subprocess, "run", return_value=proc) as run:
        with patch.object(gas, "refresh_server_gh_auth") as refresh:
            res = client.post("/api/gh-auth/login/token", json={"token": "ghp_test"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    run.assert_called_once()
    refresh.assert_called_once()


def test_login_with_token_requires_token(client):
    res = client.post("/api/gh-auth/login/token", json={"token": ""})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["error"] == "token_required"


def test_append_line_parses_device_code():
    gas = importlib.import_module("routers.gh_auth_service")
    job = gas._new_job()
    gas._append_line(job, "! First copy your one-time code: ABCD-1234")
    assert job["device_code"] == "ABCD-1234"


def test_get_login_status_empty():
    gas = importlib.import_module("routers.gh_auth_service")
    with gas._lock:
        gas._job = None
    body = gas.get_login_status()
    assert body["running"] is False
    assert body["lines"] == []

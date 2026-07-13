"""Tests for issue #1864 — static bearer-token auth on write endpoints.

AC coverage:
  AC1 — With COMMANDER_API_TOKEN set: mutating request without/with-wrong Bearer → 401;
         with correct token → normal behavior. GET and SSE unaffected.
  AC2 — With COMMANDER_API_TOKEN unset: all endpoints behave exactly as today.
  AC3 — Dashboard web UI remains fully functional with token set (auth script injected).
  AC4 — hooks/ scripts continue to ingest events (127.0.0.1 exempt from auth).
  AC6 — Token never in 401 body; received value not echoed.

Note: AC5 (CLAUDE.md updated) is a documentation change verified by code review.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_request(method="POST", headers=None, client_host="10.0.0.1"):
    """Build a minimal Starlette Request for unit-testing bearer_auth_gate."""
    from starlette.requests import Request

    header_list = [
        (k.lower().encode(), v.encode())
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": method.upper(),
        "headers": header_list,
        "path": "/api/test",
        "query_string": b"",
        "client": (client_host, 12345),
    }
    return Request(scope)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def with_token(monkeypatch):
    """Set COMMANDER_API_TOKEN for the duration of a test."""
    monkeypatch.setenv("COMMANDER_API_TOKEN", "test-secret-token")


@pytest.fixture()
def no_token(monkeypatch):
    """Ensure COMMANDER_API_TOKEN is unset."""
    monkeypatch.delenv("COMMANDER_API_TOKEN", raising=False)


# ── Unit tests: bearer_auth_gate ──────────────────────────────────────────────

class TestBearerAuthGate:
    """Tests against the pure bearer_auth_gate function (no server needed)."""

    def _gate(self):
        from routers.auth import bearer_auth_gate
        return bearer_auth_gate

    # AC1 — mutating request without header → 401 when token is set
    def test_write_no_auth_header_returns_401(self, with_token):
        gate = self._gate()
        req = _mock_request(method="POST")
        resp = gate(req)
        assert resp is not None
        assert resp.status_code == 401

    # AC1 — mutating request with wrong Bearer → 401
    def test_write_wrong_token_returns_401(self, with_token):
        gate = self._gate()
        req = _mock_request(method="POST", headers={"Authorization": "Bearer wrong-token"})
        resp = gate(req)
        assert resp is not None
        assert resp.status_code == 401

    # AC1 — mutating request with correct Bearer → pass (None)
    def test_write_correct_token_returns_none(self, with_token):
        gate = self._gate()
        req = _mock_request(
            method="POST",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        resp = gate(req)
        assert resp is None  # None = pass through

    # AC1 — GET is never blocked even without header
    def test_get_always_passes(self, with_token):
        gate = self._gate()
        req = _mock_request(method="GET")
        resp = gate(req)
        assert resp is None

    # AC1 — HEAD is never blocked
    def test_head_always_passes(self, with_token):
        gate = self._gate()
        req = _mock_request(method="HEAD")
        resp = gate(req)
        assert resp is None

    # AC1 — OPTIONS is never blocked (preflight)
    def test_options_always_passes(self, with_token):
        gate = self._gate()
        req = _mock_request(method="OPTIONS")
        resp = gate(req)
        assert resp is None

    # AC2 — no token configured: all requests pass through
    def test_no_token_configured_write_passes(self, no_token):
        gate = self._gate()
        req = _mock_request(method="POST")
        resp = gate(req)
        assert resp is None

    # AC2 — no token configured: PUT also passes
    def test_no_token_configured_put_passes(self, no_token):
        gate = self._gate()
        req = _mock_request(method="PUT")
        resp = gate(req)
        assert resp is None

    # AC2 — no token configured: DELETE also passes
    def test_no_token_configured_delete_passes(self, no_token):
        gate = self._gate()
        req = _mock_request(method="DELETE")
        resp = gate(req)
        assert resp is None

    # AC4 — 127.0.0.1 (hooks) is exempt even with token set and no header
    def test_localhost_ipv4_exempt(self, with_token):
        gate = self._gate()
        req = _mock_request(method="POST", client_host="127.0.0.1")
        resp = gate(req)
        assert resp is None

    # AC4 — ::1 (IPv6 loopback) is also exempt
    def test_localhost_ipv6_exempt(self, with_token):
        gate = self._gate()
        req = _mock_request(method="POST", client_host="::1")
        resp = gate(req)
        assert resp is None

    # AC6 — 401 body does NOT echo the submitted (wrong) token value
    def test_401_body_does_not_echo_token_value(self, with_token):
        gate = self._gate()
        submitted = "my-wrong-bearer-value-AAABBBCCC"
        req = _mock_request(
            method="POST",
            headers={"Authorization": f"Bearer {submitted}"},
        )
        resp = gate(req)
        assert resp is not None
        import json
        body = json.loads(resp.body)
        body_str = json.dumps(body)
        assert submitted not in body_str

    # AC6 — 401 body does NOT contain the actual configured token
    def test_401_body_does_not_contain_configured_token(self, with_token):
        gate = self._gate()
        req = _mock_request(method="POST")
        resp = gate(req)
        import json
        body_str = json.dumps(json.loads(resp.body))
        assert "test-secret-token" not in body_str

    # AC1 — PATCH is also a mutating method → blocked without token
    def test_patch_blocked_without_token_header(self, with_token):
        gate = self._gate()
        req = _mock_request(method="PATCH")
        resp = gate(req)
        assert resp is not None
        assert resp.status_code == 401

    # AC1 — DELETE is also a mutating method → blocked without token
    def test_delete_blocked_without_token_header(self, with_token):
        gate = self._gate()
        req = _mock_request(method="DELETE")
        resp = gate(req)
        assert resp is not None
        assert resp.status_code == 401


# ── Unit tests: inject_auth_script ────────────────────────────────────────────

class TestInjectAuthScript:
    """Tests for the HTML injection that enables browser UI writes."""

    def _inject(self):
        from routers.auth import inject_auth_script
        return inject_auth_script

    # AC3 — HTML page gets script injected when token is set
    def test_injects_script_when_token_set(self, with_token):
        inject = self._inject()
        html = "<html><head><title>T</title></head><body></body></html>"
        result = inject(html)
        assert "<script" in result
        # The injected script should contain the actual token
        assert "test-secret-token" in result

    # AC3 — HTML page gets meta tag injected when token set
    def test_injects_meta_tag_when_token_set(self, with_token):
        inject = self._inject()
        html = "<html><head></head><body></body></html>"
        result = inject(html)
        # Either a meta tag or inline script with the token
        assert "test-secret-token" in result

    # AC3 — HTML unchanged when no token configured
    def test_no_injection_when_no_token(self, no_token):
        inject = self._inject()
        html = "<html><head><title>T</title></head><body></body></html>"
        result = inject(html)
        assert result == html

    # AC3 — injected script patches fetch for non-GET methods
    def test_injected_script_patches_fetch(self, with_token):
        inject = self._inject()
        html = "<html><head></head><body></body></html>"
        result = inject(html)
        # Script should patch window.fetch or use Authorization header
        assert "Authorization" in result or "fetch" in result


# ── Integration: COMMANDER_API_TOKEN env + test client ────────────────────────

@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """Full server TestClient with a temp DB and COMMANDER_API_TOKEN set."""
    monkeypatch.setenv("COMMANDER_API_TOKEN", "integration-secret")
    monkeypatch.setenv("COMMANDER_DISABLE_NEON", "1")
    monkeypatch.setenv("COMMANDER_DISABLE_AUTO_RECONCILE", "1")

    for mod in list(sys.modules.keys()):
        if "server" in mod or "startup" in mod:
            del sys.modules[mod]

    if "db" in sys.modules:
        del sys.modules["db"]

    import db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "auth_test.db")

    import server  # noqa: F401
    from fastapi.testclient import TestClient
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture()
def app_client_no_token(tmp_path, monkeypatch):
    """Full server TestClient with no COMMANDER_API_TOKEN configured."""
    monkeypatch.delenv("COMMANDER_API_TOKEN", raising=False)
    monkeypatch.setenv("COMMANDER_DISABLE_NEON", "1")
    monkeypatch.setenv("COMMANDER_DISABLE_AUTO_RECONCILE", "1")

    for mod in list(sys.modules.keys()):
        if "server" in mod or "startup" in mod:
            del sys.modules[mod]

    if "db" in sys.modules:
        del sys.modules["db"]

    import db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "auth_notoken.db")

    import server  # noqa: F401
    from fastapi.testclient import TestClient
    return TestClient(server.app, raise_server_exceptions=False)


class TestBearerAuthIntegration:
    """Integration tests: middleware in a live server stack."""

    # AC1 — GET /api/environment passes without any token (read-only)
    def test_get_always_open(self, app_client):
        resp = app_client.get("/api/environment")
        assert resp.status_code != 401

    # AC2 — without COMMANDER_API_TOKEN, GET /api/environment still works
    def test_no_token_get_passes(self, app_client_no_token):
        resp = app_client_no_token.get("/api/environment")
        assert resp.status_code != 401

    # AC2 — without COMMANDER_API_TOKEN, a write path also passes (no auth at all)
    def test_no_token_write_not_blocked(self, app_client_no_token):
        # POST /api/agent-event is the hook ingestion path
        resp = app_client_no_token.post(
            "/api/agent-event",
            json={"session_id": "s1", "event_type": "start", "working_dir": "/tmp"},
        )
        # Should not be 401 (may be 422/400/200 depending on payload validation)
        assert resp.status_code != 401

    # AC1 — with token set, a write from non-localhost without header → 401
    # TestClient default host is "testclient" (not 127.0.0.1) so middleware fires
    def test_write_without_header_returns_401(self, app_client):
        resp = app_client.post("/api/agent-event", json={})
        assert resp.status_code == 401

    # AC1 — with correct Bearer, the write is not blocked by auth middleware
    def test_write_with_correct_bearer_not_401(self, app_client):
        resp = app_client.post(
            "/api/agent-event",
            json={"session_id": "s1", "event_type": "start", "working_dir": "/tmp"},
            headers={"Authorization": "Bearer integration-secret"},
        )
        assert resp.status_code != 401

    # AC6 — 401 response body does not echo the submitted token
    def test_401_body_clean(self, app_client):
        submitted = "Bearer totally-wrong-XXXXXX"
        resp = app_client.post(
            "/api/agent-event",
            json={},
            headers={"Authorization": submitted},
        )
        assert resp.status_code == 401
        body = resp.text
        assert "totally-wrong-XXXXXX" not in body
        assert "integration-secret" not in body

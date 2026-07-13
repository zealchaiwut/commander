"""Tests for issue #1895: bearer token must never be emitted into served HTML.

The original #1864 approach inlined the raw token via repr(token) into every
page's <script>, but page routes are open GETs — so any unauthenticated client
could curl a page and read the secret. These tests lock in the fix:

- inject_auth_script never contains the token value, even when it is set.
- The script reads the token from localStorage at runtime instead.
- bearer_auth_gate still gates writes, uses a constant-time compare, and keeps
  the GET/SSE-open + localhost-exempt contract.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR), str(_DASHBOARD_DIR / "routers")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _auth(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("COMMANDER_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("COMMANDER_API_TOKEN", token)
    mod = importlib.import_module("routers.auth") if "routers.auth" in sys.modules \
        else importlib.import_module("auth")
    return importlib.reload(mod)


SECRET = "s3cr3t-TOKEN-value-do-not-leak-9xQ"


class TestTokenNeverInHtml:
    def test_injected_script_has_no_token_when_set(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        html = "<html><head></head><body>hi</body></html>"
        out = auth.inject_auth_script(html)
        assert SECRET not in out, "token value leaked into served HTML"
        assert repr(SECRET) not in out

    def test_injected_script_reads_from_localstorage(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        out = auth.inject_auth_script("<head></head>")
        assert "localStorage" in out
        assert "getItem" in out
        # non-GET methods get the Authorization header attached client-side
        assert "Authorization" in out

    def test_no_token_still_no_secret_and_valid_html(self, monkeypatch):
        auth = _auth(monkeypatch, None)
        html = "<html><head></head><body/></html>"
        out = auth.inject_auth_script(html)
        assert SECRET not in out
        assert "<body" in out  # untouched body


class TestGateContract:
    def _gate(self, auth, method, host, header=None):
        class _Client:
            def __init__(self, h): self.host = h
        class _Req:
            def __init__(self):
                self.method = method
                self.client = _Client(host)
                self.headers = {"Authorization": header} if header else {}
        return auth.bearer_auth_gate(_Req())

    def test_write_without_token_header_401(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        resp = self._gate(auth, "POST", "100.64.0.9")
        assert resp is not None and resp.status_code == 401

    def test_write_with_correct_token_passes(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        resp = self._gate(auth, "POST", "100.64.0.9", header=f"Bearer {SECRET}")
        assert resp is None

    def test_write_with_wrong_token_401(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        resp = self._gate(auth, "POST", "100.64.0.9", header="Bearer nope")
        assert resp is not None and resp.status_code == 401

    def test_get_always_open(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        assert self._gate(auth, "GET", "100.64.0.9") is None

    def test_localhost_write_exempt(self, monkeypatch):
        auth = _auth(monkeypatch, SECRET)
        assert self._gate(auth, "POST", "127.0.0.1") is None
        assert self._gate(auth, "POST", "::1") is None

    def test_unset_token_passes_everything(self, monkeypatch):
        auth = _auth(monkeypatch, None)
        assert self._gate(auth, "POST", "100.64.0.9") is None

    def test_uses_constant_time_compare(self, monkeypatch):
        # Guard against a regression back to `==`: assert the module calls
        # hmac.compare_digest during a gate check.
        auth = _auth(monkeypatch, SECRET)
        import hmac
        calls = {"n": 0}
        real = hmac.compare_digest
        monkeypatch.setattr(hmac, "compare_digest",
                            lambda a, b: (calls.__setitem__("n", calls["n"] + 1), real(a, b))[1])
        self._gate(auth, "POST", "100.64.0.9", header=f"Bearer {SECRET}")
        assert calls["n"] >= 1, "bearer comparison must use hmac.compare_digest"


def test_localhost_hosts_has_no_dead_hostname_entry(monkeypatch):
    # issue #1893: request.client.host is always an IP, so "localhost" was dead.
    auth = _auth(monkeypatch, SECRET)
    assert "localhost" not in auth._LOCALHOST_HOSTS
    assert "127.0.0.1" in auth._LOCALHOST_HOSTS

"""Tests for issue #1896: SSRF guard on sprint-webhook callback_url.

Behavioral: exercise the real screen + fire path with DNS resolution stubbed,
and assert that internal/loopback/metadata targets are rejected and never
connected to, while a genuine public host is allowed. Also assert redirects
are not followed.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
# NOTE: do NOT put apps/dashboard/routers on sys.path — a bare `import
# calibration` would then resolve to routers/calibration.py and shadow
# services/sprint_manager/calibration.py, breaking unrelated tests in the
# same run. Import via the `routers.` package instead.
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib
wh = importlib.import_module("routers.sprint_webhook_service")


def _stub_dns(monkeypatch, mapping):
    """Patch getaddrinfo so host -> ip is deterministic (no real network)."""
    def fake(host, port, *a, **k):
        ip = mapping.get(host)
        if ip is None:
            raise socket.gaierror(f"no stub for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]
    monkeypatch.setattr(wh.socket, "getaddrinfo", fake)


BLOCKED = [
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),  # cloud metadata
    ("http://127.0.0.1:8000/x", "127.0.0.1"),                          # loopback
    ("http://internal.local/hook", "10.1.2.3"),                       # RFC1918
    ("http://db.svc/hook", "192.168.5.5"),                            # RFC1918
    ("http://x.corp/hook", "172.16.9.9"),                             # RFC1918
]


class TestScreenBlocksInternal:
    @pytest.mark.parametrize("url,ip", BLOCKED)
    def test_blocked(self, monkeypatch, url, ip):
        _stub_dns(monkeypatch, {wh.urllib.parse.urlparse(url).hostname: ip})
        reason = wh.screen_callback_url(url)
        assert reason is not None, f"{url} ({ip}) should be blocked"

    def test_public_allowed(self, monkeypatch):
        _stub_dns(monkeypatch, {"hooks.example.com": "93.184.216.34"})
        assert wh.screen_callback_url("https://hooks.example.com/cb") is None

    def test_non_http_scheme_blocked(self, monkeypatch):
        assert wh.screen_callback_url("file:///etc/passwd") is not None
        assert wh.screen_callback_url("gopher://x/1") is not None

    def test_mixed_resolution_rejected(self, monkeypatch):
        # A host that resolves to BOTH a public and an internal IP must be rejected.
        def fake(host, port, *a, **k):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80)),
            ]
        monkeypatch.setattr(wh.socket, "getaddrinfo", fake)
        assert wh.screen_callback_url("http://rebind.example/cb") is not None


class TestFireEnforces:
    def test_fire_blocked_never_connects(self, monkeypatch):
        _stub_dns(monkeypatch, {"169.254.169.254": "169.254.169.254"})
        opened = {"n": 0}
        monkeypatch.setattr(
            wh._NO_REDIRECT_OPENER, "open",
            lambda *a, **k: opened.__setitem__("n", opened["n"] + 1),
        )
        ok = wh.fire_sprint_webhook("http://169.254.169.254/latest/meta-data/", {"x": 1})
        assert ok is False
        assert opened["n"] == 0, "must not open a connection to a blocked target"

    def test_fire_public_connects(self, monkeypatch):
        _stub_dns(monkeypatch, {"hooks.example.com": "93.184.216.34"})
        class _Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        opened = {"n": 0}
        def _open(*a, **k):
            opened["n"] += 1
            return _Resp()
        monkeypatch.setattr(wh._NO_REDIRECT_OPENER, "open", _open)
        ok = wh.fire_sprint_webhook("https://hooks.example.com/cb", {"x": 1})
        assert ok is True
        assert opened["n"] == 1

    def test_opener_does_not_follow_redirects(self):
        # The opener is built with the no-redirect handler.
        handlers = wh._NO_REDIRECT_OPENER.handlers
        assert any(isinstance(h, wh._NoRedirect) for h in handlers)


class TestValidateCallbackUrl:
    def test_validate_rejects_internal(self, monkeypatch):
        _stub_dns(monkeypatch, {"127.0.0.1": "127.0.0.1"})
        assert wh.validate_callback_url("http://127.0.0.1:9/hook") is False

    def test_validate_accepts_public(self, monkeypatch):
        _stub_dns(monkeypatch, {"hooks.example.com": "93.184.216.34"})
        assert wh.validate_callback_url("https://hooks.example.com/cb") is True

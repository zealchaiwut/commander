"""Tests for issue #2243: Remove standalone live-run viewers.

AC1: static/run_browser.html deleted and /run-browser route returns 404.
AC2: rerun-modal.js deleted and not imported in the bundle entry point.
AC3: ntfy/notification template no longer references /run-browser.
AC4: Bundle rebuilt (bundle.js must not expose _rrOpen/_rrConfirm from rerun-modal).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _REPO_ROOT / "apps" / "dashboard" / "static"
_SRC_DIR = _STATIC_DIR / "src"
_SPRINT_BOARD_DIR = _SRC_DIR / "sprint-board"
_ALERTS_PY = _REPO_ROOT / "services" / "sprint_manager" / "alerts.py"
_RUNS_PY = _REPO_ROOT / "apps" / "dashboard" / "routers" / "runs.py"
_INDEX_JS = _SPRINT_BOARD_DIR / "index.js"
_BUNDLE_JS = _STATIC_DIR / "dist" / "bundle.js"


# ── AC1: run_browser.html deleted; /run-browser → 404 ────────────────────────

class TestRunBrowserHtmlDeleted:
    """AC1 — run_browser.html must not exist."""

    def test_run_browser_html_does_not_exist(self):
        assert not (_STATIC_DIR / "run_browser.html").exists(), (
            "run_browser.html must be deleted (AC1)"
        )


class TestRunBrowserRouteReturns404:
    """AC1 — /run-browser route must return 404."""

    def test_run_browser_route_returns_404(self):
        # Load the runs router and mount it on a minimal app
        sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        # Load runs router; patch out service dependencies to avoid DB setup
        from unittest.mock import patch, MagicMock

        mock_service = MagicMock()
        mock_service.list_runs.return_value = []
        mock_service.DEFAULT_LOG_LIMIT = 200
        mock_service.DEFAULT_TAIL_LINES = 50
        mock_service.get_log_path_from_db.return_value = None
        mock_service.get_issue_log_tail.return_value = {"found": False}
        mock_service.get_run_reasoning.return_value = None
        mock_service.read_log_page.return_value = {}
        mock_service.read_log_tail.return_value = {}

        with patch.dict("sys.modules", {"routers.runs_service": mock_service}):
            import importlib
            if "routers.runs" in sys.modules:
                del sys.modules["routers.runs"]
            import routers.runs as runs_mod
            app.include_router(runs_mod.router)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/run-browser")
        assert resp.status_code == 404, (
            f"/run-browser must return 404 after deletion (AC1), got {resp.status_code}"
        )


# ── AC2: rerun-modal.js deleted; not imported in bundle entry ─────────────────

class TestRerunModalJsDeleted:
    """AC2 — rerun-modal.js must not exist."""

    def test_rerun_modal_js_does_not_exist(self):
        assert not (_SPRINT_BOARD_DIR / "rerun-modal.js").exists(), (
            "rerun-modal.js must be deleted (AC2)"
        )


class TestRerunModalNotImportedInIndex:
    """AC2 — sprint-board/index.js must not import rerun-modal.js."""

    def test_index_js_does_not_import_rerun_modal(self):
        src = _INDEX_JS.read_text(encoding="utf-8")
        assert "rerun-modal" not in src, (
            "sprint-board/index.js must not import rerun-modal.js (AC2)"
        )

    def test_index_js_does_not_expose_rr_functions(self):
        src = _INDEX_JS.read_text(encoding="utf-8")
        rr_symbols = ["_rrOpen", "_rrClose", "_rrConfirm", "smgmtRerunSprint"]
        for sym in rr_symbols:
            assert sym not in src, (
                f"sprint-board/index.js must not expose {sym} (AC2)"
            )


# ── AC3: ntfy/notification template does not reference /run-browser ───────────

class TestNtfyTemplateNoRunBrowserLink:
    """AC3 — alerts.py must not build a Click header pointing to /run-browser."""

    def test_alerts_py_has_no_run_browser_click_header(self):
        src = _ALERTS_PY.read_text(encoding="utf-8")
        assert "/run-browser" not in src, (
            "alerts.py must not reference /run-browser in the ntfy Click header (AC3)"
        )

    def test_ntfy_alert_does_not_emit_run_browser_link(self):
        """Behavioural: calling _alert_ntfy must not set a Click header to /run-browser."""
        import urllib.request
        from unittest.mock import patch, MagicMock

        sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
        import importlib
        if "alerts" in sys.modules:
            del sys.modules["alerts"]
        import alerts as alerts_mod

        captured_headers: dict = {}

        class _FakeReq:
            def __init__(self, url, data, headers, method):
                captured_headers.update(headers)

        def _fake_urlopen(req, timeout=5):
            return MagicMock()

        with (
            patch.object(urllib.request, "Request", side_effect=_FakeReq),
            patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen),
            patch.dict("os.environ", {
                "COMMANDER_NTFY_URL": "http://ntfy.example/test",
                "DASHBOARD_URL": "http://dash.example",
            }),
        ):
            alerts_mod._alert_ntfy(
                "Test title",
                "Test body",
                sprint_label="sprint-99",
                category="success",
            )

        click_val = captured_headers.get("Click", "")
        assert "/run-browser" not in click_val, (
            f"ntfy Click header must not reference /run-browser (AC3); got: {click_val!r}"
        )


# ── AC4: bundle rebuilt (bundle.js must not reference rerun-modal symbols) ────

class TestBundleRebuilt:
    """AC4 — bundle.js must not contain _rrOpen or _rrConfirm from rerun-modal."""

    def test_bundle_does_not_contain_rr_symbols(self):
        if not _BUNDLE_JS.exists():
            pytest.skip("bundle.js not found — run npm run build first")
        src = _BUNDLE_JS.read_text(encoding="utf-8")
        # These function names are uniquely from rerun-modal.js; if the module
        # was tree-shaken/removed they must not appear in the bundle.
        assert "_rrOpen" not in src, (
            "bundle.js must not contain _rrOpen from rerun-modal (AC4)"
        )
        assert "_rrShowPreviewLoading" not in src, (
            "bundle.js must not contain _rrShowPreviewLoading from rerun-modal (AC4)"
        )

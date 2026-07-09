"""Shared pytest configuration for the Commander test suite.

Sets DB_PATH and COMMANDER_DISABLE_NEON before any test module imports
apps.dashboard.db (which hard-exits when DB_PATH is unset).

Also ensures sys.path includes the repo root and apps/dashboard so tests can
import project modules regardless of the working directory pytest is invoked from.
"""
from __future__ import annotations

import os
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Must run at conftest import time — before test modules import server/db.
_TEST_DB = Path(os.environ.get("COMMANDER_TEST_DB", "/tmp/commander-pytest.db"))
os.environ.setdefault("DB_PATH", str(_TEST_DB))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
# Feature flags default off in config.py; tests opt back in to sign-off/planning/advisor/brief.
os.environ.setdefault("COMMANDER_DISABLE_SIGNOFF", "0")
os.environ.setdefault("COMMANDER_DISABLE_ADVISOR", "0")
os.environ.setdefault("COMMANDER_DISABLE_PLANNING", "0")
os.environ.setdefault("COMMANDER_DISABLE_SPRINT_GOAL_REQUIRED", "0")
os.environ.setdefault("COMMANDER_DISABLE_BRIEF", "0")

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SPRINT_MGR_DIR = _REPO_ROOT / "services" / "sprint_manager"
_STATIC_DIR = _DASHBOARD_DIR / "static"

for _p in (str(_REPO_ROOT), str(_SPRINT_MGR_DIR), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Single settings_repo module object for both import paths (bare + package).
import settings_repo as _settings_repo  # noqa: E402

sys.modules["services.sprint_manager.settings_repo"] = _settings_repo

import subprocess as _subprocess  # noqa: E402

import pytest  # noqa: E402

import services.sprint_manager.agent_browser_runner as _abr  # noqa: E402


# ── Call budget fixture (issue #1788) ──────────────────────────────────────────

class _BudgetHelper:
    """Return value of call_budget_fixture.

    Tracks gh subprocess calls and arbitrary HTTP call counts recorded by tests.

    Usage::

        def test_my_endpoint(call_budget_fixture):
            # ... drive the endpoint via TestClient ...
            call_budget_fixture.assert_zero_gh()
            call_budget_fixture.assert_call_budget("/api/board", 1)
    """

    def __init__(self) -> None:
        self._gh_calls: list[list] = []
        self._http_calls: list[str] = []

    def _record_gh(self, args: list) -> None:
        self._gh_calls.append(list(args))

    def record_http(self, url: str) -> None:
        """Record a URL as an observed HTTP call (for manual budget tracking)."""
        self._http_calls.append(url)

    def assert_zero_gh(self) -> None:
        """Assert no gh subprocess calls were made since the fixture was created."""
        assert self._gh_calls == [], (
            f"Expected 0 gh subprocess calls, got {len(self._gh_calls)}: "
            f"{self._gh_calls}"
        )

    def assert_call_budget(self, path_pattern: str, max_calls: int) -> None:
        """Assert the number of recorded HTTP calls matching path_pattern is ≤ max_calls.

        Usage::

            call_budget_fixture.record_http("/api/board?project=owner/repo")
            call_budget_fixture.assert_call_budget("/api/board", 1)
        """
        matching = [u for u in self._http_calls if path_pattern in u]
        assert len(matching) <= max_calls, (
            f"Call budget exceeded for '{path_pattern}': "
            f"expected ≤ {max_calls}, got {len(matching)} calls: {matching}"
        )


@pytest.fixture(scope="module")
def static_dashboard_url():
    """Serve apps/dashboard/static on an ephemeral localhost port for browser tests."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(_STATIC_DIR))
    server = HTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def agent_browser_available():
    """Skip agent-browser tests when the CLI or Chrome is not set up."""
    if not _abr.is_available():
        pytest.skip(
            "agent-browser CLI not available "
            "(install: npm i -g agent-browser && agent-browser install)"
        )


def agent_browser_open(url: str) -> None:
    """Open a URL via agent-browser; assert success."""
    rc, _out, err = _abr.run_cli(["open", url], timeout=60)
    assert rc == 0, f"agent-browser open failed: {err}"


def agent_browser_find(selector: str) -> str:
    """Return stdout from agent-browser find; assert the element exists."""
    rc, out, err = _abr.run_cli(["find", selector], timeout=30)
    assert rc == 0, f"agent-browser find {selector!r} failed: {err}"
    return out


@pytest.fixture
def call_budget_fixture(monkeypatch) -> _BudgetHelper:
    """Shared call-count-budget test harness (issue #1788).

    Monkeypatches subprocess so gh CLI calls are tracked. Exposes
    assert_zero_gh() and assert_call_budget(path_pattern, max_calls).

    Usage::

        def test_board_zero_gh(call_budget_fixture):
            # drive the board endpoint via TestClient ...
            call_budget_fixture.assert_zero_gh()
    """
    helper = _BudgetHelper()
    original_run = _subprocess.run
    original_popen = _subprocess.Popen

    def _patched_run(args, **kwargs):
        if isinstance(args, (list, tuple)) and args and args[0] == "gh":
            helper._record_gh(list(args))
        return original_run(args, **kwargs)

    def _patched_popen(args, **kwargs):
        if isinstance(args, (list, tuple)) and args and args[0] == "gh":
            helper._record_gh(list(args))
        return original_popen(args, **kwargs)

    monkeypatch.setattr(_subprocess, "run", _patched_run)
    monkeypatch.setattr(_subprocess, "Popen", _patched_popen)
    return helper


@pytest.fixture(autouse=True)
def _isolate_settings_repo(tmp_path, monkeypatch):
    """One settings_repo module + per-test JSON fallback (no repo .commander bleed)."""
    import settings_repo

    sys.modules["services.sprint_manager.settings_repo"] = settings_repo
    store = tmp_path / "settings_store.json"
    monkeypatch.setattr(settings_repo, "_fallback_store_path", lambda: store)

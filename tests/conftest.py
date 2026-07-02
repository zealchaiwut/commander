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

import pytest  # noqa: E402

import services.sprint_manager.agent_browser_runner as _abr  # noqa: E402


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
        pytest.skip("agent-browser CLI not available (install: npm i -g agent-browser && agent-browser install)")


def agent_browser_open(url: str) -> None:
    """Open a URL via agent-browser; assert success."""
    rc, _out, err = _abr.run_cli(["open", url], timeout=60)
    assert rc == 0, f"agent-browser open failed: {err}"


def agent_browser_find(selector: str) -> str:
    """Return stdout from agent-browser find; assert the element exists."""
    rc, out, err = _abr.run_cli(["find", selector], timeout=30)
    assert rc == 0, f"agent-browser find {selector!r} failed: {err}"
    return out


@pytest.fixture(autouse=True)
def _isolate_settings_repo(tmp_path, monkeypatch):
    """One settings_repo module + per-test JSON fallback (no repo .commander bleed)."""
    import settings_repo

    sys.modules["services.sprint_manager.settings_repo"] = settings_repo
    store = tmp_path / "settings_store.json"
    monkeypatch.setattr(settings_repo, "_fallback_store_path", lambda: store)

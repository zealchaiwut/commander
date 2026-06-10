"""Tests for issue #785: cross-run log search and filter for the Run Browser.

Each test function is anchored to a specific acceptance criterion.
All tests use temp dirs + mocks — no live DB or server required.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


def _load_module(name: str, path: Path):
    """Load a Python file as a module without triggering package __init__."""
    # Ensure a stub routers package exists so relative imports resolve
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _get_svc():
    """Import log_search_service, loading it if necessary."""
    if "routers.log_search_service" not in sys.modules:
        _load_module("routers.log_search_service", ROUTERS_DIR / "log_search_service.py")
    return sys.modules["routers.log_search_service"]


def _get_ls():
    """Import log_search router module, loading it if necessary."""
    _get_svc()  # ensure service is loaded first
    if "routers.log_search" not in sys.modules:
        _load_module("routers.log_search", ROUTERS_DIR / "log_search.py")
    return sys.modules["routers.log_search"]

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_log_dir(tmp_path: Path, issue_num: int = 101, sprint: str = "sprint-1") -> Path:
    """Create a minimal log directory with one issue log and one structured log."""
    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True)
    issue_log = log_dir / f"sprint-issue-{issue_num}.log"
    issue_log.write_text(
        "Starting coder agent\nfeat: implement thing (issue #101)\nFAIL: assertion error\n",
        encoding="utf-8",
    )
    structured = log_dir / "structured-2026-06-01.log"
    structured.write_text(
        json.dumps({"ts": "2026-06-01T10:00:00+00:00", "level": "INFO",
                    "sprint_label": sprint, "issue_num": issue_num,
                    "agent_role": "coder", "message": "coder dispatched"}) + "\n" +
        json.dumps({"ts": "2026-06-01T10:05:00+00:00", "level": "FAIL",
                    "sprint_label": sprint, "issue_num": issue_num,
                    "agent_role": "tester", "message": "FAIL: assertion error"}) + "\n",
        encoding="utf-8",
    )
    return log_dir


def _make_state_json(tmp_path: Path, issue_num: int = 101,
                     sprint: str = "sprint-1", project: str = "testorg/testrepo") -> Path:
    """Write a minimal sprint state JSON."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "project": project,
        "sprint_label": sprint,
        "start_timestamp": "2026-06-01T10:00:00Z",
        "wall_clock_secs": 300.0,
        "issues": [
            {"number": issue_num, "title": "Test issue", "status": "done",
             "agent_status": None, "failure_reason": None},
        ],
    }
    path = sprints_dir / f"{sprint}-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def _build_app() -> FastAPI:
    ls_mod = _get_ls()
    app = FastAPI()
    app.include_router(ls_mod.router)
    return app


# ─────────────────────────────────────────────────────────────────────────────
# AC1: GET /api/logs/search accepts all required query params
# ─────────────────────────────────────────────────────────────────────────────

def test_search_endpoint_accepts_all_query_params(tmp_path):
    """AC1: endpoint exists at /api/logs/search and accepts all specified params."""
    _make_log_dir(tmp_path)
    ls_mod = _get_ls()
    app = _build_app()

    def _fake_search(**kwargs):
        return {"matches": [], "total": 0, "capped": False, "timed_out": False, "query_ms": 1}

    # Patch on the router module (where search_logs was imported by-value)
    with patch.object(ls_mod, "search_logs", side_effect=_fake_search):
        client = TestClient(app)
        resp = client.get(
            "/api/logs/search",
            params={
                "issue": "101",
                "agent": "coder",
                "event_type": "coder_dispatched",
                "level": "INFO",
                "time_range": "24h",
                "sprint": "sprint-1",
                "q": "assertion error",
                "project": "testorg/testrepo",
            },
        )
    assert resp.status_code == 200, resp.text


# ─────────────────────────────────────────────────────────────────────────────
# AC2: Response contains sprint, issue, agent, line_offset for each match
# ─────────────────────────────────────────────────────────────────────────────

def test_search_response_shape(tmp_path):
    """AC2: each match record has sprint, issue, agent, line_offset."""
    ls_mod = _get_ls()

    fake_match = {
        "sprint": "sprint-1",
        "issue": 101,
        "agent": "coder",
        "line_offset": 2,
        "text": "FAIL: assertion error",
        "file": "sprint-issue-101.log",
    }

    def _fake_search(**kwargs):
        return {"matches": [fake_match], "total": 1, "capped": False,
                "timed_out": False, "query_ms": 5}

    with patch.object(ls_mod, "search_logs", side_effect=_fake_search):
        app = _build_app()
        client = TestClient(app)
        resp = client.get("/api/logs/search", params={"q": "error"})

    assert resp.status_code == 200
    data = resp.json()
    assert "matches" in data
    assert len(data["matches"]) == 1
    m = data["matches"][0]
    assert "sprint" in m
    assert "issue" in m
    assert "agent" in m
    assert "line_offset" in m


# ─────────────────────────────────────────────────────────────────────────────
# AC3: Results capped at 500 by default; cap is configurable server-side
# ─────────────────────────────────────────────────────────────────────────────

def test_search_results_capped_at_500(tmp_path):
    """AC3: when >500 matches exist, only 500 returned and capped=True."""
    svc = _get_svc()

    # Build a log file with 600 matching lines
    log_dir = _make_log_dir(tmp_path)
    big_log = log_dir / "sprint-issue-200.log"
    big_log.write_text("\n".join(f"INFO line {i}" for i in range(600)) + "\n", encoding="utf-8")

    _make_state_json(tmp_path, issue_num=200, sprint="sprint-2")

    # Call service directly with a real files-based search (mocking rg)
    rg_output = "\n".join(
        f"{big_log}:{i+1}:INFO line {i}" for i in range(600)
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=rg_output,
            returncode=0,
        )
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            result = svc.search_logs(q="INFO", limit=500, timeout_secs=10.0)

    assert len(result["matches"]) <= 500
    assert result["capped"] is True
    assert result["total"] >= 500


def test_search_default_limit_is_500():
    """AC3: the default limit in search_logs is 500."""
    import inspect
    svc = _get_svc()
    sig = inspect.signature(svc.search_logs)
    assert sig.parameters["limit"].default == 500


# ─────────────────────────────────────────────────────────────────────────────
# AC4: Search times out gracefully — no hung request
# ─────────────────────────────────────────────────────────────────────────────

def test_search_timeout_graceful(tmp_path):
    """AC4: when rg subprocess hangs, service returns timed_out=True within timeout."""
    svc = _get_svc()

    def _hanging_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 5))

    log_dir = _make_log_dir(tmp_path)

    with patch("subprocess.run", side_effect=_hanging_run):
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            t0 = time.monotonic()
            result = svc.search_logs(q="anything", timeout_secs=0.1)
            elapsed = time.monotonic() - t0

    assert result["timed_out"] is True
    assert elapsed < 5.0, "timeout must not block for longer than the timeout value"


# ─────────────────────────────────────────────────────────────────────────────
# AC5: Issue-number search returns hits < 2s on a 100 MB log corpus (mocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_search_issue_prefilter_limits_scope(tmp_path):
    """AC5/AC7: when issue= is given, only sprint-issue-N.log is passed to rg.

    This validates that pre-filtering prevents scanning the entire corpus.
    """
    svc = _get_svc()

    log_dir = _make_log_dir(tmp_path, issue_num=101)
    issue_log = log_dir / "sprint-issue-101.log"
    other_log = log_dir / "sprint-issue-999.log"
    other_log.write_text("unrelated content\n", encoding="utf-8")

    captured_files: list[list[str]] = []

    def _capture_run(cmd, **kwargs):
        captured_files.append([a for a in cmd if a.endswith(".log")])
        return MagicMock(stdout=f"{issue_log}:3:FAIL: assertion error", returncode=0)

    with patch("subprocess.run", side_effect=_capture_run):
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            svc.search_logs(issue=101, q="FAIL")

    # Only the issue-specific file should be passed to rg
    all_searched = [f for files in captured_files for f in files]
    assert any("sprint-issue-101.log" in f for f in all_searched)
    assert not any("sprint-issue-999.log" in f for f in all_searched)


# ─────────────────────────────────────────────────────────────────────────────
# AC7: Backend uses ripgrep subprocess
# ─────────────────────────────────────────────────────────────────────────────

def test_search_uses_ripgrep_subprocess(tmp_path):
    """AC7: search_logs calls subprocess.run with rg (ripgrep)."""
    svc = _get_svc()

    log_dir = _make_log_dir(tmp_path)
    rg_calls: list[list] = []

    def _mock_run(cmd, **kwargs):
        rg_calls.append(list(cmd))
        return MagicMock(stdout="", returncode=1)  # rc=1 means no matches

    with patch("subprocess.run", side_effect=_mock_run):
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            svc.search_logs(q="error")

    assert any(
        cmd[0] in ("rg", "ripgrep") or cmd[0].endswith("/rg")
        for cmd in rg_calls
    ), "ripgrep was not invoked"


def test_search_passes_line_numbers_to_rg(tmp_path):
    """AC7: rg is invoked with --line-number so line_offset is available."""
    svc = _get_svc()

    log_dir = _make_log_dir(tmp_path)
    rg_calls: list[list] = []

    def _mock_run(cmd, **kwargs):
        rg_calls.append(list(cmd))
        return MagicMock(stdout="", returncode=1)

    with patch("subprocess.run", side_effect=_mock_run):
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            svc.search_logs(q="error")

    flat_args = [a for cmd in rg_calls for a in cmd]
    assert "--line-number" in flat_args or "-n" in flat_args


def test_search_sprint_prefilter_limits_scope(tmp_path):
    """AC7: when sprint= is given, only sprint-run-<label>-*.log files are searched."""
    svc = _get_svc()

    log_dir = _make_log_dir(tmp_path)
    sprint_log = log_dir / "sprint-run-sprint-1-20260601T100000.log"
    sprint_log.write_text("Sprint 1 dispatch log line\n", encoding="utf-8")
    other_sprint = log_dir / "sprint-run-sprint-99-20260601T100000.log"
    other_sprint.write_text("Sprint 99 log\n", encoding="utf-8")

    captured_files: list[list[str]] = []

    def _capture_run(cmd, **kwargs):
        captured_files.append([a for a in cmd if ".log" in a])
        return MagicMock(stdout="", returncode=1)

    with patch("subprocess.run", side_effect=_capture_run):
        with patch.object(svc, "_discover_log_dirs",
                          return_value=[(log_dir, "testorg/testrepo")]):
            svc.search_logs(sprint="sprint-1", q="dispatch")

    all_searched = [f for files in captured_files for f in files]
    assert not any("sprint-99" in f for f in all_searched), (
        "sprint-99 log should not be searched when sprint=sprint-1"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC8: Empty results return clear empty state
# ─────────────────────────────────────────────────────────────────────────────

def test_search_empty_results_returns_200_not_error(tmp_path):
    """AC8: no matches → 200 with matches=[] (not 404 or 500)."""
    def _fake_search(**kwargs):
        return {"matches": [], "total": 0, "capped": False,
                "timed_out": False, "query_ms": 1}

    ls_mod = _get_ls()
    with patch.object(ls_mod, "search_logs", side_effect=_fake_search):
        app = _build_app()
        client = TestClient(app)
        resp = client.get("/api/logs/search", params={"q": "zzz_no_match"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["matches"] == []
    assert "total" in data


# ─────────────────────────────────────────────────────────────────────────────
# UI AC6 + AC8: HTML contains search view and result click handler
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_HTML = DASHBOARD_DIR / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def test_ui_search_view_button_exists():
    """AC6/UI: a Search view button exists in the logs view toggle."""
    src = _html()
    assert "logsSetView('search')" in src or "logs-view-search" in src, (
        "No search view button found in logs view toggle"
    )


def test_ui_search_input_exists():
    """AC6/UI: a dedicated search input/form for cross-run search exists in the logs pane."""
    src = _html()
    assert "logs-search-q" in src or "logsSearchSubmit" in src or "lsrSearch" in src, (
        "No cross-run search form element found in logs pane"
    )


def test_ui_search_result_click_opens_log_at_line():
    """AC6/UI: result click handler uses line_offset to scroll the log pane."""
    src = _html()
    assert "line_offset" in src, (
        "line_offset not referenced in JS — result click-to-scroll not implemented"
    )


def test_ui_search_empty_state_element():
    """AC8/UI: an empty state element exists for the search results panel."""
    src = _html()
    # Should have a container that shows "no matches" when results are empty
    assert "No matches" in src or "no-matches" in src or "lsr-empty" in src or (
        "logsSearchEmpty" in src
    ), "No empty-state element for search results"


# ─────────────────────────────────────────────────────────────────────────────
# AC1 continued: router is mounted on the app
# ─────────────────────────────────────────────────────────────────────────────

def test_search_router_exported_from_routers_init():
    """AC1: log_search_router is exported from routers/__init__.py."""
    src = (ROUTERS_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "log_search_router" in src, (
        "log_search_router not exported from routers/__init__.py"
    )


def test_search_router_mounted_in_server():
    """AC1: server.py includes log_search_router via include_router."""
    src = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
    assert "log_search_router" in src
    assert "include_router" in src

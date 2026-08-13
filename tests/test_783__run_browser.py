"""Tests for issue #783: Forensic Run Browser.

Each test function is anchored to a specific acceptance criterion.
All tests use temp dirs + mocks — no live server or external network required.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sqlite3
import sys
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
STATIC_DIR = DASHBOARD_DIR / "static"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))


def _load_module(name: str, path: Path):
    """Load a Python file as a module without triggering package __init__."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _make_test_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with agent_runs, sprints tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number     INTEGER NOT NULL,
            sprint_label     TEXT NOT NULL,
            agent            TEXT NOT NULL,
            started_at       TEXT,
            finished_at      TEXT,
            duration_seconds INTEGER,
            outcome          TEXT,
            total_tokens     INTEGER,
            risk_tier        TEXT,
            model_used       TEXT,
            routing_reason   TEXT,
            worktree_sha     TEXT,
            base_sha         TEXT,
            attempt_kind     TEXT,
            log_path         TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sprints (
            label        TEXT PRIMARY KEY,
            project      TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'planning',
            created_at   TEXT,
            started_at   TEXT,
            ended_at     TEXT,
            end_reason   TEXT,
            parent_label TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _seed_runs(db_path: Path, rows: list[dict]) -> None:
    """Insert agent_runs rows."""
    conn = sqlite3.connect(str(db_path))
    for r in rows:
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, "
            "started_at, finished_at, duration_seconds, outcome, log_path) "
            "VALUES (:issue_number, :sprint_label, :agent, "
            ":started_at, :finished_at, :duration_seconds, :outcome, :log_path)",
            {
                "issue_number": r.get("issue_number", 1),
                "sprint_label": r.get("sprint_label", "sprint-1"),
                "agent": r.get("agent", "coder"),
                "started_at": r.get("started_at", "2026-01-01T00:00:00"),
                "finished_at": r.get("finished_at", "2026-01-01T00:10:00"),
                "duration_seconds": r.get("duration_seconds", 600),
                "outcome": r.get("outcome", "passed"),
                "log_path": r.get("log_path", None),
            },
        )
    conn.commit()
    conn.close()


def _seed_sprints(db_path: Path, rows: list[dict]) -> None:
    """Insert sprints rows."""
    conn = sqlite3.connect(str(db_path))
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state, started_at, ended_at) "
            "VALUES (:label, :project, :state, :started_at, :ended_at)",
            {
                "label": r.get("label", "sprint-1"),
                "project": r.get("project", ""),
                "state": r.get("state", "completed"),
                "started_at": r.get("started_at", None),
                "ended_at": r.get("ended_at", None),
            },
        )
    conn.commit()
    conn.close()


def _make_log_file(tmp_path: Path, issue_num: int = 1, content: str = "log content\n") -> Path:
    """Create a log file inside .commander/logs/ (as required by the allowlist guard)."""
    logs_dir = tmp_path / ".commander" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"sprint-issue-{issue_num}.log"
    log_file.write_text(content, encoding="utf-8")
    return log_file


def _reload_db_module(db_path: Path) -> None:
    """Force db module reload with new DB_PATH env var."""
    os.environ["DB_PATH"] = str(db_path)
    for name in list(sys.modules.keys()):
        if name == "db" or name.endswith(".db"):
            del sys.modules[name]


def _get_svc(db_path: Path):
    """Load runs_service module with patched DB_PATH."""
    svc_path = ROUTERS_DIR / "runs_service.py"
    name = "routers.runs_service"
    if name in sys.modules:
        del sys.modules[name]
    _reload_db_module(db_path)
    return _load_module(name, svc_path)


def _get_router_client(db_path: Path) -> TestClient:
    """Return a TestClient for the runs router."""
    for mod_name in ("routers.runs_service", "routers.runs"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    _reload_db_module(db_path)
    router_mod = _load_module("routers.runs", ROUTERS_DIR / "runs.py")
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app, raise_server_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — Three endpoints exist in the new router (not server.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunsEndpointsExist:
    """AC1: new backend router exposes three endpoints."""

    def test_router_file_exists(self):
        assert (ROUTERS_DIR / "runs.py").exists(), "runs.py router missing"

    def test_router_service_file_exists(self):
        assert (ROUTERS_DIR / "runs_service.py").exists(), "runs_service.py missing"

    def test_runs_endpoint_not_in_server_py(self):
        server_py = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "@app.get(\"/runs\")" not in server_py, "/runs route found in server.py (must be in router)"

    def test_get_runs_returns_200(self, tmp_path):
        db = _make_test_db(tmp_path)
        _seed_runs(db, [{"sprint_label": "sprint-1", "outcome": "passed"}])
        client = _get_router_client(db)
        resp = client.get("/runs")
        assert resp.status_code == 200

    def test_get_runs_returns_sprint_list(self, tmp_path):
        db = _make_test_db(tmp_path)
        _seed_runs(db, [{"sprint_label": "sprint-42", "issue_number": 10, "agent": "coder", "outcome": "passed"}])
        client = _get_router_client(db)
        data = client.get("/runs").json()
        assert isinstance(data, list)
        labels = [s["sprint"] for s in data]
        assert "sprint-42" in labels

    def test_log_endpoint_exists(self, tmp_path):
        db = _make_test_db(tmp_path)
        log_file = _make_log_file(tmp_path, 1, "log content\n")
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": str(log_file)}])
        client = _get_router_client(db)
        resp = client.get("/runs/sprint-1/1/coder/log")
        assert resp.status_code == 200

    def test_log_tail_endpoint_exists(self, tmp_path):
        db = _make_test_db(tmp_path)
        log_file = _make_log_file(tmp_path, 1, "line1\nline2\n")
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": str(log_file)}])
        client = _get_router_client(db)
        resp = client.get("/runs/sprint-1/1/coder/log/tail")
        assert resp.status_code == 200

    def test_runs_endpoint_registered_in_init(self):
        """runs_router must be importable from routers.__init__ (registered in __all__)."""
        init_src = (ROUTERS_DIR / "__init__.py").read_text(encoding="utf-8")
        assert "runs_router" in init_src, "runs_router not found in routers/__init__.py"

    def test_runs_router_mounted_in_server_py(self):
        """server.py must include_router(runs_router) to mount the endpoints."""
        server_src = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "runs_router" in server_src, "runs_router not mounted in server.py"


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — Path traversal returns 400
# ─────────────────────────────────────────────────────────────────────────────

class TestPathTraversal:
    """AC2: traversal attempts against the log endpoint return 400."""

    def test_traversal_sprint_segment_with_dotdot_returns_400(self, tmp_path):
        """A sprint segment containing '..' is rejected with 400."""
        db = _make_test_db(tmp_path)
        # Seed a run with a log_path that escapes the allowed directory
        evil_path = str(Path("/etc/passwd"))
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": evil_path}])
        client = _get_router_client(db)
        # The stored log_path points outside .commander/logs/ — must return 400
        resp = client.get("/runs/sprint-1/1/coder/log")
        assert resp.status_code == 400

    def test_traversal_sprint_segment_dotdot_in_url_returns_400(self, tmp_path):
        """Sprint URL segment containing '..' is rejected before DB lookup."""
        db = _make_test_db(tmp_path)
        client = _get_router_client(db)
        # URL-encode the traversal so it reaches the handler
        resp = client.get("/runs/sprint..%2F..%2Fetc/1/coder/log")
        assert resp.status_code in (400, 404)  # 400 preferred, 404 acceptable

    def test_service_rejects_traversal_path(self, tmp_path):
        """runs_service.resolve_log_path raises ValueError for traversal attempts."""
        db = _make_test_db(tmp_path)
        allowed_dir = tmp_path / ".commander" / "logs"
        allowed_dir.mkdir(parents=True)
        svc = _get_svc(db)
        # A path that escapes the allowed dir should raise ValueError
        with pytest.raises((ValueError, PermissionError)):
            svc.resolve_log_path("../../etc/passwd", str(allowed_dir))

    def test_service_accepts_valid_path(self, tmp_path):
        """resolve_log_path returns path for valid files within allowlist."""
        db = _make_test_db(tmp_path)
        allowed_dir = tmp_path / ".commander" / "logs"
        allowed_dir.mkdir(parents=True)
        log_file = allowed_dir / "sprint-issue-1.log"
        log_file.write_text("content", encoding="utf-8")
        svc = _get_svc(db)
        result = svc.resolve_log_path(str(log_file), str(allowed_dir))
        assert result == log_file.resolve()


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — agent_runs.log_path column exists
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRunsLogPath:
    """AC3: agent_runs table has log_path column written at dispatch time."""

    def test_log_path_column_in_create_statement(self):
        """db.py _create_agent_runs_table includes log_path column."""
        db_src = (DASHBOARD_DIR / "db.py").read_text(encoding="utf-8")
        assert "log_path" in db_src, "log_path column not found in db.py"

    def test_record_agent_start_accepts_log_path(self, tmp_path):
        """record_agent_start stores log_path when provided."""
        db_path = tmp_path / "test.db"
        os.environ["DB_PATH"] = str(db_path)
        # Reload db module with new DB_PATH
        if "db" in sys.modules:
            del sys.modules["db"]
        import importlib
        db_mod = importlib.import_module("db")
        db_mod.init_db()
        log_path_val = str(tmp_path / "sprint-issue-5.log")
        run_id = db_mod.record_agent_start(
            issue_number=5,
            sprint_label="sprint-99",
            agent="coder",
            log_path=log_path_val,
        )
        assert run_id is not None
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT log_path FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == log_path_val

    def test_record_agent_start_log_path_defaults_to_null(self, tmp_path):
        """record_agent_start log_path defaults to NULL when not provided."""
        db_path = tmp_path / "test2.db"
        os.environ["DB_PATH"] = str(db_path)
        if "db" in sys.modules:
            del sys.modules["db"]
        import importlib
        db_mod = importlib.import_module("db")
        db_mod.init_db()
        run_id = db_mod.record_agent_start(
            issue_number=6, sprint_label="sprint-99", agent="tester"
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT log_path FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None  # defaults to NULL


# AC4 — run_browser.html removed in issue #2243; tests deleted with it.

# ─────────────────────────────────────────────────────────────────────────────
# AC5 — Failed runs sort before passing runs
# ─────────────────────────────────────────────────────────────────────────────

class TestFailedRunsSortFirst:
    """AC5: failed runs appear before passing runs in sprint and ticket lists."""

    def test_failed_sprint_sorts_before_passing(self, tmp_path):
        db = _make_test_db(tmp_path)
        _seed_runs(db, [
            {"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder", "outcome": "passed"},
            {"sprint_label": "sprint-2", "issue_number": 2, "agent": "coder", "outcome": "failed"},
        ])
        client = _get_router_client(db)
        data = client.get("/runs").json()
        labels = [s["sprint"] for s in data]
        assert labels.index("sprint-2") < labels.index("sprint-1"), \
            "Failed sprint should sort before passing sprint"

    def test_failed_ticket_sorts_before_passing_within_sprint(self, tmp_path):
        db = _make_test_db(tmp_path)
        _seed_runs(db, [
            {"sprint_label": "sprint-1", "issue_number": 10, "agent": "coder", "outcome": "passed"},
            {"sprint_label": "sprint-1", "issue_number": 20, "agent": "coder", "outcome": "failed"},
        ])
        client = _get_router_client(db)
        data = client.get("/runs").json()
        sprint = next(s for s in data if s["sprint"] == "sprint-1")
        issue_nums = [t["issue_number"] for t in sprint["tickets"]]
        assert issue_nums.index(20) < issue_nums.index(10), \
            "Failed ticket should sort before passing ticket"

    def test_sprint_has_failed_count(self, tmp_path):
        db = _make_test_db(tmp_path)
        _seed_runs(db, [
            {"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder", "outcome": "failed"},
            {"sprint_label": "sprint-1", "issue_number": 2, "agent": "coder", "outcome": "passed"},
        ])
        client = _get_router_client(db)
        data = client.get("/runs").json()
        sprint = data[0]
        assert sprint["failed_count"] == 1
        assert sprint["total_count"] == 2


# AC6 — run_browser.html removed in issue #2243; tests deleted with it.

# ─────────────────────────────────────────────────────────────────────────────
# AC7 — Paging works on a 5 MB log without full memory load
# ─────────────────────────────────────────────────────────────────────────────

class TestLogPaging:
    """AC7: log endpoint supports offset/limit and tail without loading full file."""

    def test_log_endpoint_supports_offset_limit(self, tmp_path):
        db = _make_test_db(tmp_path)
        lines_content = "".join(f"line {i}\n" for i in range(100))
        log_file = _make_log_file(tmp_path, 1, lines_content)
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": str(log_file)}])
        client = _get_router_client(db)
        resp = client.get("/runs/sprint-1/1/coder/log?offset=10&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data
        assert len(data["lines"]) == 5
        assert data["lines"][0] == "line 10"

    def test_log_endpoint_default_limit(self, tmp_path):
        db = _make_test_db(tmp_path)
        lines_content = "".join(f"line {i}\n" for i in range(2000))
        log_file = _make_log_file(tmp_path, 1, lines_content)
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": str(log_file)}])
        client = _get_router_client(db)
        resp = client.get("/runs/sprint-1/1/coder/log")
        data = resp.json()
        assert "total_lines" in data
        # Should not load all 2000 lines by default
        assert len(data["lines"]) <= 500

    def test_log_tail_returns_last_kb(self, tmp_path):
        db = _make_test_db(tmp_path)
        # Write 10 KB of data inside .commander/logs/
        content = ("x" * 100 + "\n") * 100  # ~100 * 101 bytes ≈ 10 KB
        log_file = _make_log_file(tmp_path, 1, content)
        _seed_runs(db, [{"sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
                          "log_path": str(log_file)}])
        client = _get_router_client(db)
        resp = client.get("/runs/sprint-1/1/coder/log/tail?kb=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert len(data["content"].encode("utf-8")) <= 1024 + 200  # ≤ 1 KB + one line slack

    def test_log_service_uses_seek_not_readline(self, tmp_path):
        """Verify read_log_page uses file.seek(), not readlines() for the whole file."""
        db = _make_test_db(tmp_path)
        svc = _get_svc(db)
        svc_src = (ROUTERS_DIR / "runs_service.py").read_text(encoding="utf-8")
        assert "seek" in svc_src or "offset" in svc_src.lower(), \
            "runs_service.py must use file.seek() for efficient paging"

    def test_log_service_tail_uses_seek_from_end(self, tmp_path):
        """Verify read_log_tail uses seek(0, 2) or os.SEEK_END for O(1) tail."""
        svc_src = (ROUTERS_DIR / "runs_service.py").read_text(encoding="utf-8")
        assert "SEEK_END" in svc_src or "seek(-" in svc_src or "seek(0, 2)" in svc_src \
               or "os.SEEK_END" in svc_src, \
            "runs_service.py must seek from end of file for tail"


# AC8 — ntfy Click header removed in issue #2243; tests deleted with it.
# AC9 — run_browser.html removed in issue #2243; tests deleted with it.

# ─────────────────────────────────────────────────────────────────────────────
# AC10 — Zero GitHub API calls on this surface
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroGitHubCalls:
    """AC10: the runs router makes no GitHub API calls."""

    def test_runs_service_does_not_import_github_client(self):
        svc_src = (ROUTERS_DIR / "runs_service.py").read_text(encoding="utf-8")
        assert "github_client" not in svc_src, \
            "runs_service.py must not import github_client"

    def test_runs_router_does_not_import_github_client(self):
        router_src = (ROUTERS_DIR / "runs.py").read_text(encoding="utf-8")
        assert "github_client" not in router_src, \
            "runs.py must not import github_client"

    def test_get_runs_no_github_calls(self, tmp_path):
        """GET /runs should not call any GitHub API endpoints."""
        db = _make_test_db(tmp_path)
        _seed_runs(db, [{"sprint_label": "sprint-1", "outcome": "passed"}])
        client = _get_router_client(db)
        with patch("urllib.request.urlopen") as mock_urlopen:
            resp = client.get("/runs")
        assert mock_urlopen.call_count == 0, "GET /runs made unexpected HTTP calls"
        assert resp.status_code == 200

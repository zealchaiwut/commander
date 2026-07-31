"""Tests for issue #2042: Failures inbox must exclude test-harness / scratchpad agents.

Root cause
----------
``_rows_from_agents`` surfaces any ``agents`` row with ``status='timed_out'``,
including Claude Code sub-agent sessions spawned by test suites on the same
machine.  Those rows have ``working_dir`` under ``/private/tmp/...`` and
``project=None``.  The post-union filter in ``get_failures`` used to KEEP
rows whose project could not be determined ("can't determine → possibly
relevant"), causing scratchpad sessions to appear at the top of every
project's Failures inbox — pure noise ahead of real signal.

Fix (issue #2042)
-----------------
Two complementary changes in failures_service.py:

1. ``_rows_from_agents`` skips any agent whose ``working_dir`` starts with a
   system temp prefix (``/tmp/``, ``/private/tmp/``, ``/var/folders/``, etc.).
   Structural signal preferred over agent-name pattern matching because agent
   names are free-form; OS temp paths are not.

2. The post-union ``_project_matches`` helper now returns ``False`` for agents
   whose project is ``None`` when a project filter is active.  Showing
   unknown-project rows in every project's inbox (cross-project bleed) is worse
   than occasionally hiding a genuinely ambiguous row.

How these tests FAIL against pre-fix code
-----------------------------------------
test_scratchpad_agent_excluded_via_http:
    Pre-fix: ``_project_matches`` returns ``True`` for ``row_project=None`` →
    the scratchpad row appears in results.  The assertion
    ``assert session_not_in_results`` FAILS.

test_is_scratchpad_working_dir_tmp:
    Pre-fix: ``_is_scratchpad_working_dir`` does not exist → ImportError /
    AttributeError → FAILS.

Git-isolation guard
-------------------
The ``git_no_mutation`` autouse fixture verifies that no test in this module
moves HEAD.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402

# Load failures_service via importlib to avoid sys.path conflicts with
# other test modules that import the same module under a different key.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "failures_service_2042",
    _DASHBOARD_ROOT / "routers" / "failures_service.py",
)
_fs_mod = _ilu.module_from_spec(_spec)
sys.modules.setdefault("failures_service_2042", _fs_mod)
_spec.loader.exec_module(_fs_mod)

get_failures = _fs_mod.get_failures
_is_scratchpad_working_dir = _fs_mod._is_scratchpad_working_dir


# ── git-isolation guard ───────────────────────────────────────────────────────

def _head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(_REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert no test in this module commits to the repository.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    before = _head_sha()
    yield
    after = _head_sha()
    assert before == after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {before}\n"
        f"  HEAD after:  {after}\n"
        "An unmocked code path ran 'git commit'. All git-touching paths must be stubbed."
    )


# ── DB isolation fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def isolated_db():
    """Temporary SQLite DB for each test; patches _db.DB_PATH and DB_PATH env."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="commander-test-2042-")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()

    original_path = _db.DB_PATH
    _db.DB_PATH = Path(db_path)
    os.environ["DB_PATH"] = db_path
    _db.init_db()

    yield db_path

    _db.DB_PATH = original_path
    os.environ.pop("DB_PATH", None)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


# ── DB row helpers ────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc).isoformat()
_PROJECT = "zealchaiwut/commander"


def _insert_agent(conn, *, session_id: str, name: str, working_dir: str,
                  status: str = "timed_out") -> None:
    """Insert a row into the agents table."""
    conn.execute(
        """INSERT INTO agents (session_id, name, working_dir, status, last_seen, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, name, working_dir, status, _NOW, _NOW),
    )


# ── Unit: _is_scratchpad_working_dir ─────────────────────────────────────────

def test_is_scratchpad_working_dir_tmp():
    """AC3 (unit): /tmp/ paths are identified as scratchpad.

    PRE-FIX FAILURE: _is_scratchpad_working_dir does not exist on the module →
    AttributeError / ImportError.
    """
    assert _is_scratchpad_working_dir("/tmp/claude-501/abc/scratchpad/test_run") is True


def test_is_scratchpad_working_dir_private_tmp():
    """AC3 (unit): /private/tmp/ paths (macOS) are identified as scratchpad."""
    assert _is_scratchpad_working_dir(
        "/private/tmp/claude-501/abc/scratchpad/test_run"
    ) is True


def test_is_scratchpad_working_dir_var_folders():
    """AC3 (unit): /var/folders/ paths (macOS) are identified as scratchpad."""
    assert _is_scratchpad_working_dir("/var/folders/ab/xyz123/T/pytest-of-user/") is True


def test_is_scratchpad_working_dir_dev_path():
    """AC3 (unit): a real /dev/<project>/clone path is NOT a scratchpad."""
    assert _is_scratchpad_working_dir(
        "/Users/chaiwutchaianuchittrakul/dev/commander/coder"
    ) is False


def test_is_scratchpad_working_dir_none():
    """AC3 (unit): None working_dir returns False (not a scratchpad)."""
    assert _is_scratchpad_working_dir(None) is False


def test_is_scratchpad_working_dir_empty():
    """AC3 (unit): empty string working_dir returns False."""
    assert _is_scratchpad_working_dir("") is False


# ── AC3 behavioral: scratchpad agent excluded, genuine agent included ─────────

def test_scratchpad_agent_excluded_via_http(isolated_db):
    """AC3: scratchpad agent does NOT appear in the project Failures inbox.

    Seeds an agents row whose working_dir is under /private/tmp/ (matching the
    live rows that triggered issue #2042).  Drives GET /api/failures via
    TestClient and asserts the row is absent.

    PRE-FIX FAILURE: _project_matches returned True for row_project=None →
    the scratchpad row was kept → assertion fails because the agent name
    appears in results.
    """
    from server import app  # noqa: E402 — after env patching

    with _db.get_conn() as conn:
        _insert_agent(
            conn,
            session_id="sess-scratchpad-2042-a",
            name="agent·test_concurrent_processes_boun0··#84ccde",
            working_dir="/private/tmp/claude-501/abc-def/scratchpad/test_run",
        )
        conn.commit()

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get(f"/api/failures?project={_PROJECT}")
    assert resp.status_code == 200
    rows = resp.json()

    agent_names = [r.get("agent") for r in rows]
    assert "agent·test_concurrent_processes_boun0··#84ccde" not in agent_names, (
        "Scratchpad agent (working_dir under /private/tmp/) must be excluded from "
        "the Failures inbox.  Pre-fix: row_project=None was kept by the post-union "
        "filter, so the scratchpad session appeared at the top of every project's inbox."
    )


def test_genuine_agent_timeout_included_via_http(isolated_db):
    """AC4 / AC3: a genuine timed-out project agent still appears in the inbox.

    Seeds an agents row with working_dir under /dev/commander (resolves to
    project='commander').  Asserts it appears in GET /api/failures?project=...

    This test must pass both pre-fix AND post-fix: the fix must not over-filter.
    """
    from server import app  # noqa: E402

    with _db.get_conn() as conn:
        _insert_agent(
            conn,
            session_id="sess-genuine-2042-b",
            name="coder-real-agent",
            working_dir="/Users/chaiwutchaianuchittrakul/dev/commander/coder",
        )
        conn.commit()

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get(f"/api/failures?project={_PROJECT}")
    assert resp.status_code == 200
    rows = resp.json()

    agent_names = [r.get("agent") for r in rows]
    assert "coder-real-agent" in agent_names, (
        "A genuine timed-out project agent (working_dir under /dev/commander) must "
        "still appear in the Failures inbox.  The scratchpad filter must not "
        "over-filter real sprint failures."
    )


def test_scratchpad_excluded_from_get_failures_directly(isolated_db):
    """AC3 (direct service call): scratchpad agent excluded from get_failures().

    Drives the service function directly (no HTTP layer) so the test is not
    confused by any HTTP routing issues.

    PRE-FIX FAILURE: the scratchpad row has project=None; the old filter
    returned True for None-project rows → scratchpad row in results → fails.
    """
    with _db.get_conn() as conn:
        _insert_agent(
            conn,
            session_id="sess-scratchpad-2042-c",
            name="test_harness_agent_xyz",
            working_dir="/tmp/pytest-1234/test_session/sub_agent",
        )
        conn.commit()

    result = get_failures(project=_PROJECT)
    agent_names = [r.get("agent") for r in result]
    assert "test_harness_agent_xyz" not in agent_names, (
        "get_failures(project=...) must exclude agents with scratchpad working_dir. "
        "Pre-fix: _rows_from_agents did not filter scratchpad dirs, and "
        "_project_matches kept None-project rows."
    )


def test_genuine_agent_included_in_get_failures_directly(isolated_db):
    """AC4 (direct service call): genuine project agent included by get_failures()."""
    with _db.get_conn() as conn:
        _insert_agent(
            conn,
            session_id="sess-genuine-2042-d",
            name="tester-real-agent",
            working_dir="/home/runner/dev/commander/tester",
        )
        conn.commit()

    result = get_failures(project=_PROJECT)
    agent_names = [r.get("agent") for r in result]
    assert "tester-real-agent" in agent_names, (
        "A genuine timed-out agent with /dev/commander path must appear in results."
    )


def test_no_project_filter_shows_all_non_scratchpad(isolated_db):
    """With no project filter, non-scratchpad agents from any project appear."""
    with _db.get_conn() as conn:
        # A scratchpad agent — should be excluded even without project filter
        _insert_agent(
            conn,
            session_id="sess-scratch-2042-e",
            name="scratch-agent",
            working_dir="/private/tmp/abc/scratchpad/test",
        )
        # A real agent from a different project — should appear without project filter
        _insert_agent(
            conn,
            session_id="sess-other-2042-f",
            name="perf-coach-agent",
            working_dir="/Users/dev/perf-coach/coder",
        )
        conn.commit()

    result = get_failures()  # no project filter
    agent_names = [r.get("agent") for r in result]

    # Scratchpad excluded even without project filter (filtered in _rows_from_agents)
    assert "scratch-agent" not in agent_names, (
        "Scratchpad agents must be excluded regardless of whether a project filter is used."
    )
    # Real agent from any project appears when no filter
    assert "perf-coach-agent" in agent_names, (
        "Non-scratchpad agents must appear when no project filter is applied."
    )


def test_scratchpad_agent_excluded_both_filter_paths(isolated_db):
    """AC1+AC2: both the working_dir filter and the None-project filter work together.

    Seeds one scratchpad row (working_dir=/tmp/...) and one genuinely
    unknown-path row (working_dir with no /dev/ segment, not a temp dir).
    With project filter active:
    - scratchpad row excluded by _rows_from_agents
    - unknown-path row excluded by _project_matches (None-project → False)
    """
    with _db.get_conn() as conn:
        # Scratchpad: filtered by _is_scratchpad_working_dir in _rows_from_agents
        _insert_agent(
            conn,
            session_id="sess-scratch-2042-g",
            name="scratch-filtered",
            working_dir="/tmp/some-test/agent",
        )
        # Unknown path: not a temp dir but has no /dev/ segment → project=None
        _insert_agent(
            conn,
            session_id="sess-unknown-2042-h",
            name="unknown-path-agent",
            working_dir="/opt/myapp/agent",
        )
        conn.commit()

    result = get_failures(project=_PROJECT)
    agent_names = [r.get("agent") for r in result]

    assert "scratch-filtered" not in agent_names, (
        "Scratchpad agent must be excluded by the working_dir filter."
    )
    assert "unknown-path-agent" not in agent_names, (
        "Agent with unresolvable project path (no /dev/ segment) must be excluded "
        "when a project filter is requested.  Showing it in every project's inbox "
        "is worse than hiding it (issue #2042 AC2)."
    )

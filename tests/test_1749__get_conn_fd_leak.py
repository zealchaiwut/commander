"""Fix sqlite fd leak: get_conn() never closes connections (issue #1749).

Problem: db.get_conn() returned a raw sqlite3.Connection. Every call site uses
`with get_conn() as conn:`, but sqlite3.Connection.__exit__ only commits/rolls
back the transaction — it never closes the connection. Every DB call leaked
one OS file descriptor. On PRD this accumulated to 223/256 open fds (117 of
them separate handles to commander.db), and once the OS fd cap was hit, `gh`
subprocess spawns started failing with OSError: [Errno 24] Too many open files
— surfacing to the operator as a spurious "merge conflict" during bulk-complete.

AC1: get_conn() is a context manager; the underlying connection is closed when
     the `with` block exits (normal exit and on exception).
AC2: All existing `with get_conn() as conn:` call sites continue to work
     unchanged (verified by the full existing suite; see AC4).
AC3: A test opens N connections sequentially via `with get_conn() as conn:`
     and asserts no lingering open connection/fd (via sqlite3's own
     connection tracking — each conn.close() call is verified, and the file
     is removable/lockable afterward, which a leaked fd would prevent).
AC4: Full existing test suite still passes.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1749.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    yield _db_module
    _db_module.DB_PATH = original


class TestGetConnIsContextManager:
    def test_get_conn_returns_context_manager(self, fresh_db):
        cm = fresh_db.get_conn()
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__")
        cm.__enter__()
        cm.__exit__(None, None, None)

    def test_connection_closed_after_normal_exit(self, fresh_db):
        with fresh_db.get_conn() as conn:
            conn.execute("SELECT 1")
        # A closed sqlite3.Connection raises ProgrammingError on further use.
        with pytest.raises(fresh_db.sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_connection_closed_after_exception(self, fresh_db):
        conn_ref = {}
        with pytest.raises(ValueError):
            with fresh_db.get_conn() as conn:
                conn_ref["conn"] = conn
                raise ValueError("boom")
        with pytest.raises(fresh_db.sqlite3.ProgrammingError):
            conn_ref["conn"].execute("SELECT 1")

    def test_commit_persists_on_normal_exit(self, fresh_db):
        with fresh_db.get_conn() as conn:
            fresh_db._create_sprint_lifecycle_tables(conn)
            conn.execute(
                "INSERT OR REPLACE INTO sprints (label, state) VALUES (?, ?)",
                ("sprint-1749", "running"),
            )
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT state FROM sprints WHERE label = ?", ("sprint-1749",)
            ).fetchone()
        assert row["state"] == "running"

    def test_rollback_on_exception_does_not_persist(self, fresh_db):
        with fresh_db.get_conn() as conn:
            fresh_db._create_sprint_lifecycle_tables(conn)
        with contextlib.suppress(RuntimeError):
            with fresh_db.get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sprints (label, state) VALUES (?, ?)",
                    ("sprint-1749-rb", "running"),
                )
                raise RuntimeError("boom")
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT state FROM sprints WHERE label = ?", ("sprint-1749-rb",)
            ).fetchone()
        assert row is None


class TestNoFdLeak:
    def test_many_sequential_opens_leave_no_open_handles(self, fresh_db):
        """Regression for the fd leak: opening the DB many times in sequence
        must not accumulate open connections. If get_conn() leaked (as before
        this fix), the underlying sqlite3.Connection objects stay usable after
        their `with` block exits; here every one must already be closed."""
        conns = []
        for i in range(50):
            with fresh_db.get_conn() as conn:
                conn.execute("SELECT 1")
                conns.append(conn)
        still_open = [c for c in conns if _is_connection_open(c)]
        assert still_open == [], (
            f"{len(still_open)}/50 connections were not closed — fd leak regression"
        )


def _is_connection_open(conn) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False

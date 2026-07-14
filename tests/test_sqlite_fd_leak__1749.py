"""Tests for issue #1749: Fix sqlite fd leak — get_conn() closes connections.

Consolidated from test_sqlite_fd_leak__1749.py (tester-authored UAT tests) and
test_1749__fix_sqlite_fd_leak.py (coder-authored unit tests).
Both sets of behavioral assertions are retained.

AC coverage:
  AC1 — get_conn() is a context manager; the underlying sqlite3 connection
         is closed when the with-block exits (normal exit and on exception).
  AC2 — All existing `with get_conn() as conn:` / `with db.get_conn() as conn:`
         call sites continue to work with no changes.
  AC3 — Opening N connections via `with get_conn() as conn:` sequentially
         does not grow the OS fd count unboundedly; every opened connection
         is closed before the next one opens.
  AC4 — Core db helpers that use get_conn() internally still work (smoke
         regression check).
"""
from __future__ import annotations

import contextlib
import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import httpx


_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def _reload_db(monkeypatch, tmp_path):
    """Set DB_PATH and reload apps.dashboard.db to pick up the env var."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import apps.dashboard.db as db_module
    importlib.reload(db_module)
    return db_module


def _open_fd_count() -> int:
    """Return the number of open file descriptors for this process."""
    dev_fd = Path("/dev/fd")
    if dev_fd.exists():
        return sum(1 for _ in dev_fd.iterdir())
    proc_fd = Path(f"/proc/{os.getpid()}/fd")
    if proc_fd.exists():
        return sum(1 for _ in proc_fd.iterdir())
    import resource
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    count = 0
    for fd_num in range(min(soft, 4096)):
        try:
            os.fstat(fd_num)
            count += 1
        except OSError:
            pass
    return count


# ---------------------------------------------------------------------------
# AC1 — get_conn() is a context manager that closes on exit
# ---------------------------------------------------------------------------

class TestAC1ConnectionClosedOnExit:
    """AC1: connection is closed when the with-block exits, both normally and on error."""

    def _make_mock_sqlite3(self):
        """Return (mock_sqlite3_module, mock_conn) for patching db.sqlite3."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_sqlite3 = MagicMock()
        mock_sqlite3.connect.return_value = mock_conn
        mock_sqlite3.Row = sqlite3.Row
        return mock_sqlite3, mock_conn

    def test_connection_closed_on_normal_exit(self, tmp_path, monkeypatch):
        """After `with get_conn() as conn:` exits normally, conn.close() was called."""
        db = _reload_db(monkeypatch, tmp_path)
        mock_sqlite3, mock_conn = self._make_mock_sqlite3()

        with patch.object(db, "sqlite3", mock_sqlite3):
            with db.get_conn() as conn:
                pass

        mock_conn.close.assert_called_once()

    def test_connection_closed_on_exception(self, tmp_path, monkeypatch):
        """When the with-block raises, conn.close() is still called (finally)."""
        db = _reload_db(monkeypatch, tmp_path)
        mock_sqlite3, mock_conn = self._make_mock_sqlite3()

        with patch.object(db, "sqlite3", mock_sqlite3):
            with contextlib.suppress(RuntimeError):
                with db.get_conn() as conn:
                    raise RuntimeError("intentional")

        mock_conn.close.assert_called_once()

    def test_get_conn_is_context_manager(self, tmp_path, monkeypatch):
        """get_conn() returns an object with __enter__ and __exit__."""
        db = _reload_db(monkeypatch, tmp_path)
        cm = db.get_conn()
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__"), (
            "get_conn() must return a context manager"
        )
        with cm:
            pass

    def test_close_called_exactly_once_per_use(self, tmp_path, monkeypatch):
        """Each with-block calls close() exactly once — not zero, not twice."""
        db = _reload_db(monkeypatch, tmp_path)
        mock_sqlite3, mock_conn = self._make_mock_sqlite3()

        with patch.object(db, "sqlite3", mock_sqlite3):
            with db.get_conn():
                pass
            with db.get_conn():
                pass

        assert mock_conn.close.call_count == 2, (
            f"Expected close() called twice (once per with-block), "
            f"got {mock_conn.close.call_count}"
        )

    def test_sqlite_fd_leak__get_conn_is_context_manager(self):
        """UAT: get_conn() is a context manager; connections close on exit."""
        from apps.dashboard import db
        import inspect

        # Verify get_conn is a context manager function (generator-based)
        assert inspect.isgeneratorfunction(db.get_conn.__wrapped__)

        # Test normal exit: connection closes
        conn = None
        with db.get_conn() as c:
            conn = c
            assert conn is not None
            result = conn.execute("SELECT 1").fetchone()
            assert result is not None
        # After exiting, the connection should be closed
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_sqlite_fd_leak__context_manager_closes_on_exception(self):
        """UAT: get_conn() closes connections even on exception."""
        from apps.dashboard import db

        conn = None
        try:
            with db.get_conn() as c:
                conn = c
                assert conn is not None
                raise ValueError("test exception")
        except ValueError:
            pass

        # Connection should be closed even after exception
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# AC2 — Existing call-site pattern continues to work
# ---------------------------------------------------------------------------

class TestAC2ExistingCallSitesUnchanged:
    """AC2: `with get_conn() as conn:` and `with db.get_conn() as conn:` still work."""

    def test_with_get_conn_as_conn_works(self, tmp_path, monkeypatch):
        """`with get_conn() as conn:` returns a working Connection."""
        db = _reload_db(monkeypatch, tmp_path)
        with db.get_conn() as conn:
            result = conn.execute("SELECT 1 AS val").fetchone()
            assert result["val"] == 1

    def test_with_get_conn_can_create_and_query_table(self, tmp_path, monkeypatch):
        """Can create a table and insert rows inside `with get_conn() as conn:`."""
        db = _reload_db(monkeypatch, tmp_path)
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
            conn.execute("INSERT INTO t VALUES ('hello')")
            conn.commit()
            row = conn.execute("SELECT v FROM t").fetchone()
            assert row["v"] == "hello"

    def test_commit_in_with_block_persists_to_next_with_block(self, tmp_path, monkeypatch):
        """Data committed inside one with-block is readable in a subsequent one."""
        db = _reload_db(monkeypatch, tmp_path)
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS persist_test (x INTEGER)")
            conn.execute("INSERT INTO persist_test VALUES (42)")
            conn.commit()

        with db.get_conn() as conn2:
            row = conn2.execute("SELECT x FROM persist_test").fetchone()
            assert row is not None and row["x"] == 42

    def test_conn_has_row_factory_set(self, tmp_path, monkeypatch):
        """The yielded connection has row_factory = sqlite3.Row (dict-like rows)."""
        db = _reload_db(monkeypatch, tmp_path)
        with db.get_conn() as conn:
            assert conn.row_factory is sqlite3.Row, (
                "get_conn() must set conn.row_factory = sqlite3.Row"
            )

    def test_sqlite_fd_leak__all_call_sites_work(self, client):
        """UAT: Existing call sites work — backward compatible API."""
        r = client.get("/api/home")
        assert r.status_code == 200
        assert r.json() is not None


# ---------------------------------------------------------------------------
# AC3 — No fd growth across N sequential connections
# ---------------------------------------------------------------------------

class TestAC3NoFdGrowth:
    """AC3: fd count does not grow when opening N connections sequentially."""

    def test_close_count_matches_open_count(self, tmp_path, monkeypatch):
        """Each get_conn() call results in exactly one close() call — no fd leak."""
        db = _reload_db(monkeypatch, tmp_path)

        N = 20
        close_count = [0]
        open_count = [0]
        real_sqlite3 = sqlite3

        mock_sqlite3 = MagicMock()
        mock_sqlite3.Row = real_sqlite3.Row

        def tracking_connect(path, **kwargs):
            open_count[0] += 1
            conn = real_sqlite3.connect(str(path), **kwargs)
            conn.row_factory = real_sqlite3.Row

            class _ConnProxy:
                def __init__(self, inner):
                    self._inner = inner

                def __getattr__(self, name):
                    return getattr(self._inner, name)

                def close(self):
                    close_count[0] += 1
                    self._inner.close()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return self._inner.__exit__(*a)

            return _ConnProxy(conn)

        mock_sqlite3.connect.side_effect = tracking_connect

        with patch.object(db, "sqlite3", mock_sqlite3):
            for _ in range(N):
                with db.get_conn() as conn:
                    conn.execute("SELECT 1")

        assert open_count[0] == N, f"Expected {N} opens, got {open_count[0]}"
        assert close_count[0] == N, (
            f"Expected {N} close() calls for {N} opens, got {close_count[0]}. "
            "Connections are leaking — close() must be called in a finally block."
        )

    def test_fd_count_stable_after_repeated_get_conn(self, tmp_path, monkeypatch):
        """OS fd count does not grow after N sequential `with get_conn()` calls."""
        db = _reload_db(monkeypatch, tmp_path)

        # Warm up to let any lazy initialisation settle
        with db.get_conn() as conn:
            conn.execute("SELECT 1")

        N = 30
        fd_before = _open_fd_count()
        for _ in range(N):
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
        fd_after = _open_fd_count()

        # Allow a tolerance of 2 for transient OS-level fds
        assert fd_after - fd_before <= 2, (
            f"fd count grew by {fd_after - fd_before} after {N} sequential "
            f"get_conn() calls (before={fd_before}, after={fd_after}). "
            "Connections must be closed on exit — fd leak detected."
        )

    def test_sqlite_fd_leak__no_unbounded_growth(self):
        """UAT: Repeated sequential opens do not exhaust file descriptors."""
        from apps.dashboard import db

        for i in range(10):
            with db.get_conn() as conn:
                result = conn.execute("SELECT 1").fetchone()
                assert result is not None


# ---------------------------------------------------------------------------
# AC4 — Smoke: existing db helpers still work after the change
# ---------------------------------------------------------------------------

class TestAC4NoRegression:
    """AC4: core db helpers that use get_conn() internally still work."""

    def test_init_db_completes(self, tmp_path, monkeypatch):
        """init_db() runs without error (uses get_conn() internally)."""
        db = _reload_db(monkeypatch, tmp_path)
        db.init_db()  # must not raise

    def test_record_sprint_start_and_get_sprint(self, tmp_path, monkeypatch):
        """record_sprint_start + get_sprint round-trip via get_conn()."""
        db = _reload_db(monkeypatch, tmp_path)
        db.init_db()
        db.record_sprint_start("sprint-99", project="owner/repo")
        row = db.get_sprint("sprint-99", project="owner/repo")
        assert row is not None
        assert row["state"] == "running"

    def test_upsert_and_get_mirrored_milestone(self, tmp_path, monkeypatch):
        """upsert_milestones + get_mirrored_milestones work via get_conn()."""
        db = _reload_db(monkeypatch, tmp_path)
        db.init_db()
        written = db.upsert_milestones("owner/repo", [
            {"number": 1, "title": "v1", "state": "open", "description": ""}
        ])
        assert written == 1
        rows = db.get_mirrored_milestones("owner/repo")
        assert len(rows) == 1
        assert rows[0]["title"] == "v1"

    def test_sqlite_fd_leak__commit_and_rollback_unchanged(self):
        """UAT: commit/rollback behavior unchanged — full db round-trip."""
        from apps.dashboard import db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            test_db = f.name

        try:
            with patch.object(db, 'DB_PATH', test_db):
                db.init_db()

                with db.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO agents (session_id, name, working_dir, last_seen, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        ("test_1", "test_agent", "/tmp", "2026-01-01T00:00:00", "2026-01-01T00:00:00")
                    )
                    conn.commit()

                with db.get_conn() as conn:
                    row = conn.execute(
                        "SELECT session_id FROM agents WHERE session_id = ?",
                        ("test_1",)
                    ).fetchone()
                    assert row is not None
                    assert row["session_id"] == "test_1"
        finally:
            try:
                os.unlink(test_db)
            except:
                pass

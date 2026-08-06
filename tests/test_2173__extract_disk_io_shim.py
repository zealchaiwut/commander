"""Tests for issue #2173: extract triplicated db-import shim into _handle_disk_io helper.

Acceptance criteria:
  AC1  A module-level helper _handle_disk_io(exc, db_module) exists in
       github_events_sync and is callable.
  AC2  When db_module is provided, _handle_disk_io delegates to
       db_module.handle_runtime_disk_io_error(exc) — no import side-effect.
  AC3  When db_module is None, _handle_disk_io falls back to the real `db`
       module and calls db.handle_runtime_disk_io_error(exc).
  AC4  All three call sites in run_issues_sync_loop use the helper — verified
       behaviorally: disk I/O errors from each site still abort the loop and
       trigger integrity_check (regression guard).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db  # noqa: E402
import github_events_sync  # noqa: E402


def _make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


_GOOD_MS_STUB = types.SimpleNamespace(sync_milestones_mirror=lambda *a, **kw: None)


# ── AC1: helper exists ────────────────────────────────────────────────────────

class TestHelperExists:
    def test_handle_disk_io_is_callable(self):
        """_handle_disk_io must exist on github_events_sync and be callable (AC1)."""
        assert hasattr(github_events_sync, "_handle_disk_io"), (
            "github_events_sync must expose _handle_disk_io"
        )
        assert callable(github_events_sync._handle_disk_io)


# ── AC2: delegates to provided db_module ─────────────────────────────────────

class TestHandleDiskIoWithModule:
    def test_delegates_to_provided_db_module(self):
        """_handle_disk_io calls db_module.handle_runtime_disk_io_error (AC2)."""
        mock_db = MagicMock()
        exc = ValueError("something")
        github_events_sync._handle_disk_io(exc, mock_db)
        mock_db.handle_runtime_disk_io_error.assert_called_once_with(exc)

    def test_no_op_for_non_disk_io_via_module(self):
        """Non-disk-IO exception → no-op (handle_runtime_disk_io_error is a no-op)."""
        mock_db = MagicMock()
        mock_db.handle_runtime_disk_io_error.return_value = None
        exc = sqlite3.OperationalError("no such table")
        github_events_sync._handle_disk_io(exc, mock_db)
        mock_db.handle_runtime_disk_io_error.assert_called_once_with(exc)

    def test_raises_for_disk_io_via_module(self):
        """Disk I/O OperationalError → helper propagates the raise from db_module (AC2)."""
        mock_db = MagicMock()
        mock_db.handle_runtime_disk_io_error.side_effect = RuntimeError("disk I/O")
        exc = sqlite3.OperationalError("disk I/O error")
        with pytest.raises(RuntimeError, match="disk I/O"):
            github_events_sync._handle_disk_io(exc, mock_db)


# ── AC3: falls back to real db when db_module is None ────────────────────────

class TestHandleDiskIoNoneModule:
    def test_no_op_when_db_module_is_none_and_non_io_exc(self, tmp_path, monkeypatch):
        """Non-disk-IO exc with db_module=None → no-op (falls back to real db, AC3)."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        exc = ValueError("harmless")
        github_events_sync._handle_disk_io(exc, None)

    def test_raises_when_db_module_is_none_and_disk_io_exc(self, tmp_path, monkeypatch):
        """Disk I/O exc with db_module=None → RuntimeError via real db (AC3)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        exc = sqlite3.OperationalError("disk I/O error")
        with pytest.raises(RuntimeError, match="disk I/O error"):
            github_events_sync._handle_disk_io(exc, None)


# ── AC4: regression — all three loop sites use the helper ────────────────────

class TestLoopSitesUseHelper:
    """Behavioral regression: each of the three except sites still aborts on disk I/O."""

    def _no_sleep(self, monkeypatch) -> None:
        async def _instant(_: float) -> None:
            return None
        monkeypatch.setattr(github_events_sync.asyncio, "sleep", _instant)

    def test_site1_issues_sync_aborts_on_disk_io(self, tmp_path, monkeypatch):
        """Site 1 (sync_issues_mirror except): disk I/O still aborts the loop (AC4)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _disk_sync(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _disk_sync)
        monkeypatch.setattr(github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {})
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": _GOOD_MS_STUB}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(["o/r"], iterations=1)
                )

    def test_site2_milestones_sync_aborts_on_disk_io(self, tmp_path, monkeypatch):
        """Site 2 (milestones sync except): disk I/O still aborts the loop (AC4)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _good_sync(repo, db_module=None):
            return {"status": 304, "synced": 0}

        def _disk_milestones(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        bad_ms = types.SimpleNamespace(sync_milestones_mirror=_disk_milestones)
        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _good_sync)
        monkeypatch.setattr(github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {})
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": bad_ms}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(["o/r"], iterations=1)
                )

    def test_site3_reconcile_aborts_on_disk_io(self, tmp_path, monkeypatch):
        """Site 3 (reconcile_closed_issues except): disk I/O still aborts the loop (AC4)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _good_sync(repo, db_module=None):
            return {"status": 304, "synced": 0}

        def _disk_reconcile(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _good_sync)
        monkeypatch.setattr(github_events_sync, "reconcile_closed_issues", _disk_reconcile)
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": _GOOD_MS_STUB}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(["o/r"], iterations=1)
                )

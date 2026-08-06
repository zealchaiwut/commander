"""Tests for issue #1903: Restore precise return type annotation on db.get_conn().

The #1749 refactor converted get_conn() to a @contextlib.contextmanager but
dropped the return annotation. This ticket adds -> Iterator[sqlite3.Connection].

AC coverage:
  AC1 — get_conn().__wrapped__ (the underlying generator) carries a return
         annotation of Iterator[sqlite3.Connection], accessible via
         typing.get_type_hints().
  AC2 — typing.Iterator is present in the db module namespace (i.e. the
         'from typing import Iterator' import was added).
  AC3 — The annotation does not break get_conn() behavior: the context
         manager still yields a live sqlite3.Connection and closes it on exit.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
import typing
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reload_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import apps.dashboard.db as db_module
    importlib.reload(db_module)
    return db_module


# ---------------------------------------------------------------------------
# AC1 — get_conn has return annotation Iterator[sqlite3.Connection]
# ---------------------------------------------------------------------------

class TestAC1ReturnAnnotation:
    """AC1: get_conn().__wrapped__ annotated as Iterator[sqlite3.Connection]."""

    def test_get_conn_wrapped_has_return_annotation(self, tmp_path, monkeypatch):
        """get_conn.__wrapped__ must have a 'return' key in its annotations."""
        db = _reload_db(monkeypatch, tmp_path)
        assert hasattr(db.get_conn, "__wrapped__"), (
            "get_conn() must be a @contextlib.contextmanager (has __wrapped__)"
        )
        hints = typing.get_type_hints(db.get_conn.__wrapped__)
        assert "return" in hints, (
            "get_conn.__wrapped__ must have a return annotation"
        )

    def test_get_conn_return_annotation_is_iterator_of_connection(self, tmp_path, monkeypatch):
        """Return annotation must be Iterator[sqlite3.Connection]."""
        db = _reload_db(monkeypatch, tmp_path)
        hints = typing.get_type_hints(db.get_conn.__wrapped__)
        ret = hints["return"]
        origin = typing.get_origin(ret)
        args = typing.get_args(ret)
        # The origin of Iterator[X] is collections.abc.Iterator
        import collections.abc
        assert origin is collections.abc.Iterator, (
            f"Expected Iterator as origin, got {origin!r}. "
            "Annotate get_conn() as -> Iterator[sqlite3.Connection]"
        )
        assert args == (sqlite3.Connection,), (
            f"Expected Iterator[sqlite3.Connection], got args={args!r}."
        )

    def test_get_conn_outer_also_carries_annotation(self, tmp_path, monkeypatch):
        """functools.wraps copies __annotations__, so the outer function also has it."""
        db = _reload_db(monkeypatch, tmp_path)
        assert "return" in db.get_conn.__annotations__, (
            "The @contextmanager-wrapped get_conn must expose 'return' in __annotations__"
        )


# ---------------------------------------------------------------------------
# AC2 — Iterator is importable from the db module namespace
# ---------------------------------------------------------------------------

class TestAC2IteratorImported:
    """AC2: typing.Iterator is present in db's module namespace."""

    def test_iterator_in_db_module(self, tmp_path, monkeypatch):
        """db module must export Iterator (from typing import Iterator)."""
        db = _reload_db(monkeypatch, tmp_path)
        assert hasattr(db, "Iterator"), (
            "db.py must contain 'from typing import Iterator'"
        )
        import collections.abc
        # typing.Iterator and collections.abc.Iterator are the same at runtime in 3.9+
        assert db.Iterator is typing.Iterator or db.Iterator is collections.abc.Iterator, (
            "db.Iterator must be typing.Iterator (or collections.abc.Iterator)"
        )


# ---------------------------------------------------------------------------
# AC3 — Annotation does not break get_conn() behavior
# ---------------------------------------------------------------------------

class TestAC3BehaviorUnchanged:
    """AC3: get_conn() still works correctly after the annotation is added."""

    def test_get_conn_yields_sqlite_connection(self, tmp_path, monkeypatch):
        """get_conn() must yield a live sqlite3.Connection inside the with-block."""
        db = _reload_db(monkeypatch, tmp_path)
        with db.get_conn() as conn:
            assert isinstance(conn, sqlite3.Connection)
            row = conn.execute("SELECT 1 AS v").fetchone()
            assert row["v"] == 1

    def test_connection_closed_after_with_block(self, tmp_path, monkeypatch):
        """Connection must be closed when the with-block exits."""
        db = _reload_db(monkeypatch, tmp_path)
        captured = []
        with db.get_conn() as conn:
            captured.append(conn)
        with pytest.raises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

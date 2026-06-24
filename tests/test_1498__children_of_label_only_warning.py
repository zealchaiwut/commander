"""Tests for issue #1498: children_of label-only fallback must emit a warning.

AC — children_of() called without a project argument must emit a logger.warning
     on the "startup" logger containing "label-only fallback" or "without project",
     mirroring the warning already present in db.get_sprint_children (issue #1464 AC4).
     No warning is emitted when project is supplied.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import db  # noqa: E402

_COMMANDER = "zealchaiwut/commander"
_BASE = "sprint-77"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Fresh SQLite DB per test — no shared state."""
    db_file = tmp_path / "test_1498.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield
    db.DB_PATH = original


def _insert_sprint(label: str, project: str, parent_label: str | None = None) -> None:
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, parent_label)"
            " VALUES (?, ?, 'running', '2026-01-01T00:00:00Z', ?)",
            (label, project, parent_label),
        )
        conn.commit()


class TestChildrenOfLabelOnlyFallbackWarning:
    """children_of emits a warning when called without project (label-only fallback)."""

    def test_warns_when_no_project_supplied(self, caplog):
        """Calling children_of without project logs a WARNING on the startup logger."""
        _insert_sprint("sprint-77.1", _COMMANDER, parent_label=_BASE)
        import server as srv  # noqa: PLC0415

        with caplog.at_level(logging.WARNING, logger="startup"):
            srv.children_of(_BASE)

        assert any(
            "label-only fallback" in r.message or "without project" in r.message
            for r in caplog.records
        ), (
            "Expected a warning about label-only fallback when children_of is called "
            f"without project; records: {[r.message for r in caplog.records]}"
        )

    def test_no_warning_when_project_supplied(self, caplog):
        """Calling children_of with a project does NOT emit a label-only fallback warning."""
        _insert_sprint("sprint-77.1", _COMMANDER, parent_label=_BASE)
        import server as srv  # noqa: PLC0415

        with caplog.at_level(logging.WARNING, logger="startup"):
            srv.children_of(_BASE, project=_COMMANDER)

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any(
            "label-only fallback" in m or "without project" in m
            for m in warning_messages
        ), (
            "No fallback warning expected when project is supplied; "
            f"got: {warning_messages}"
        )

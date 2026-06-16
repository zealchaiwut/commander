"""P1: Merge Sprint persists develop PR number to sprints row."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "merge_pr.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    if "db" in sys.modules:
        del sys.modules["db"]
    import db as db_module
    db_module.init_db()
    return db_module


def test_update_sprint_pr_number(fresh_db):
    fresh_db.record_sprint_start("sprint-79", project="o/r")
    fresh_db.record_sprint_finish("sprint-79", end_reason="merge_sprint", project="o/r")
    fresh_db.update_sprint_pr_number("sprint-79", 1159)
    row = fresh_db.get_sprint("sprint-79")
    assert row is not None
    assert row["pr_number"] == 1159

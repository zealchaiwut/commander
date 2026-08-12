"""AC2 for issue #2231: todo_repo fallback store is redirected away from production during tests.

The _isolate_todo_repo fixture in conftest.py must monkeypatch
todo_repo._fallback_store_path to return a temp path so that running
the test suite never touches .commander/project_todos_store.json.
"""
from __future__ import annotations

from pathlib import Path

import services.sprint_manager.todo_repo as todo_repo
from services.sprint_manager.commander_paths import discover_commander_dir


def test_store_path_not_production():
    """AC2: _fallback_store_path() does not resolve to the production .commander path."""
    resolved = todo_repo._fallback_store_path()
    prod_commander = discover_commander_dir(Path(todo_repo.__file__).resolve())
    prod_store = prod_commander / "project_todos_store.json"
    assert resolved != prod_store, (
        f"todo_repo._fallback_store_path() returned the production path {prod_store}. "
        "The _isolate_todo_repo fixture (tests/conftest.py) must redirect it to tmp_path."
    )

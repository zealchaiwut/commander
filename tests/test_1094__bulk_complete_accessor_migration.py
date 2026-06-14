"""Tests for bulk-complete migration to sprint_state.current() accessor (issue #1094)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent


@contextmanager
def _stack(patches):
    [p.start() for p in patches]
    try:
        yield
    finally:
        for p in patches:
            p.stop()


def _import_server():
    import sys

    dash = REPO_ROOT / "apps" / "dashboard"
    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))
    import server as srv
    return srv


@pytest.fixture
def srv():
    return _import_server()


def test_bulk_complete_child_state_uses_accessor(srv):
    """_bulk_complete_child_state must use sprint_state.current(), not inline reads."""
    with patch("server.sprint_state.current", return_value="completed") as mock_accessor:
        result = srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    assert result == "completed"
    mock_accessor.assert_called_once_with("sprint-68.1")


def test_bulk_complete_child_state_ready_states_check_uses_accessor_result(srv):
    """Ready-states membership check must use the accessor result."""
    with patch("server.sprint_state.current", return_value="ready_to_merge"):
        result = srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    assert result == "ready_to_merge"
    assert result in srv._BULK_COMPLETE_CHILD_READY_STATES


def test_bulk_complete_child_state_nonready_accessor_result(srv):
    """Non-ready states from accessor are returned as-is."""
    with patch("server.sprint_state.current", return_value="needs_rework"):
        result = srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    assert result == "needs_rework"
    assert result not in srv._BULK_COMPLETE_CHILD_READY_STATES


def test_bulk_complete_child_state_no_direct_db_query(srv):
    """Function must not call db.get_sprint() directly."""
    with patch("server.sprint_state.current", return_value="completed"), \
         patch("server.db.get_sprint") as mock_db_get, \
         patch("server._read_plan_json", return_value=None):
        srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    # db.get_sprint should NOT be called directly; accessor handles it
    mock_db_get.assert_not_called()


def test_bulk_complete_child_state_no_plan_json_read(srv):
    """Function must not read plan.json directly."""
    with patch("server.sprint_state.current", return_value="completed"), \
         patch("server._read_plan_json") as mock_plan_read:
        srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    # _read_plan_json should NOT be called; accessor is the only source
    mock_plan_read.assert_not_called()


def test_bulk_complete_child_state_accessor_unknown_handling(srv):
    """When accessor returns 'unknown', behavior is preserved."""
    with patch("server.sprint_state.current", return_value="unknown"):
        result = srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    assert result == "unknown"


def test_bulk_complete_child_state_in_lineage_settled_uses_accessor(srv):
    """_bulk_complete_lineage_settled must use refactored child-state function."""
    with patch("server.sprint_state.current", return_value="completed"), \
         patch("server._commander_dir", return_value=Path("/fake")):
        result = srv._bulk_complete_lineage_settled(Path("/tmp"), "sprint-68.1")

    assert result is True


def test_bulk_complete_child_state_deleted_is_ready(srv):
    """'deleted' state in ready-states membership must work."""
    with patch("server.sprint_state.current", return_value="deleted"):
        result = srv._bulk_complete_child_state(Path("/tmp"), "sprint-68.1")

    assert result == "deleted"
    assert result in srv._BULK_COMPLETE_CHILD_READY_STATES

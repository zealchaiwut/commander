"""Tests for issue #1826: Guard fire-and-forget board_invalidated broadcast against unhandled exceptions

Tests the _guarded_broadcast helper that wraps SSE broadcasts with exception handling.
Issue #1826 adds a guard against unhandled asyncio task exceptions when broadcasting
board_invalidated events. Previously, if broadcast() raised an exception, it would
surface only as an unretrieved-task warning at GC time with no context. Now it's
caught and logged via logger.warning.
"""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

# Add the apps/dashboard directory to sys.path
# board_cache is in apps/dashboard/routers/board_cache.py
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_DIR = _DASHBOARD_ROOT / "routers"
for p in [str(_DASHBOARD_ROOT), str(_ROUTERS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def board_cache():
    """Import and reload board_cache module for each test."""
    import importlib
    from importlib import import_module
    spec = importlib.util.spec_from_file_location(
        "board_cache",
        _DASHBOARD_ROOT / "routers" / "board_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_guarded_broadcast__success(caplog, board_cache):
    """AC: _guarded_broadcast awaits broadcast_fn and returns on success."""
    caplog.set_level(logging.WARNING)
    mock_broadcast = AsyncMock()
    payload = {"type": "board_invalidated", "project": "owner/repo"}

    await board_cache._guarded_broadcast(mock_broadcast, payload)

    mock_broadcast.assert_called_once_with(payload)
    # No warning logged on success
    assert not any("broadcast failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_guarded_broadcast__catches_exception(caplog, board_cache):
    """AC: _guarded_broadcast catches exceptions from broadcast_fn and logs with logger.warning."""
    caplog.set_level(logging.WARNING)
    mock_broadcast = AsyncMock(side_effect=ValueError("test error"))
    payload = {"type": "board_invalidated", "project": "owner/repo"}

    # Should not raise
    await board_cache._guarded_broadcast(mock_broadcast, payload)

    mock_broadcast.assert_called_once_with(payload)
    # Should log warning with project and exception
    assert any(
        "board_invalidated broadcast failed" in record.message and "owner/repo" in record.message
        for record in caplog.records
    ), f"Expected warning not found. Logs: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_guarded_broadcast__logs_project_and_exception_details(caplog, board_cache):
    """AC: _guarded_broadcast logs include project name and exception details."""
    caplog.set_level(logging.WARNING)
    test_error = RuntimeError("network timeout")
    mock_broadcast = AsyncMock(side_effect=test_error)
    payload = {"type": "board_invalidated", "project": "test-org/test-repo"}

    await board_cache._guarded_broadcast(mock_broadcast, payload)

    # Find the warning log record
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_records) > 0, f"No warning logged. Records: {caplog.records}"
    log_message = warning_records[0].message
    assert "test-org/test-repo" in log_message
    # The exception class name or message should be in the log
    assert "RuntimeError" in log_message or "network timeout" in log_message


@pytest.mark.asyncio
async def test_guarded_broadcast__handles_any_exception_type(caplog, board_cache):
    """AC: _guarded_broadcast handles any Exception subclass, not just ValueError."""
    caplog.set_level(logging.WARNING)

    class CustomError(Exception):
        pass

    mock_broadcast = AsyncMock(side_effect=CustomError("custom problem"))
    payload = {"type": "board_invalidated", "project": "org/repo"}

    await board_cache._guarded_broadcast(mock_broadcast, payload)

    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_records) > 0
    assert "org/repo" in warning_records[0].message


def test_invalidate_board__clears_cache(board_cache):
    """AC: invalidate_board clears the cache entry for the project."""
    # Manually set cache entry
    board_cache._cache["owner/repo"] = {
        "snapshot": {"issues": []},
        "expires_at": 9999.0
    }

    # Mock set_main_loop to None to skip broadcast logic
    board_cache.set_main_loop(None)

    # Call invalidate_board
    board_cache.invalidate_board("owner/repo")

    # Cache should be cleared
    assert "owner/repo" not in board_cache._cache


def test_invalidate_board__broadcasts_when_no_exception(board_cache):
    """AC: invalidate_board schedules broadcast via create_task when in async context."""
    # Mock _guarded_broadcast at module level to track calls
    called_args = []

    async def mock_guarded_broadcast(broadcast_fn, payload):
        called_args.append({"broadcast_fn": broadcast_fn, "payload": payload})

    with patch.object(board_cache, "_guarded_broadcast", side_effect=mock_guarded_broadcast):
        # Patch the dynamic import by mocking the logs_service module
        mock_bc = AsyncMock()
        with patch.dict('sys.modules', {'logs_service': type('module', (), {'broadcast': mock_bc})()} ):
            async def run_test():
                board_cache.invalidate_board("org/repo")
                # Yield to let event loop process the created task
                await asyncio.sleep(0.01)

            asyncio.run(run_test())

            # Verify _guarded_broadcast was scheduled
            assert len(called_args) > 0
            assert called_args[0]["payload"]["project"] == "org/repo"
            assert called_args[0]["payload"]["type"] == "board_invalidated"

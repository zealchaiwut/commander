"""Tests for board_cache broadcast error guard — issue #1826.

AC coverage:
  AC1  _guarded_broadcast catches a raising broadcast and calls logger.warning
       — does NOT re-raise and does NOT silently swallow (warning is emitted).
  AC2  The warning message includes the project name for context.
  AC3  invalidate_board in an async context schedules a guarded task; when
       broadcast raises, logger.warning is called — not an unhandled asyncio
       task exception at GC time.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

# ── Path setup ───────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board_cache as _bc  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _raising_broadcast(payload: dict) -> None:
    raise RuntimeError("SSE bus closed")


async def _ok_broadcast(payload: dict) -> None:
    pass


# ── AC1: _guarded_broadcast catches exception and emits warning ──────────────

class TestGuardedBroadcastCatchesException:
    def test_guarded_broadcast_does_not_raise(self):
        """_guarded_broadcast must not propagate broadcast exceptions."""
        asyncio.run(
            _bc._guarded_broadcast(
                _raising_broadcast,
                {"type": "board_invalidated", "project": "owner/repo"},
            )
        )

    def test_guarded_broadcast_calls_logger_warning_on_failure(self):
        """When broadcast raises, logger.warning must be called."""
        with patch.object(_bc.logger, "warning") as mock_warn:
            asyncio.run(
                _bc._guarded_broadcast(
                    _raising_broadcast,
                    {"type": "board_invalidated", "project": "owner/repo"},
                )
            )
        assert mock_warn.called, (
            "logger.warning must be called when broadcast raises"
        )

    def test_guarded_broadcast_does_not_warn_on_success(self):
        """When broadcast succeeds, logger.warning must NOT be called."""
        with patch.object(_bc.logger, "warning") as mock_warn:
            asyncio.run(
                _bc._guarded_broadcast(
                    _ok_broadcast,
                    {"type": "board_invalidated", "project": "owner/repo"},
                )
            )
        assert not mock_warn.called, (
            "logger.warning must not be called on success"
        )


# ── AC2: warning message includes project name for context ───────────────────

class TestGuardedBroadcastWarningContext:
    def test_warning_includes_project_name(self):
        """The logged warning must include the project name for context."""
        project = "owner/my-repo"
        warning_args: list = []

        def capture_warning(*args, **kwargs):
            warning_args.extend(args)

        with patch.object(_bc.logger, "warning", side_effect=capture_warning):
            asyncio.run(
                _bc._guarded_broadcast(
                    _raising_broadcast,
                    {"type": "board_invalidated", "project": project},
                )
            )

        assert warning_args, "logger.warning was not called"
        full_message = " ".join(str(a) for a in warning_args)
        assert project in full_message, (
            f"project name '{project}' must appear in warning;"
            f" got: {full_message!r}"
        )

    def test_warning_includes_exception_details(self):
        """The logged warning must include the exception message."""
        error_text = "SSE bus closed"

        async def raising(payload: dict) -> None:
            raise RuntimeError(error_text)

        captured: list = []

        def capture_warning(*args, **kwargs):
            captured.extend(args)

        with patch.object(_bc.logger, "warning", side_effect=capture_warning):
            asyncio.run(
                _bc._guarded_broadcast(
                    raising,
                    {"type": "board_invalidated", "project": "owner/repo"},
                )
            )

        full_message = " ".join(str(a) for a in captured)
        assert error_text in full_message or any(
            error_text in str(a) for a in captured
        ), f"exception text must appear in warning; got: {full_message!r}"


# ── AC3: invalidate_board uses guarded task in async context ─────────────────

class TestInvalidateBoardUsesGuard:
    def test_broadcast_exception_does_not_propagate(self):
        """invalidate_board must not raise even if broadcast raises."""
        _bc._cache.clear()
        _bc._cache["owner/repo"] = {"snapshot": {}, "expires_at": 1e18}

        async def _test():
            async def bad_broadcast(payload):
                raise RuntimeError("broadcast failed")

            import types
            import sys as _sys
            fake_logs = types.ModuleType("logs_service")
            fake_logs.broadcast = bad_broadcast  # type: ignore[attr-defined]

            old = _sys.modules.get("logs_service")
            _sys.modules["logs_service"] = fake_logs
            try:
                with patch.object(_bc.logger, "warning") as mock_warn:
                    _bc.invalidate_board("owner/repo")
                    # Drain scheduled tasks so the guarded coroutine runs.
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                assert mock_warn.called, (
                    "logger.warning should fire when broadcast raises"
                )
            finally:
                if old is None:
                    _sys.modules.pop("logs_service", None)
                else:
                    _sys.modules["logs_service"] = old

        asyncio.run(_test())

    def test_warning_includes_project_name_in_async_path(self):
        """Warning logged via guarded task must include the project name."""
        project = "owner/guard-test-repo"
        _bc._cache[project] = {"snapshot": {}, "expires_at": 1e18}

        async def _test():
            async def bad_broadcast(payload):
                raise RuntimeError("error")

            import types
            import sys as _sys
            fake_logs = types.ModuleType("logs_service")
            fake_logs.broadcast = bad_broadcast  # type: ignore[attr-defined]

            old = _sys.modules.get("logs_service")
            _sys.modules["logs_service"] = fake_logs
            captured: list = []

            def capture(*args, **kwargs):
                captured.extend(args)

            try:
                with patch.object(_bc.logger, "warning", side_effect=capture):
                    _bc.invalidate_board(project)
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
            finally:
                if old is None:
                    _sys.modules.pop("logs_service", None)
                else:
                    _sys.modules["logs_service"] = old

            full = " ".join(str(a) for a in captured)
            assert project in full, (
                f"project name must appear in warning; got: {full!r}"
            )

        asyncio.run(_test())

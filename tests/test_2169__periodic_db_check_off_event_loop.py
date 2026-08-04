"""Behavioral test for issue #2169: _periodic_db_integrity_loop must dispatch
alert_if_corrupt via asyncio.to_thread, not synchronously on the event loop.

AC: PRAGMA quick_check must not block the event loop — it must run in a thread
via asyncio.to_thread so large DBs (~88 MB) do not stall the event loop for
the duration of the check every 30 minutes.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import os
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2169.db")


class TestPeriodicDbLoopUsesThread:
    """_periodic_db_integrity_loop dispatches alert_if_corrupt via asyncio.to_thread."""

    def test_alert_if_corrupt_dispatched_via_to_thread(self):
        """alert_if_corrupt must be called via asyncio.to_thread, not directly.

        Runs _periodic_db_integrity_loop for exactly one check cycle and asserts
        that asyncio.to_thread was invoked with db.alert_if_corrupt as the callable.
        Before the fix (synchronous call), to_thread is never called and this test
        fails — proving it is anchored to the implementation, not the source text.
        """
        from server import _periodic_db_integrity_loop

        to_thread_calls: list = []

        async def _to_thread_spy(fn, *args, **kwargs):
            to_thread_calls.append(fn)
            return fn(*args, **kwargs)

        sleep_count = [0]

        async def _sleep_stub(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                raise asyncio.CancelledError()

        captured_mock: list = [None]

        async def _run():
            with (
                patch("server.db.alert_if_corrupt", return_value="ok") as m,
                patch("server.asyncio.to_thread", new=_to_thread_spy),
                patch("server.asyncio.sleep", new=_sleep_stub),
            ):
                captured_mock[0] = m
                try:
                    await _periodic_db_integrity_loop()
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run())

        assert to_thread_calls, (
            "asyncio.to_thread was never called inside _periodic_db_integrity_loop — "
            "alert_if_corrupt appears to run synchronously on the event loop, "
            "blocking it for the duration of PRAGMA quick_check (issue #2169)"
        )
        assert captured_mock[0] in to_thread_calls, (
            f"asyncio.to_thread was called but not with db.alert_if_corrupt. "
            f"Got: {to_thread_calls!r}"
        )

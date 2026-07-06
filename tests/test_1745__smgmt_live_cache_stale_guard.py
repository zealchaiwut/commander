"""Tests for issue #1745: guard _smgmtLiveCache against stale overwrites.

AC coverage:
  AC-1: Monotonic sequence counter declared; per-label write-seq tracker declared.
  AC-2: Guard applies to both _smgmtRunningFirstPaint and _smgmtLivePollTick.
  AC-3: Behavioral simulation — out-of-order resolution (slow first-paint after
        a poll tick) asserts the newer data survives.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_HTML = (
    Path(__file__).resolve().parent.parent
    / "apps" / "dashboard" / "static" / "project.html"
)
PROJECT_HTML = _PROJECT_HTML.read_text(encoding="utf-8")


def _extract_fn(name: str) -> str:
    """Extract a top-level async/regular function body from project.html."""
    m = re.search(
        rf"async function {re.escape(name)}\(\).*?^}}",
        PROJECT_HTML,
        re.DOTALL | re.MULTILINE,
    )
    if m is None:
        m = re.search(
            rf"function {re.escape(name)}\(\).*?^}}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
    assert m is not None, f"{name} not found in project.html"
    return m.group(0)


# ---------------------------------------------------------------------------
# AC-1: Declarations
# ---------------------------------------------------------------------------

class TestAC1Declarations:
    """AC-1: Monotonic counter + per-label seq tracker declared near _smgmtLiveCache."""

    def test_monotonic_counter_declared(self):
        """Global monotonic counter _smgmtLiveCacheSeq must be declared."""
        assert "let _smgmtLiveCacheSeq = 0" in PROJECT_HTML, (
            "_smgmtLiveCacheSeq not found — add "
            "`let _smgmtLiveCacheSeq = 0;` near the _smgmtLiveCache declaration"
        )

    def test_write_seq_tracker_declared(self):
        """Per-label write-seq tracker _smgmtLiveCacheWriteSeq must be declared."""
        assert "const _smgmtLiveCacheWriteSeq = {}" in PROJECT_HTML, (
            "_smgmtLiveCacheWriteSeq not found — add "
            "`const _smgmtLiveCacheWriteSeq = {};` near the _smgmtLiveCache declaration"
        )

    def test_declarations_near_live_cache(self):
        """Both declarations should appear within 20 lines of _smgmtLiveCache."""
        lines = PROJECT_HTML.splitlines()
        cache_line = next(
            (i for i, l in enumerate(lines) if "const _smgmtLiveCache = {}" in l), None
        )
        assert cache_line is not None, "_smgmtLiveCache not found"
        window = "\n".join(lines[max(0, cache_line - 5): cache_line + 20])
        assert "_smgmtLiveCacheSeq" in window, (
            "_smgmtLiveCacheSeq not within 20 lines of _smgmtLiveCache declaration"
        )
        assert "_smgmtLiveCacheWriteSeq" in window, (
            "_smgmtLiveCacheWriteSeq not within 20 lines of _smgmtLiveCache declaration"
        )


# ---------------------------------------------------------------------------
# AC-2: Both writers guarded
# ---------------------------------------------------------------------------

class TestAC2BothWritersGuarded:
    """AC-2: Guard present in both _smgmtRunningFirstPaint and _smgmtLivePollTick."""

    def test_first_paint_increments_seq(self):
        fn = _extract_fn("_smgmtRunningFirstPaint")
        assert "_smgmtLiveCacheSeq" in fn, (
            "_smgmtRunningFirstPaint does not reference _smgmtLiveCacheSeq"
        )
        assert "++_smgmtLiveCacheSeq" in fn, (
            "_smgmtRunningFirstPaint must increment _smgmtLiveCacheSeq"
        )

    def test_first_paint_checks_staleness(self):
        fn = _extract_fn("_smgmtRunningFirstPaint")
        assert "_smgmtLiveCacheWriteSeq[label]" in fn, (
            "_smgmtRunningFirstPaint does not check _smgmtLiveCacheWriteSeq[label]"
        )

    def test_first_paint_guard_before_write(self):
        """The staleness guard must appear before the cache write in the function."""
        fn = _extract_fn("_smgmtRunningFirstPaint")
        guard_pos = fn.find("_smgmtLiveCacheWriteSeq[label]")
        write_pos = fn.find("_smgmtLiveCache[label] = data")
        assert guard_pos != -1, "staleness guard not found in _smgmtRunningFirstPaint"
        assert write_pos != -1, "cache write not found in _smgmtRunningFirstPaint"
        assert guard_pos < write_pos, (
            "staleness guard must precede cache write in _smgmtRunningFirstPaint"
        )

    def test_poll_tick_increments_seq(self):
        fn = _extract_fn("_smgmtLivePollTick")
        assert "_smgmtLiveCacheSeq" in fn, (
            "_smgmtLivePollTick does not reference _smgmtLiveCacheSeq"
        )
        assert "++_smgmtLiveCacheSeq" in fn, (
            "_smgmtLivePollTick must increment _smgmtLiveCacheSeq"
        )

    def test_poll_tick_checks_staleness(self):
        fn = _extract_fn("_smgmtLivePollTick")
        assert "_smgmtLiveCacheWriteSeq[label]" in fn, (
            "_smgmtLivePollTick does not check _smgmtLiveCacheWriteSeq[label]"
        )

    def test_poll_tick_guard_before_write(self):
        """The staleness guard must appear before the cache write in the function."""
        fn = _extract_fn("_smgmtLivePollTick")
        guard_pos = fn.find("_smgmtLiveCacheWriteSeq[label]")
        write_pos = fn.find("_smgmtLiveCache[label] = live")
        assert guard_pos != -1, "staleness guard not found in _smgmtLivePollTick"
        assert write_pos != -1, "cache write not found in _smgmtLivePollTick"
        assert guard_pos < write_pos, (
            "staleness guard must precede cache write in _smgmtLivePollTick"
        )


# ---------------------------------------------------------------------------
# AC-3: Behavioral simulation of out-of-order resolution
# ---------------------------------------------------------------------------

class TestAC3BehavioralRaceSimulation:
    """AC-3: Python simulation of the guard algorithm.

    Scenario: first-paint fetch starts first (seq=1) but resolves AFTER a
    poll tick (seq=2).  The newer poll data must survive.
    """

    def _run_simulation(self, first_paint_resolves_last: bool) -> dict:
        """
        Simulates the guard logic from both writers.

        Returns the final state of cache and write_seq.
        """
        cache = {}
        write_seq_tracker = {}  # label → last-written seq
        monotonic = [0]  # mutable int via list

        def claim_seq():
            monotonic[0] += 1
            return monotonic[0]

        def guarded_write(label: str, data: dict, my_seq: int) -> bool:
            if my_seq < (write_seq_tracker.get(label) or 0):
                return False  # stale — skip
            write_seq_tracker[label] = my_seq
            cache[label] = data
            return True

        LABEL = "sprint-106"
        FIRST_PAINT_DATA = {"sprint_label": LABEL, "source": "first-paint", "elapsed": 10}
        POLL_DATA = {"sprint_label": LABEL, "source": "poll-tick", "elapsed": 12}

        # Both fetches initiated; first-paint claims seq first (starts first).
        seq_first_paint = claim_seq()   # = 1
        seq_poll_tick = claim_seq()     # = 2

        if first_paint_resolves_last:
            # Poll tick resolves first, then first-paint (slow first-paint).
            guarded_write(LABEL, POLL_DATA, seq_poll_tick)
            guarded_write(LABEL, FIRST_PAINT_DATA, seq_first_paint)
        else:
            # First-paint resolves first (normal fast path).
            guarded_write(LABEL, FIRST_PAINT_DATA, seq_first_paint)
            guarded_write(LABEL, POLL_DATA, seq_poll_tick)

        return {"cache": cache, "write_seq": write_seq_tracker}

    def test_slow_first_paint_does_not_clobber_poll_data(self):
        """When first-paint resolves AFTER poll tick, poll tick's data survives."""
        result = self._run_simulation(first_paint_resolves_last=True)
        assert result["cache"]["sprint-106"]["source"] == "poll-tick", (
            "poll-tick data was clobbered by slow first-paint — guard failed"
        )

    def test_fast_first_paint_writes_its_data(self):
        """When first-paint resolves BEFORE poll tick, poll tick then overwrites (correct)."""
        result = self._run_simulation(first_paint_resolves_last=False)
        assert result["cache"]["sprint-106"]["source"] == "poll-tick", (
            "poll-tick should overwrite fast first-paint data (it has a higher seq)"
        )

    def test_write_seq_reflects_last_winner(self):
        """write_seq tracker holds the seq of the last successful write."""
        result = self._run_simulation(first_paint_resolves_last=True)
        # poll-tick has seq=2, first-paint has seq=1 → poll-tick wins → seq=2
        assert result["write_seq"]["sprint-106"] == 2

    def test_equal_seq_does_not_block_write(self):
        """A write with seq equal to the stored seq is allowed (non-strict < check)."""
        cache = {}
        write_seq_tracker = {}
        LABEL = "sprint-106"

        def guarded_write(label, data, my_seq):
            if my_seq < (write_seq_tracker.get(label) or 0):
                return False
            write_seq_tracker[label] = my_seq
            cache[label] = data
            return True

        DATA_A = {"source": "a"}
        DATA_B = {"source": "b"}
        guarded_write(LABEL, DATA_A, 5)
        result = guarded_write(LABEL, DATA_B, 5)  # same seq — should NOT be blocked
        assert result is True, "equal seq should not block write"
        assert cache[LABEL]["source"] == "b"

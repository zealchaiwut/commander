"""A transient merge-conflict race gets a free retry that doesn't burn the
code-fix attempt budget (the #1053 saga: 3 attempts all lost to rebase races
vs #1049 while #1053's own tests passed).

Exercises the StageResult.RETRY_FREE handling in both schedulers
(pipeline._run_serial and pipeline._run_pipeline).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.pipeline import (  # noqa: E402
    StageResult, _run_serial, _run_pipeline,
)


def _code_pass(ticket, attempt):
    return StageResult.PASS


def _make_test_fn(free_count: int):
    """tester returns RETRY_FREE `free_count` times (races), then PASS."""
    seen = {"n": 0}

    def _test_fn(ticket, attempt):
        seen["n"] += 1
        if seen["n"] <= free_count:
            return StageResult.RETRY_FREE
        return StageResult.PASS

    return _test_fn


def test_serial_retry_free_does_not_count_attempts():
    """Two RETRY_FREE races then PASS → merged, attempt count stays at 1
    (max_attempts=3 not exhausted by the transient conflicts)."""
    res = _run_serial(
        ["#1"], _code_pass, _make_test_fn(2),
        max_attempts=3, on_merged=None, on_needs_rework=None, on_dropped=None,
    )
    assert "#1" in res.merged
    assert "#1" not in res.needs_rework
    assert res.attempts["#1"] == 1


def test_pipeline_retry_free_does_not_count_attempts():
    """Same, through the threaded pipeline scheduler."""
    res = _run_pipeline(
        ["#1"], _code_pass, _make_test_fn(2),
        max_attempts=3, on_merged=None, on_needs_rework=None, on_dropped=None,
    )
    assert "#1" in res.merged
    assert "#1" not in res.needs_rework
    assert res.attempts["#1"] == 1


def test_serial_real_rejects_still_exhaust():
    """Regression guard: genuine REJECTs (not RETRY_FREE) still count and
    exhaust at max_attempts → needs_rework."""
    def _always_reject(ticket, attempt):
        return StageResult.REJECT

    res = _run_serial(
        ["#1"], _code_pass, _always_reject,
        max_attempts=3, on_merged=None, on_needs_rework=None, on_dropped=None,
    )
    assert "#1" in res.needs_rework
    assert res.attempts["#1"] == 3

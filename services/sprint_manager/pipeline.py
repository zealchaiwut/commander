"""Concurrent two-stage dispatch pipeline for the sprint manager (issue #737).

This module owns the *opt-in* concurrent pipeline that lets one coder worker
build the next ticket while one tester worker validates the previous one,
roughly halving wall-clock time for independent tickets in the same dispatch
level. The feature is default-off; serial dispatch remains the canonical path.

Design contract (mirrors the issue's acceptance criteria):

  - Exactly one coder worker and one tester worker run at a time — never more.
  - The coder worker pulls from a coder queue; the tester worker pulls from a
    tester queue that is fed by coder completions.
  - A tester rejection pushes the ticket to the *front* of the coder queue.
  - Retries respect a cap (default 3 attempts). A ticket that exceeds the cap is
    marked needs-rework and dropped from both queues without blocking the rest.
  - One level is processed at a time. The caller iterates levels sequentially,
    so the hard level barrier (never advance until the current level finishes)
    is structural — `run_level` does not return until its level is drained.

Both serial and pipeline execution route through `run_level` and call the same
`code_fn` / `test_fn` stage callables in the same per-ticket order, so every
per-ticket side effect is identical between modes by construction. The only
difference is whether the two stages overlap across tickets.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

# Cap on coder attempts per ticket before it is dropped as needs-rework.
DEFAULT_MAX_ATTEMPTS = 3

# Kill-switch env var: when truthy, serial mode is forced regardless of any
# pipeline_mode setting value.
PIPELINE_KILL_SWITCH_ENV = "COMMANDER_PIPELINE_DISABLE"

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(raw: Any) -> bool:
    return str(raw).strip().lower() in _TRUTHY


class StageResult(Enum):
    """Outcome of a coder or tester stage for one ticket attempt."""

    PASS = "pass"        # coder: branch ready / tester: merged
    REJECT = "reject"    # tester only: send back to the coder queue
    FAIL = "fail"        # infra/non-retryable: drop, no rework label
    EXHAUST = "exhaust"  # tester only: consecutive identical failure — finalize needs-rework immediately


def pipeline_mode_enabled(
    sprint_setting: Optional[bool] = None,
    project_setting: Optional[bool] = None,
    env: Optional[dict] = None,
) -> bool:
    """Resolve the effective pipeline mode.

    Precedence (highest first):
      1. Kill-switch env var — when truthy, always returns False (force serial).
      2. Per-sprint setting, when not None.
      3. Per-project setting, when not None.
      4. Default: False (serial).
    """
    env = env if env is not None else os.environ
    if _is_truthy(env.get(PIPELINE_KILL_SWITCH_ENV, "")):
        return False
    if sprint_setting is not None:
        return bool(sprint_setting)
    if project_setting is not None:
        return bool(project_setting)
    return False


@dataclass
class LevelResult:
    """Outcome of processing a single dispatch level."""

    merged: list = field(default_factory=list)
    needs_rework: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    # Ordered record of completed stages: (ticket, "coder"|"tester", attempt).
    order: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)


def run_level(
    tickets: list,
    code_fn: Callable[[Any, int], StageResult],
    test_fn: Callable[[Any, int], StageResult],
    *,
    pipeline: bool,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_merged: Optional[Callable[[Any], None]] = None,
    on_needs_rework: Optional[Callable[[Any], None]] = None,
    on_dropped: Optional[Callable[[Any], None]] = None,
) -> LevelResult:
    """Process all tickets in one dispatch level and return a LevelResult.

    `code_fn(ticket, attempt)` runs the coder stage and returns
    StageResult.PASS (a SIT feature branch is ready) or StageResult.FAIL
    (infra/no-work — drop the ticket, no rework label).

    `test_fn(ticket, attempt)` runs the tester + gates and returns
    StageResult.PASS (merged), StageResult.REJECT (logic failure — re-queue to
    the coder), or StageResult.FAIL (infra — drop without rework).

    `attempt` is 1-based and counts coder runs for that ticket.

    When `pipeline` is False the two stages run strictly one ticket at a time.
    When True, one coder thread and one tester thread run concurrently, sharing
    a coder queue and a tester queue. Either way the per-ticket sequence of
    stage calls and callbacks is identical.
    """
    if pipeline:
        return _run_pipeline(
            tickets, code_fn, test_fn,
            max_attempts=max_attempts,
            on_merged=on_merged,
            on_needs_rework=on_needs_rework,
            on_dropped=on_dropped,
        )
    return _run_serial(
        tickets, code_fn, test_fn,
        max_attempts=max_attempts,
        on_merged=on_merged,
        on_needs_rework=on_needs_rework,
        on_dropped=on_dropped,
    )


def _run_serial(
    tickets, code_fn, test_fn, *,
    max_attempts, on_merged, on_needs_rework, on_dropped,
) -> LevelResult:
    result = LevelResult()
    attempts: dict = {t: 0 for t in tickets}
    result.attempts = attempts
    work: deque = deque(tickets)

    while work:
        ticket = work.popleft()
        attempts[ticket] += 1
        attempt = attempts[ticket]

        code_res = code_fn(ticket, attempt)
        result.order.append((ticket, "coder", attempt))
        if code_res is not StageResult.PASS:
            result.dropped.append(ticket)
            if on_dropped:
                on_dropped(ticket)
            continue

        test_res = test_fn(ticket, attempt)
        result.order.append((ticket, "tester", attempt))
        _apply_tester_outcome(
            ticket, attempt, test_res, result, work, max_attempts,
            requeue_front=True,
            on_merged=on_merged, on_needs_rework=on_needs_rework,
            on_dropped=on_dropped,
        )

    return result


def _apply_tester_outcome(
    ticket, attempt, test_res, result, coder_work, max_attempts,
    *, requeue_front, on_merged, on_needs_rework, on_dropped,
) -> None:
    """Shared tester-result handling for both serial and pipeline paths."""
    if test_res is StageResult.PASS:
        result.merged.append(ticket)
        if on_merged:
            on_merged(ticket)
    elif test_res is StageResult.EXHAUST:
        # Consecutive identical failure — finalize as needs-rework immediately.
        result.needs_rework.append(ticket)
        if on_needs_rework:
            on_needs_rework(ticket)
    elif test_res is StageResult.REJECT:
        if attempt >= max_attempts:
            # Cap reached — drop from both queues, label needs-rework.
            result.needs_rework.append(ticket)
            if on_needs_rework:
                on_needs_rework(ticket)
        else:
            # Push to the FRONT of the coder queue for a fix attempt.
            if requeue_front:
                coder_work.appendleft(ticket)
            else:
                coder_work.append(ticket)
    else:  # StageResult.FAIL
        result.dropped.append(ticket)
        if on_dropped:
            on_dropped(ticket)


def _run_pipeline(
    tickets, code_fn, test_fn, *,
    max_attempts, on_merged, on_needs_rework, on_dropped,
) -> LevelResult:
    result = LevelResult()
    attempts: dict = {t: 0 for t in tickets}
    result.attempts = attempts

    coder_q: deque = deque(tickets)
    tester_q: deque = deque()
    terminal: set = set()
    total = len(tickets)

    lock = threading.Lock()
    cond = threading.Condition(lock)

    def _finished() -> bool:
        return len(terminal) >= total

    def coder_loop() -> None:
        while True:
            with cond:
                while not coder_q and not _finished():
                    cond.wait()
                if _finished():
                    cond.notify_all()
                    return
                ticket = coder_q.popleft()
                attempts[ticket] += 1
                attempt = attempts[ticket]

            code_res = code_fn(ticket, attempt)

            with cond:
                result.order.append((ticket, "coder", attempt))
                if code_res is StageResult.PASS:
                    tester_q.append(ticket)
                else:
                    terminal.add(ticket)
                    result.dropped.append(ticket)
                    if on_dropped:
                        on_dropped(ticket)
                cond.notify_all()

    def tester_loop() -> None:
        while True:
            with cond:
                while not tester_q and not _finished():
                    cond.wait()
                if _finished():
                    cond.notify_all()
                    return
                ticket = tester_q.popleft()
                attempt = attempts[ticket]

            test_res = test_fn(ticket, attempt)

            with cond:
                result.order.append((ticket, "tester", attempt))
                if test_res is StageResult.PASS:
                    terminal.add(ticket)
                    result.merged.append(ticket)
                    if on_merged:
                        on_merged(ticket)
                elif test_res is StageResult.EXHAUST:
                    # Consecutive identical failure — finalize immediately.
                    terminal.add(ticket)
                    result.needs_rework.append(ticket)
                    if on_needs_rework:
                        on_needs_rework(ticket)
                elif test_res is StageResult.REJECT:
                    if attempt >= max_attempts:
                        terminal.add(ticket)
                        result.needs_rework.append(ticket)
                        if on_needs_rework:
                            on_needs_rework(ticket)
                    else:
                        coder_q.appendleft(ticket)  # front of coder queue
                else:  # FAIL
                    terminal.add(ticket)
                    result.dropped.append(ticket)
                    if on_dropped:
                        on_dropped(ticket)
                cond.notify_all()

    coder_t = threading.Thread(target=coder_loop, name="pipeline-coder")
    tester_t = threading.Thread(target=tester_loop, name="pipeline-tester")
    coder_t.start()
    tester_t.start()
    coder_t.join()
    tester_t.join()
    return result

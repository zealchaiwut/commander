"""Role-flexible concurrent slot scheduler (issue #1413, extends #1412).

Each worker slot can execute either a code_fn task or a test_fn task; the role
is determined by the task pulled from the queues, not by the slot. Workers
prefer code tasks (to keep the pipeline fed) and fall back to test tasks when
no eligible code task is available.

This enables natural load rebalancing across the sprint:
- Early sprint (all tickets unstarted): slots predominantly run code_fn.
- Late sprint (all tickets coded): slots run test_fn.
- Mid sprint: slots dynamically mix roles as work becomes available.

Merge serialization from issue #738 is preserved: the production test_fn
wraps its git-merge step in develop_merge_guard(), so concurrent test tasks
never merge to develop at the same time regardless of slot count.

Tester rejection re-queues the ticket to the FRONT of the coder queue so it
is retried before any other pending ticket (issue #1413 AC6).

Conflict rules (file-overlap checks) apply when picking BOTH code and test
tasks: a test task is not started while a coding task on the same files is
active (AC5).

When max_coder_slots <= 1, behaviour is identical to pipeline.run_level with
pipeline=True — the delegate path ensures zero divergence.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable, Optional

from services.sprint_manager.pipeline import (
    DEFAULT_MAX_ATTEMPTS,
    LevelResult,
    StageResult,
    run_level,
)


def run_concurrent_level(
    tickets: list,
    code_fn: Callable[[Any, int], StageResult],
    test_fn: Callable[[Any, int], StageResult],
    *,
    max_coder_slots: int = 1,
    file_map: Optional[dict] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_merged: Optional[Callable[[Any], None]] = None,
    on_needs_rework: Optional[Callable[[Any], None]] = None,
    on_dropped: Optional[Callable[[Any], None]] = None,
    on_active_slots_change: Optional[Callable[[int], None]] = None,
) -> LevelResult:
    """Process all tickets in one dispatch level with role-flexible slots.

    Each slot picks the next eligible task from whichever queue has work —
    code tasks are preferred (to keep the pipeline fed), test tasks taken
    when no eligible code task is available.

    ``file_map`` maps each ticket to the set of file paths it touches.
    Tickets whose file sets intersect with any currently-coding ticket are
    held regardless of whether the waiting ticket is a code or test task
    (AC5: same conflict rules for both roles).

    ``on_active_slots_change(count)`` is called (thread-safely) each time the
    number of actively-coding tickets changes (backward-compatible with AC9
    from issue #1412).

    When ``max_coder_slots <= 1`` the call delegates to ``run_level(pipeline=True)``
    for exact behavioural equivalence.
    """
    if max_coder_slots <= 1:
        return run_level(
            tickets, code_fn, test_fn,
            pipeline=True,
            max_attempts=max_attempts,
            on_merged=on_merged,
            on_needs_rework=on_needs_rework,
            on_dropped=on_dropped,
        )

    return _run_concurrent(
        tickets, code_fn, test_fn,
        max_coder_slots=max_coder_slots,
        file_map=file_map or {},
        max_attempts=max_attempts,
        on_merged=on_merged,
        on_needs_rework=on_needs_rework,
        on_dropped=on_dropped,
        on_active_slots_change=on_active_slots_change,
    )


def _run_concurrent(
    tickets, code_fn, test_fn, *,
    max_coder_slots, file_map, max_attempts,
    on_merged, on_needs_rework, on_dropped, on_active_slots_change,
) -> LevelResult:
    result = LevelResult()
    attempts: dict = {t: 0 for t in tickets}
    result.attempts = attempts

    lock = threading.Lock()
    cond = threading.Condition(lock)

    # All mutable state is protected by `cond` (which wraps `lock`).
    #
    # coder_q is a list (not deque) so we can insert(0, t) to re-queue a
    # rejected ticket at the FRONT without copying the whole structure.
    coder_q: list = list(tickets)    # tickets waiting to be coded
    tester_q: list = []              # (ticket, attempt) pairs ready to test
    coding_set: set = set()          # tickets whose code_fn is currently running
    terminal: set = set()            # tickets that have reached a final state
    total: int = len(tickets)

    def _active_files() -> set:
        """Files touched by tickets currently being coded."""
        f: set = set()
        for t in coding_set:
            f |= file_map.get(t, set())
        return f

    def _find_eligible_code():
        """First pending code task whose files don't conflict with coding_set."""
        active = _active_files()
        for t in coder_q:
            if not (file_map.get(t, set()) & active):
                return t
        return None

    def _find_eligible_test():
        """First test-ready task whose files don't conflict with coding_set (AC5)."""
        active = _active_files()
        for item in tester_q:
            t, _att = item
            if not (file_map.get(t, set()) & active):
                return item
        return None

    def _finished() -> bool:
        return len(terminal) >= total

    # ── flexible worker ───────────────────────────────────────────────────────

    def worker() -> None:
        while True:
            task_type: Optional[str] = None
            t = None
            attempt = None

            with cond:
                while True:
                    if _finished():
                        cond.notify_all()
                        return

                    # Prefer code tasks to keep the pipeline flowing.
                    tc = _find_eligible_code()
                    if tc is not None:
                        coder_q.remove(tc)
                        coding_set.add(tc)
                        attempts[tc] += 1
                        t, attempt, task_type = tc, attempts[tc], "code"
                        if on_active_slots_change is not None:
                            on_active_slots_change(len(coding_set))
                        break

                    # Fall back to a test task when no eligible code task exists.
                    tt = _find_eligible_test()
                    if tt is not None:
                        tester_q.remove(tt)
                        t, attempt = tt
                        task_type = "test"
                        break

                    cond.wait()

            # ── run the task outside the lock ─────────────────────────────────

            if task_type == "code":
                code_res = code_fn(t, attempt)
                with cond:
                    result.order.append((t, "coder", attempt))
                    coding_set.discard(t)
                    if on_active_slots_change is not None:
                        on_active_slots_change(len(coding_set))
                    if code_res is StageResult.PASS:
                        tester_q.append((t, attempt))
                    else:
                        terminal.add(t)
                        result.dropped.append(t)
                        if on_dropped is not None:
                            on_dropped(t)
                    cond.notify_all()

            else:  # "test"
                # Merge serialization is the responsibility of test_fn itself
                # (production test_fn wraps git-merge in develop_merge_guard from
                # serialization.py — issue #738). The scheduler imposes no additional
                # merge lock so that test tasks can overlap in their non-merge work.
                test_res = test_fn(t, attempt)
                with cond:
                    result.order.append((t, "tester", attempt))
                    if test_res is StageResult.PASS:
                        terminal.add(t)
                        result.merged.append(t)
                        if on_merged is not None:
                            on_merged(t)
                    elif test_res is StageResult.EXHAUST:
                        terminal.add(t)
                        result.needs_rework.append(t)
                        if on_needs_rework is not None:
                            on_needs_rework(t)
                    elif test_res is StageResult.REJECT:
                        if attempts[t] >= max_attempts:
                            terminal.add(t)
                            result.needs_rework.append(t)
                            if on_needs_rework is not None:
                                on_needs_rework(t)
                        else:
                            # Re-queue to FRONT of coder queue so this ticket is
                            # retried before any other pending ticket (AC6).
                            coder_q.insert(0, t)
                    else:  # StageResult.FAIL
                        terminal.add(t)
                        result.dropped.append(t)
                        if on_dropped is not None:
                            on_dropped(t)
                    cond.notify_all()

    # ── launch flexible worker threads ────────────────────────────────────────

    workers = [
        threading.Thread(target=worker, daemon=True)
        for _ in range(max_coder_slots)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    return result

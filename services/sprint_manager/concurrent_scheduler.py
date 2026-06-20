"""Conflict-aware concurrent coder scheduler (issue #1412).

Runs up to max_coder_slots coders in parallel within a single dispatch level,
subject to file-conflict constraints. Tickets whose files_likely_affected overlap
with an actively-coding ticket are held until that ticket completes (merges or
is dropped). The level does not return until every ticket is terminal.

Algorithm per scheduling tick:
  1. Gather pending tickets whose file sets do not conflict with any ticket
     currently in the coding state.
  2. From those eligible tickets, greedily select a non-mutually-conflicting
     subset (largest independent set approximation) up to the available slots.
  3. Dispatch each selected ticket in its own coder worker thread.
  4. When a coder finishes, hand off to the tester queue (serial) and free the
     slot for the next eligible ticket.

When max_coder_slots == 1, behaviour is identical to pipeline.run_level with
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
    """Process all tickets in one dispatch level with concurrent coders.

    ``code_fn(ticket, attempt)`` and ``test_fn(ticket, attempt)`` carry the
    same semantics as in pipeline.run_level.

    ``file_map`` maps each ticket to the set of file paths it touches
    (files_likely_affected / files_touched from the estimate).  Tickets whose
    file sets intersect with any currently-coding ticket are held until that
    ticket exits the coding state.

    ``on_active_slots_change(count)`` is called (thread-safely) each time the
    number of actively-coding tickets changes, so the sprint state can track
    lane occupancy in real time (AC9).

    When ``max_coder_slots <= 1`` the call delegates to ``run_level(pipeline=True)``
    for exact behavioural equivalence (AC2).
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
    pending: list = list(tickets)       # tickets not yet started / re-queued after REJECT
    coding_set: set = set()             # tickets whose code_fn is currently running
    tester_q: deque = deque()           # (ticket, attempt) waiting for test_fn
    terminal: set = set()               # tickets that have reached a final state
    total: int = len(tickets)

    def _active_files() -> set:
        f: set = set()
        for t in coding_set:
            f |= file_map.get(t, set())
        return f

    def _find_eligible():
        """Return the first pending ticket that doesn't conflict with coding_set."""
        active = _active_files()
        for t in pending:
            if not (file_map.get(t, set()) & active):
                return t
        return None

    def _finished() -> bool:
        return len(terminal) >= total

    # ── coder worker ─────────────────────────────────────────────────────────

    def coder_worker() -> None:
        while True:
            with cond:
                # Wait until there is an eligible ticket or all work is done.
                while True:
                    if _finished():
                        cond.notify_all()
                        return
                    t = _find_eligible()
                    if t is not None:
                        break
                    cond.wait()
                # Claim the ticket atomically.
                pending.remove(t)
                coding_set.add(t)
                attempts[t] += 1
                attempt = attempts[t]
                if on_active_slots_change is not None:
                    on_active_slots_change(len(coding_set))

            # Run code_fn outside the lock so other workers can proceed.
            code_res = code_fn(t, attempt)

            with cond:
                result.order.append((t, "coder", attempt))
                coding_set.discard(t)
                if code_res is StageResult.PASS:
                    tester_q.append((t, attempt))
                else:
                    # Coder failure: terminal immediately (no tester).
                    terminal.add(t)
                    result.dropped.append(t)
                    if on_dropped is not None:
                        on_dropped(t)
                if on_active_slots_change is not None:
                    on_active_slots_change(len(coding_set))
                cond.notify_all()

    # ── tester worker (serial — one ticket at a time) ─────────────────────────

    def tester_worker() -> None:
        while True:
            with cond:
                # Wait until there is something to test or all work is done.
                while not tester_q:
                    if _finished():
                        cond.notify_all()
                        return
                    cond.wait()
                t, attempt = tester_q.popleft()

            # Run test_fn outside the lock.
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
                        # Re-add to pending for the next coder pick-up.
                        pending.append(t)
                else:  # StageResult.FAIL
                    terminal.add(t)
                    result.dropped.append(t)
                    if on_dropped is not None:
                        on_dropped(t)
                cond.notify_all()

    # ── launch threads ────────────────────────────────────────────────────────

    workers = [
        threading.Thread(target=coder_worker, daemon=True)
        for _ in range(max_coder_slots)
    ]
    tester_t = threading.Thread(target=tester_worker, daemon=True)

    for w in workers:
        w.start()
    tester_t.start()

    for w in workers:
        w.join()
    tester_t.join()

    return result

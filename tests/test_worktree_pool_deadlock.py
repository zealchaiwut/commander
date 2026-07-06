"""Worktree pool deadlock hardening.

A sprint hung for 3+ hours with zero CPU and zero child processes: a slot never
returned to the free pool, and acquire() blocked on its condition variable
forever with no timeout, no error, and no operator-visible signal. Two fixes:
1. acquire() takes a bounded timeout and raises TimeoutError instead of
   blocking forever when no slot frees up.
2. release() returns the slot to the free pool in a `finally`, so an exception
   anywhere in its cleanup can never permanently leak a slot out of circulation.
"""
from __future__ import annotations

import threading
import time

import pytest
import worktree_pool as wp


def _make_pool(tmp_path, slots=1):
    return wp.WorktreePool(
        pool_dir=tmp_path / "runtime" / "worktree-pool",
        repo_root=tmp_path / "repo",
        base_branch="develop",
        slots=slots,
    )


def test_acquire_raises_timeout_when_pool_is_empty(tmp_path):
    """No slots ever created (create() never ran / produced zero slots) — a
    real acquire() must fail fast and loud, not hang forever."""
    pool = _make_pool(tmp_path)
    with pytest.raises(TimeoutError):
        pool.acquire(timeout=0.2)


def test_acquire_raises_timeout_when_all_slots_stuck_in_use(tmp_path, monkeypatch):
    """Every slot is marked in-use and never comes back — acquire() for a
    second caller must time out instead of hanging (the sprint-102 scenario:
    a leaked slot meant the next ticket's acquire() would wait forever)."""
    pool = _make_pool(tmp_path, slots=1)
    monkeypatch.setattr(pool, "_ensure_slot_ready", lambda wt: True)
    fake_slot = tmp_path / "runtime" / "worktree-pool" / "slot-0"
    fake_slot.mkdir(parents=True)
    pool._free = [fake_slot]

    wt = pool.acquire(timeout=5)
    assert wt == fake_slot
    # Slot never released — a second acquire must not hang the test suite.
    with pytest.raises(TimeoutError):
        pool.acquire(timeout=0.2)


def test_acquire_succeeds_once_a_slot_is_released_concurrently(tmp_path, monkeypatch):
    """A slot that frees up mid-wait must be picked up before the timeout —
    the fix must not make normal operation slower."""
    pool = _make_pool(tmp_path, slots=1)
    monkeypatch.setattr(pool, "_ensure_slot_ready", lambda wt: True)
    slot = tmp_path / "runtime" / "worktree-pool" / "slot-0"
    slot.mkdir(parents=True)
    pool._in_use.add(slot)  # start fully occupied, nothing free

    def _release_soon():
        time.sleep(0.1)
        with pool._cond:
            pool._in_use.discard(slot)
            pool._free.append(slot)
            pool._cond.notify_all()

    threading.Thread(target=_release_soon, daemon=True).start()
    wt = pool.acquire(timeout=5)
    assert wt == slot


def test_release_returns_slot_even_when_cleanup_raises(tmp_path, monkeypatch):
    """If a cleanup step inside release() raises, the slot must still land
    back in the free pool (via finally) — never permanently lost."""
    pool = _make_pool(tmp_path, slots=1)
    slot = tmp_path / "repo-slot"
    slot.mkdir(parents=True)
    pool._in_use.add(slot)

    def _boom(self, wt_path):
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(wp.WorktreePool, "_link_venv", _boom)
    monkeypatch.setattr(wp, "_run", lambda *a, **k: (True, "", ""))

    with pytest.raises(RuntimeError):
        pool.release(slot)

    # Despite the exception, the slot must be back in the free pool.
    assert slot in pool._free
    assert slot not in pool._in_use


def test_release_notifies_a_waiting_acquire_despite_exception(tmp_path, monkeypatch):
    """An acquire() blocked waiting for a slot must still wake up and get it
    even if the releasing call's cleanup raised."""
    pool = _make_pool(tmp_path, slots=1)
    slot = tmp_path / "repo-slot"
    slot.mkdir(parents=True)
    pool._in_use.add(slot)
    monkeypatch.setattr(pool, "_ensure_slot_ready", lambda wt: True)
    monkeypatch.setattr(wp, "_run", lambda *a, **k: (True, "", ""))

    def _boom(self, wt_path):
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(wp.WorktreePool, "_link_venv", _boom)

    results = {}

    def _acquirer():
        results["wt"] = pool.acquire(timeout=5)

    t = threading.Thread(target=_acquirer, daemon=True)
    t.start()
    time.sleep(0.1)  # let the acquirer start waiting
    try:
        pool.release(slot)
    except RuntimeError:
        pass
    t.join(timeout=5)
    assert results.get("wt") == slot

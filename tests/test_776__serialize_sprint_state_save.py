"""
Tests for issue #776: Serialize SprintState.save under concurrent pipeline mode.

Acceptance criteria covered:
- AC1: SprintState has a threading.Lock (_save_lock) for save serialization
- AC2: to_dict() is called while the lock is held (verified via concurrent mutation)
- AC3: Write is atomic (os.replace() via temp file — no torn reads)
- AC4: _save_lock is independent of existing _label_lock / _merge_lock
- AC5: Concurrent saves produce valid parseable JSON after a pipeline-mode run
- AC6: Single-thread (non-pipeline) save is unaffected
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from services.sprint_manager.state import IssueState, SprintState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(n_issues: int = 3) -> SprintState:
    state = SprintState(sprint_label="sprint-test", sprint_number=1)
    state.issues = [IssueState(number=i, title=f"Issue {i}") for i in range(1, n_issues + 1)]
    return state


# ---------------------------------------------------------------------------
# AC1: SprintState has a _save_lock threading.Lock
# ---------------------------------------------------------------------------

def test_sprint_state_has_save_lock():
    """AC1: SprintState must expose a _save_lock that is a threading.Lock."""
    state = SprintState(sprint_label="sprint-1", sprint_number=1)
    assert hasattr(state, "_save_lock"), (
        "_save_lock must be present on SprintState instances"
    )
    # threading.Lock() returns a _thread.lock — isinstance against Lock() instance works
    lock = threading.Lock()
    assert isinstance(state._save_lock, type(lock)), (
        "_save_lock must be a threading.Lock instance"
    )


def test_save_lock_acquired_before_write(tmp_path):
    """AC1: Acquiring the lock externally blocks save() until the lock is released."""
    state = _make_state()
    path = tmp_path / "state.json"
    completed = []

    def do_save():
        state.save(path)
        completed.append(True)

    # Hold the lock from outside — save() must block until we release it
    with state._save_lock:
        t = threading.Thread(target=do_save)
        t.start()
        time.sleep(0.05)
        assert not completed, "save() must not complete while lock is held externally"
    t.join(timeout=2)
    assert completed, "save() must complete once the lock is released"


# ---------------------------------------------------------------------------
# AC3: Atomic write — no torn reads visible to concurrent readers
# ---------------------------------------------------------------------------

def test_save_produces_valid_json_on_disk(tmp_path):
    """AC3/AC6: save() writes valid JSON that parses cleanly."""
    state = _make_state(5)
    path = tmp_path / "state.json"
    state.save(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["sprint_label"] == "sprint-test"
    assert len(data["issues"]) == 5


def test_save_atomic_no_torn_reads(tmp_path):
    """AC3: Concurrent readers never observe an empty or partially-written file.

    With os.replace(), the file transition is atomic at the OS level: readers
    see either the old complete file or the new complete file, never a partial.
    """
    state = _make_state(10)
    path = tmp_path / "state.json"
    state.save(path)  # ensure file exists before readers start

    stop = threading.Event()
    torn_reads: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                text = path.read_text()
                if text:
                    json.loads(text)
            except json.JSONDecodeError as exc:
                torn_reads.append(str(exc))
            except OSError:
                pass  # file briefly absent during first replace is acceptable

    def writer():
        for _ in range(15):
            state.save(path)

    reader_threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    writer_threads = [threading.Thread(target=writer) for _ in range(3)]

    for t in reader_threads:
        t.start()
    for t in writer_threads:
        t.start()
    for t in writer_threads:
        t.join(timeout=10)
    stop.set()
    for t in reader_threads:
        t.join(timeout=2)

    assert not torn_reads, (
        f"Observed {len(torn_reads)} torn/invalid reads during concurrent writes: "
        f"{torn_reads[:3]}"
    )


# ---------------------------------------------------------------------------
# AC4: _save_lock is independent of existing locks
# ---------------------------------------------------------------------------

def test_save_lock_is_independent_of_module_level_locks():
    """AC4: _save_lock must be a separate object from _label_lock and _merge_lock."""
    import services.sprint_manager.sprint_manager as sm

    state = SprintState(sprint_label="sprint-1", sprint_number=1)

    label_lock = getattr(sm, "_label_lock", None)
    merge_lock = getattr(sm, "_merge_lock", None)

    if label_lock is not None:
        assert state._save_lock is not label_lock, (
            "_save_lock must not be the same object as _label_lock"
        )
    if merge_lock is not None:
        assert state._save_lock is not merge_lock, (
            "_save_lock must not be the same object as _merge_lock"
        )

    # Two separate SprintState instances have separate locks (instance-level)
    # or both point to the same class-level lock — either is acceptable per AC1.
    state2 = SprintState(sprint_label="sprint-2", sprint_number=2)
    # Both are real locks — the test just confirms they exist and aren't aliased to
    # the module-level locks; whether they share the same object is implementation detail.
    assert isinstance(state2._save_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# AC5: Concurrent pipeline-mode saves produce valid, parseable state.json
# ---------------------------------------------------------------------------

def test_concurrent_saves_always_produce_valid_json(tmp_path):
    """AC5: Simulates pipeline mode — two threads mutate issues and call save()
    concurrently.  The final state.json must be valid parseable JSON.
    """
    state = _make_state(5)
    path = tmp_path / "state.json"
    errors: list[Exception] = []

    def coder_thread(issue_idx: int):
        try:
            for status in ("coder_dispatched", "coder_running", "coder_done"):
                state.issues[issue_idx % len(state.issues)].agent_status = status
                state.save(path)
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    def tester_thread(issue_idx: int):
        try:
            for status in ("tester_dispatched", "tester_running", "tester_done"):
                state.issues[issue_idx % len(state.issues)].agent_status = status
                state.save(path)
                time.sleep(0.001)
        except Exception as exc:
            errors.append(exc)

    threads = []
    for i in range(3):
        threads.append(threading.Thread(target=coder_thread, args=(i,)))
        threads.append(threading.Thread(target=tester_thread, args=(i,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Threads raised exceptions: {errors}"

    # Final file must parse cleanly (AC5)
    assert path.exists(), "state.json must exist after concurrent saves"
    final = json.loads(path.read_text())
    assert "sprint_label" in final
    assert "issues" in final
    assert isinstance(final["issues"], list)


def test_concurrent_saves_no_exceptions_under_rapid_contention(tmp_path):
    """AC5: 20 concurrent threads all calling save() produce no exceptions."""
    state = _make_state(5)
    path = tmp_path / "state.json"
    errors: list[Exception] = []

    def worker(idx: int):
        try:
            state.issues[idx % len(state.issues)].agent_status = f"worker-{idx}"
            state.save(path)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Concurrent save raised exceptions: {errors}"
    parsed = json.loads(path.read_text())
    assert parsed["sprint_label"] == "sprint-test"


# ---------------------------------------------------------------------------
# AC6: Single-thread save is unaffected
# ---------------------------------------------------------------------------

def test_single_thread_save_roundtrip(tmp_path):
    """AC6: Non-pipeline (single-thread) save completes and produces correct state file."""
    state = SprintState(sprint_label="sprint-solo", sprint_number=7)
    state.issues = [
        IssueState(number=1, title="Solo issue", agent_status="tester_done")
    ]
    state.pipeline_mode = False

    path = tmp_path / ".commander" / "sprints" / "sprint-solo" / "state.json"
    state.save(path)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["sprint_label"] == "sprint-solo"
    assert data["sprint_number"] == 7
    assert data["pipeline_mode"] is False
    assert data["issues"][0]["number"] == 1
    assert data["issues"][0]["agent_status"] == "tester_done"


def test_single_thread_save_creates_parent_dirs(tmp_path):
    """AC6: save() creates parent directories if they don't exist."""
    state = _make_state(1)
    deep_path = tmp_path / "a" / "b" / "c" / "state.json"
    assert not deep_path.parent.exists()
    state.save(deep_path)
    assert deep_path.exists()
    json.loads(deep_path.read_text())


def test_single_thread_repeated_saves(tmp_path):
    """AC6: Multiple sequential saves overwrite correctly — last write wins."""
    state = _make_state(2)
    path = tmp_path / "state.json"

    state.save(path)
    state.issues[0].agent_status = "coder_done"
    state.save(path)
    state.issues[1].agent_status = "tester_done"
    state.save(path)

    data = json.loads(path.read_text())
    assert data["issues"][0]["agent_status"] == "coder_done"
    assert data["issues"][1]["agent_status"] == "tester_done"

"""Thread-safety tests for _sprint_user_cancelled (issue #514).

Verifies that the threading.Event replaces the bare bool global and that
cross-thread signaling is consistent.
"""
import threading
import time

import services.sprint_manager.sprint_manager as sm


def test_flag_is_threading_event():
    assert isinstance(sm._sprint_user_cancelled, threading.Event)


def test_initial_state_clear():
    sm._sprint_user_cancelled.clear()
    assert not sm._sprint_user_cancelled.is_set()


def test_set_and_clear():
    sm._sprint_user_cancelled.clear()
    sm._sprint_user_cancelled.set()
    assert sm._sprint_user_cancelled.is_set()
    sm._sprint_user_cancelled.clear()
    assert not sm._sprint_user_cancelled.is_set()


def test_cross_thread_set_visible_to_reader():
    """Set from one thread; reader thread must observe is_set() == True."""
    sm._sprint_user_cancelled.clear()
    results = []

    def reader():
        # Wait up to 2 s for the event to be set.
        observed = sm._sprint_user_cancelled.wait(timeout=2.0)
        results.append(observed)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.05)  # let reader block on wait()
    sm._sprint_user_cancelled.set()
    t.join(timeout=3.0)

    assert len(results) == 1
    assert results[0] is True


def test_concurrent_readers_all_observe_set():
    """20 readers simultaneously; single setter — all must see is_set() == True."""
    sm._sprint_user_cancelled.clear()
    barrier = threading.Barrier(21)  # 20 readers + 1 setter
    observations = []
    lock = threading.Lock()

    def reader():
        barrier.wait()
        # Spin-wait briefly to catch the set in progress.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if sm._sprint_user_cancelled.is_set():
                break
            time.sleep(0.001)
        with lock:
            observations.append(sm._sprint_user_cancelled.is_set())

    def setter():
        barrier.wait()
        sm._sprint_user_cancelled.set()

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(20)]
    threads.append(threading.Thread(target=setter, daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(observations) == 20
    assert all(observations), f"Some readers missed the event: {observations}"

    sm._sprint_user_cancelled.clear()


def test_no_bare_bool_assignment_in_module(tmp_path):
    """Static check: no bare `_sprint_user_cancelled = True/False` in source."""
    import re
    src = sm.__file__
    if src.endswith(".pyc"):
        src = src[:-1]
    text = open(src).read()
    pattern = re.compile(r"_sprint_user_cancelled\s*=\s*(True|False)")
    matches = pattern.findall(text)
    assert matches == [], f"Bare bool assignment found: {matches}"

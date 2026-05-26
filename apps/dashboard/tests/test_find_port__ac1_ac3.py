"""Tests for scripts/find_port.py (issue #62).

AC-1: find_port.py --prefer <port> prints preferred port when free, exits 0.
AC-2: find_port.py --prefer <port> prints a different port when <port> is in use.
AC-3: find_port.py --prefer <port> --strategy always_random always prints a
      random free port, ignoring the preferred port.
"""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
FIND_PORT_SCRIPT = SCRIPTS_DIR / "find_port.py"


def _run_find_port(*args: str) -> tuple[int, str]:
    """Run find_port.py with given args. Returns (returncode, stdout stripped)."""
    result = subprocess.run(
        [sys.executable, str(FIND_PORT_SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip()


def _find_free_port() -> int:
    """Return a port number that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _is_port_bindable(port: int) -> bool:
    """Return True if the port can be bound (i.e., it is free)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


# ── AC-1: free port case ───────────────────────────────────────────────────────

def test_find_port_returns_preferred_when_free():
    """AC-1: When preferred port is free, script prints it and exits 0."""
    free_port = _find_free_port()
    rc, output = _run_find_port("--prefer", str(free_port))
    assert rc == 0, f"Expected exit 0, got {rc}. Output: {output}"
    assert output == str(free_port), (
        f"Expected preferred port {free_port}, got {output!r}"
    )


def test_find_port_output_is_integer():
    """AC-1 (sanity): Output is a valid integer."""
    free_port = _find_free_port()
    rc, output = _run_find_port("--prefer", str(free_port))
    assert rc == 0
    chosen = int(output)  # raises if not integer
    assert chosen > 0


# ── AC-2: occupied port case ───────────────────────────────────────────────────

def test_find_port_returns_different_port_when_occupied():
    """AC-2: When preferred port is occupied, script prints a different port."""
    # Bind to a port to occupy it for the duration of the test
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied_sock:
        occupied_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied_sock.bind(("", 0))
        occupied_port = occupied_sock.getsockname()[1]
        occupied_sock.listen(1)

        rc, output = _run_find_port("--prefer", str(occupied_port))

    assert rc == 0, f"Expected exit 0, got {rc}. Output: {output}"
    chosen = int(output)
    assert chosen != occupied_port, (
        f"Expected a port different from occupied {occupied_port}, got {chosen}"
    )


def test_find_port_fallback_is_bindable():
    """AC-2: The fallback port returned when preferred is occupied is actually free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied_sock:
        occupied_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied_sock.bind(("", 0))
        occupied_port = occupied_sock.getsockname()[1]
        occupied_sock.listen(1)

        rc, output = _run_find_port("--prefer", str(occupied_port))

    assert rc == 0
    chosen = int(output)
    # The chosen fallback port should be bindable (i.e., free)
    assert _is_port_bindable(chosen), (
        f"Fallback port {chosen} is not bindable — it may already be in use"
    )


# ── AC-3: always_random strategy ──────────────────────────────────────────────

def test_find_port_always_random_ignores_preferred():
    """AC-3: always_random strategy always returns a different port from preferred."""
    # We run several times to reduce flakiness (OS could assign the same port by
    # coincidence, but it's extremely unlikely with multiple tries)
    free_port = _find_free_port()
    results = set()
    for _ in range(5):
        rc, output = _run_find_port(
            "--prefer", str(free_port), "--strategy", "always_random"
        )
        assert rc == 0, f"Expected exit 0, got {rc}"
        results.add(int(output))

    # At least one result should differ from the preferred port
    assert any(p != free_port for p in results), (
        f"always_random strategy returned preferred port {free_port} in all 5 runs"
    )


def test_find_port_always_random_returns_free_port():
    """AC-3: The port returned by always_random is bindable."""
    free_port = _find_free_port()
    rc, output = _run_find_port(
        "--prefer", str(free_port), "--strategy", "always_random"
    )
    assert rc == 0
    chosen = int(output)
    assert _is_port_bindable(chosen), (
        f"always_random returned port {chosen} which is not bindable"
    )


# ── Module-level unit tests (import find_port directly) ───────────────────────

def test_find_port_module_prefer_default_free(monkeypatch):
    """Unit test: find_port() with prefer_default returns preferred port when free."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import find_port as fp

    free_port = _find_free_port()
    result = fp.find_port(prefer=free_port, strategy="prefer_default")
    assert result == free_port


def test_find_port_module_prefer_default_occupied(monkeypatch):
    """Unit test: find_port() with prefer_default returns different port when occupied."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import find_port as fp

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        occupied_port = s.getsockname()[1]
        s.listen(1)
        result = fp.find_port(prefer=occupied_port, strategy="prefer_default")

    assert result != occupied_port
    assert isinstance(result, int)
    assert result > 0


def test_find_port_module_always_random(monkeypatch):
    """Unit test: find_port() with always_random ignores preferred port."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    import find_port as fp

    free_port = _find_free_port()
    results = set()
    for _ in range(5):
        results.add(fp.find_port(prefer=free_port, strategy="always_random"))

    # At least one result should differ
    assert any(p != free_port for p in results), (
        "always_random returned preferred port in all 5 calls"
    )

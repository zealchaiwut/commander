"""Tests for issue #2312: Enforce that AC tests never make live HTTP calls.

AC coverage:
  AC1 - A test file using the forbidden pattern is rejected with a message
        that names the alternative and cites CLAUDE.md #1746
  AC2 - A test file using TestClient(srv.app) is accepted
  AC3 - The GET-returns-405 probe pattern is accepted
  AC4 - The check does not fire on non-test code (files outside tests/)
        when invoked against tests/ only
  AC5 - Tests that start their own mock server are not flagged
  AC6 - Existing tests/ tree is clean (allowlist covers all pre-2312 offenders)
  AC7 - The allowlist mechanism works: an allowlisted file is skipped even
        if it contains the forbidden pattern

All tests use fixture files written to a temp directory — no live HTTP calls.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_no_live_http_in_tests.py"
TESTS_DIR = REPO_ROOT / "tests"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fixture(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a Python fixture file with the given content."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _run(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(target)],
        capture_output=True,
        text=True,
    )


def _run_on_dir(tmp_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_dir)],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# AC1: Forbidden pattern is rejected
# ---------------------------------------------------------------------------


def test_ac1_uat_base_url_and_httpx_client_rejected(tmp_path):
    """AC1: BASE_URL from UAT_BASE_URL + httpx.Client → rejected."""
    f = _write_fixture(
        tmp_path,
        "test_bad.py",
        """
        import os
        import httpx

        BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"

        def test_something():
            with httpx.Client(base_url=BASE_URL) as c:
                r = c.get("/api/board")
                assert r.status_code == 200
        """,
    )
    result = _run(f)
    assert result.returncode == 1, "Expected exit 1 for forbidden pattern"


def test_ac1_failure_message_names_alternative(tmp_path):
    """AC1: Error message mentions TestClient and CLAUDE.md #1746."""
    f = _write_fixture(
        tmp_path,
        "test_bad2.py",
        """
        import os
        import httpx

        BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"

        def test_endpoint():
            with httpx.Client(base_url=BASE_URL) as c:
                r = c.get("/api/sprints")
                assert r.status_code == 200
        """,
    )
    result = _run(f)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "TestClient" in combined, "Message must name TestClient as the alternative"
    assert "1746" in combined, "Message must cite CLAUDE.md #1746"


def test_ac1_uat_port_env_var_also_rejected(tmp_path):
    """AC1: Using UAT_PORT to build a base URL is also rejected."""
    f = _write_fixture(
        tmp_path,
        "test_uat_port.py",
        """
        import os
        import httpx

        BASE_URL = "http://localhost:" + os.environ.get("UAT_PORT", "8001")

        def test_endpoint():
            with httpx.Client(base_url=BASE_URL) as c:
                r = c.get("/api/board")
                assert r.status_code == 200
        """,
    )
    result = _run(f)
    assert result.returncode == 1


def test_ac1_localhost_literal_in_httpx_rejected(tmp_path):
    """AC1: Direct localhost literal inside httpx.Client(base_url=...) is rejected."""
    f = _write_fixture(
        tmp_path,
        "test_localhost_literal.py",
        """
        import httpx

        def test_something():
            with httpx.Client(base_url="http://localhost:8001") as c:
                r = c.get("/api/board")
                assert r.status_code == 200
        """,
    )
    result = _run(f)
    assert result.returncode == 1


def test_ac1_127_literal_in_httpx_rejected(tmp_path):
    """AC1: 127.0.0.1 literal inside httpx.Client(base_url=...) is rejected."""
    f = _write_fixture(
        tmp_path,
        "test_127_literal.py",
        """
        import httpx

        def test_something():
            with httpx.Client(base_url="http://127.0.0.1:8001") as c:
                r = c.get("/api/board")
                assert r.status_code == 200
        """,
    )
    result = _run(f)
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# AC2: TestClient is accepted
# ---------------------------------------------------------------------------


def test_ac2_testclient_pattern_accepted(tmp_path):
    """AC2: TestClient(srv.app) without a live server is accepted."""
    f = _write_fixture(
        tmp_path,
        "test_good_testclient.py",
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
        import server as srv
        from fastapi.testclient import TestClient

        def test_board_endpoint():
            client = TestClient(srv.app)
            r = client.get("/api/board")
            assert r.status_code in (200, 404)
        """,
    )
    result = _run(f)
    assert result.returncode == 0, (
        f"TestClient pattern should be accepted. stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC3: GET-returns-405 probe is accepted
# ---------------------------------------------------------------------------


def test_ac3_405_probe_pattern_accepted(tmp_path):
    """AC3: GET-probe against a POST-only route using TestClient is accepted."""
    f = _write_fixture(
        tmp_path,
        "test_405_probe.py",
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
        import server as srv
        from fastapi.testclient import TestClient

        def test_bulk_create_route_registered():
            client = TestClient(srv.app)
            r = client.get("/api/tickets/bulk")
            assert r.status_code == 405, (
                f"expected 405 from POST-only route, got {r.status_code}"
            )
        """,
    )
    result = _run(f)
    assert result.returncode == 0, (
        f"405-probe pattern should be accepted. stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC4: Non-test code is not scanned when check runs against tests/
# ---------------------------------------------------------------------------


def test_ac4_check_only_scans_tests_directory(tmp_path):
    """AC4: Running against tests/ does not scan files outside that directory."""
    # Create a forbidden file in a non-tests subdir
    non_tests = tmp_path / "scripts"
    non_tests.mkdir()
    _write_fixture(
        non_tests,
        "some_script.py",
        """
        import os
        import httpx

        BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"

        def call_api():
            with httpx.Client(base_url=BASE_URL) as c:
                return c.get("/api/board")
        """,
    )
    # Create a clean tests/ directory
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    _write_fixture(
        tests_dir,
        "test_clean.py",
        """
        def test_nothing():
            assert True
        """,
    )
    result = _run_on_dir(tests_dir)
    assert result.returncode == 0, (
        "Check against tests/ must not scan files outside it. "
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC5: Tests that start their own mock server are not flagged
# ---------------------------------------------------------------------------


def test_ac5_mock_server_test_not_flagged(tmp_path):
    """AC5: A test that starts its own HTTPServer is exempted."""
    f = _write_fixture(
        tmp_path,
        "test_mock_server.py",
        """
        import os
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import httpx

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            def log_message(self, *a):
                pass

        def test_with_own_server():
            server = HTTPServer(("127.0.0.1", 0), _Handler)
            port = server.server_address[1]
            t = threading.Thread(target=server.handle_request, daemon=True)
            t.start()
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as c:
                r = c.get("/")
                assert r.status_code == 200
            server.server_close()
        """,
    )
    result = _run(f)
    assert result.returncode == 0, (
        "Mock-server test must not be flagged. stderr:\n{result.stderr}"
    )


def test_ac5_conftest_is_always_skipped(tmp_path):
    """AC5: conftest.py is never flagged regardless of content."""
    f = _write_fixture(
        tmp_path,
        "conftest.py",
        """
        import os
        import httpx

        BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"

        def client():
            return httpx.Client(base_url=BASE_URL)
        """,
    )
    result = _run(f)
    assert result.returncode == 0, (
        f"conftest.py must always be skipped. stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# AC6: Current tests/ tree is clean
# ---------------------------------------------------------------------------


def test_ac6_current_tests_tree_is_clean():
    """AC6: Running the check against the real tests/ returns 0 (clean)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(TESTS_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Current tests/ tree has unlisted live-HTTP violations:\n"
        + result.stderr
    )


# ---------------------------------------------------------------------------
# AC7: Allowlist works
# ---------------------------------------------------------------------------


def test_ac7_allowlisted_file_is_skipped(tmp_path):
    """AC7: A file listed in .live-http-allowlist is skipped even with bad content."""
    # Create a tests dir with an allowlist that covers the bad file
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    allowlist = tests_dir / ".live-http-allowlist"
    allowlist.write_text("# test comment\ntest_allowlisted_bad.py\n", encoding="utf-8")

    _write_fixture(
        tests_dir,
        "test_allowlisted_bad.py",
        """
        import os
        import httpx

        BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:8001"

        def test_something():
            with httpx.Client(base_url=BASE_URL) as c:
                r = c.get("/api/board")
                assert r.status_code == 200
        """,
    )
    result = _run_on_dir(tests_dir)
    assert result.returncode == 0, (
        f"Allowlisted file must be skipped. stderr:\n{result.stderr}"
    )

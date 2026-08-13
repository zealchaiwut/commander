#!/usr/bin/env python3
"""Enforce that AC tests never make live HTTP calls.

Detects Python test files under tests/ that construct a live httpx or
requests client against UAT_BASE_URL, UAT_PORT, or a hardcoded localhost /
127.0.0.1 URL — patterns that cause CI failures and real side-effects
whenever no dashboard is listening.

Correct alternative: TestClient(srv.app)
  - No running server needed.
  - The repo constructs it WITHOUT a `with` block because entering the
    lifespan hangs (see tests/test_1161__finish_card_db_only.py).
  - To prove a POST-only route exists without side effects: GET it and
    assert 405 (registered) vs 404 (gone).
    See tests/test_2260__retire_bulk_create_library.py.

Reference: CLAUDE.md "AC tests must exercise behavior, not source text" (#1746).

Exit codes
----------
0  No violations found.
1  One or more test files use forbidden live-HTTP patterns.

Usage
-----
    python3 scripts/check_no_live_http_in_tests.py          # check all tests/
    python3 scripts/check_no_live_http_in_tests.py [PATH]   # explicit path
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
# Read from tests/.live-http-allowlist — a text file where each non-comment,
# non-blank line is a filename relative to tests/ that is explicitly
# grandfathered in because it is a legacy UAT integration test (designed to
# run against a live dashboard and skip/raise when the server is absent).
#
# Add NEW entries only when the test genuinely requires a live server.
# All entries pre-dating issue #2312 are listed in the allowlist file.


def _load_allowlist(tests_root: Path) -> set[str]:
    """Return a set of filenames (basenames only) that are allowlisted."""
    allowlist_path = tests_root / ".live-http-allowlist"
    if not allowlist_path.exists():
        return set()
    allowed: set[str] = set()
    for line in allowlist_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Accept both bare filenames and relative paths (relative to tests/)
        allowed.add(Path(stripped).name)
    return allowed


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Pattern A: reads UAT_BASE_URL or UAT_PORT via os.environ AND constructs
# a live httpx.Client / httpx.AsyncClient.
_UAT_ENV_READ = re.compile(
    r'os\.environ(?:\.get|\.getenv)?\s*\(\s*["\'](?:UAT_BASE_URL|UAT_PORT)["\']'
    r"|"
    r"os\.environ\s*\[\s*[\"'](?:UAT_BASE_URL|UAT_PORT)[\"']\s*\]",
)

_LIVE_HTTPX_CLIENT = re.compile(r"httpx\.(Async)?Client\s*\(")

# Pattern B (belt-and-suspenders): literal localhost/127.0.0.1 URL fed
# directly to httpx.Client( base_url= on the same line.
_LOCALHOST_LITERAL_IN_HTTPX = re.compile(
    r"httpx\.(Async)?Client\s*\(",
)
_LOCALHOST_URL = re.compile(
    r"""["']http://(localhost|127\.0\.0\.1):\d""",
)

# Pattern C: requests.* against a localhost literal on the same line.
_REQUESTS_METHOD = re.compile(
    r"requests\.(get|post|put|delete|patch|head)\s*\(",
)

# Files that start their own mock server are exempted even if they also
# connect to 127.0.0.1.  Conftest files are always skipped.
_MOCK_SERVER_INDICATORS = re.compile(
    r"HTTPServer\s*\(|socketserver\.(TCP|UDP)Server\s*\(",
)


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def _check_file(path: Path) -> list[str]:
    """Return a list of violation description strings (empty = clean)."""
    content = path.read_text(encoding="utf-8", errors="replace")
    violations: list[str] = []

    # Conftest files are infrastructure, not AC tests — skip them.
    if path.name == "conftest.py":
        return violations

    # Skip files that start their own mock server.
    if _MOCK_SERVER_INDICATORS.search(content):
        return violations

    # --- Pattern A ---
    if _UAT_ENV_READ.search(content) and _LIVE_HTTPX_CLIENT.search(content):
        violations.append(
            "reads UAT_BASE_URL/UAT_PORT from os.environ and constructs a live httpx client"
        )

    # --- Pattern B: scan line-by-line to keep matching tight ---
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # skip comment lines
        if _LOCALHOST_LITERAL_IN_HTTPX.search(line) and _LOCALHOST_URL.search(line):
            violations.append(
                f"line {lineno}: httpx client constructed with hardcoded localhost/127.0.0.1 URL"
            )
            break  # one report per file is enough for this pattern

    # --- Pattern C ---
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _REQUESTS_METHOD.search(line) and _LOCALHOST_URL.search(line):
            violations.append(
                f"line {lineno}: requests.* called with hardcoded localhost/127.0.0.1 URL"
            )
            break

    return violations


_TEACH_MSG = """
  WHY THIS IS FORBIDDEN
  ---------------------
  Live HTTP calls fail in CI (no server), create real side-effects
  (POST routes actually create tickets), and pass locally only by accident.

  CORRECT ALTERNATIVES
  --------------------
  1. In-process TestClient — no running server required:

       from fastapi.testclient import TestClient
       import server as srv

       client = TestClient(srv.app)   # no `with` block; lifespan hangs
       r = client.get("/api/board")
       assert r.status_code == 200

  2. Prove a POST-only route exists (GET → 405 registered, 404 = gone):

       r = client.get("/api/tickets/bulk")
       assert r.status_code == 405

  3. Node frontend tests: use a fetch spy — see
       tests/frontend/board-aggregate-flag.test.mjs

  REFERENCE: CLAUDE.md "AC tests must exercise behavior, not source text" (#1746)

  To allowlist a file that genuinely requires a live server, add its name
  to tests/.live-http-allowlist (with a comment explaining why).
"""


def run_check(tests_root: Path) -> int:
    """Scan tests_root for violations. Returns exit code (0=clean, 1=violations)."""
    allowed = _load_allowlist(tests_root)

    violating: list[tuple[Path, list[str]]] = []
    for py_file in sorted(tests_root.rglob("*.py")):
        # Skip files outside tests/ (e.g. if caller passes repo root)
        try:
            py_file.relative_to(tests_root)
        except ValueError:
            continue

        if py_file.name in allowed:
            continue

        reasons = _check_file(py_file)
        if reasons:
            violating.append((py_file, reasons))

    if not violating:
        print("check_no_live_http_in_tests: OK — no live HTTP patterns found in tests/")
        return 0

    print(
        f"check_no_live_http_in_tests: FAIL — {len(violating)} test file(s) "
        "make live HTTP calls\n",
        file=sys.stderr,
    )
    for path, reasons in violating:
        rel = path.relative_to(tests_root.parent) if tests_root.parent in path.parents else path
        print(f"  {rel}", file=sys.stderr)
        for r in reasons:
            print(f"    - {r}", file=sys.stderr)

    print(_TEACH_MSG, file=sys.stderr)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reject test files that make live HTTP calls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to scan (defaults to tests/ relative to the repo root).",
    )
    args = parser.parse_args()

    if args.path:
        target = Path(args.path).resolve()
    else:
        # Auto-detect: walk up from script location to find repo root
        here = Path(__file__).resolve().parent
        repo_root = here.parent
        target = repo_root / "tests"

    if not target.exists():
        print(f"check_no_live_http_in_tests: path not found: {target}", file=sys.stderr)
        sys.exit(2)

    if target.is_file():
        # Single-file mode
        tests_root = target.parent
        allowed = _load_allowlist(tests_root)
        if target.name in allowed:
            print(f"check_no_live_http_in_tests: {target.name} is allowlisted — skipping")
            sys.exit(0)
        reasons = _check_file(target)
        if not reasons:
            print(f"check_no_live_http_in_tests: OK — {target.name}")
            sys.exit(0)
        rel = target.relative_to(tests_root.parent) if tests_root.parent in target.parents else target
        print(f"check_no_live_http_in_tests: FAIL — {rel}", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        print(_TEACH_MSG, file=sys.stderr)
        sys.exit(1)

    sys.exit(run_check(target))


if __name__ == "__main__":
    main()

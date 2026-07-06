"""Tests for issue #1746: Replace source-regex tests with behavioral tests.

Acceptance criteria:
  AC1 — SSE parser test that feeds real `event: X\ndata: {json}` frames
  AC2 — Integration test for _all_sprints_running/load_projects with gh spy
  AC3 — Node-based fetch-spy for board-aggregate flag ON/OFF behavior
  AC4 — Document standard in CLAUDE.md: AC tests must exercise behavior

Run with: pytest tests/test_1746__behavioral_tests_replace_regex.py -v
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # apps/dashboard/../../
TESTS_DIR = REPO_ROOT / "tests"
FRONTEND_TESTS_DIR = TESTS_DIR / "frontend"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


# ── AC1: SSE parser test file exists and runs ────────────────────────────────

def test_ac1_sse_parser_test_file_exists():
    """SSE parser test file must exist at tests/frontend/sse-parser.test.mjs."""
    sse_test = FRONTEND_TESTS_DIR / "sse-parser.test.mjs"
    assert sse_test.exists(), f"tests/frontend/sse-parser.test.mjs not found at {sse_test}"
    content = sse_test.read_text(encoding="utf-8")
    # Verify it tests the parser function
    assert "_parsePfSSEFrame" in content, "SSE test must test _parsePfSSEFrame function"
    # Verify it tests real frames (behavioral, not just imports)
    assert "event: log" in content, "SSE test must feed real event frames"
    assert "event: done" in content, "SSE test must feed real done frames"
    # Verify it has the regression test (would have caught #982)
    assert "broken regex" in content or "regression" in content.lower(), \
        "SSE test must include regression guard for #982"


def test_ac1_sse_parser_test_runs_successfully():
    """SSE parser test must run and pass."""
    sse_test = FRONTEND_TESTS_DIR / "sse-parser.test.mjs"
    rc = subprocess.run(
        ["node", "--test", str(sse_test)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rc.returncode == 0, f"SSE parser test failed:\n{rc.stdout}\n{rc.stderr}"


# ── AC2: Integration test for _all_sprints_running with gh spy ──────────────

def test_ac2_all_sprints_running_test_file_exists():
    """Integration test for gh-spy on _all_sprints_running/load_projects."""
    integration_test = TESTS_DIR / "test_1746__all_sprints_running_gh_spy.py"
    assert integration_test.exists(), \
        f"tests/test_1746__all_sprints_running_gh_spy.py not found at {integration_test}"
    content = integration_test.read_text(encoding="utf-8")
    # Verify it tests the real function
    assert "_all_sprints_running" in content, "Test must exercise _all_sprints_running()"
    assert "load_projects" in content, "Test must exercise load_projects()"
    # Verify it uses a gh spy (behavioral)
    assert "patch.object(github_client" in content, "Test must patch github_client methods"
    # Verify it asserts zero calls
    assert "assert_not_called" in content, "Test must assert github_client methods were not called"


def test_ac2_all_sprints_running_test_runs_successfully():
    """Integration test must run and pass."""
    integration_test = TESTS_DIR / "test_1746__all_sprints_running_gh_spy.py"
    rc = subprocess.run(
        ["python", "-m", "pytest", str(integration_test), "-v"],
        cwd=str(REPO_ROOT / "apps" / "dashboard"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rc.returncode == 0, f"Integration test failed:\n{rc.stdout}\n{rc.stderr}"


# ── AC3: Fetch-spy tests for board-aggregate flag ON/OFF ────────────────────

def test_ac3_board_aggregate_fetch_spy_test_file_exists():
    """Board-aggregate flag test file must exist with fetch spies."""
    board_test = FRONTEND_TESTS_DIR / "board-aggregate-flag.test.mjs"
    assert board_test.exists(), \
        f"tests/frontend/board-aggregate-flag.test.mjs not found at {board_test}"
    content = board_test.read_text(encoding="utf-8")
    # Verify it tests fetch behavior
    assert "fetch" in content, "Board test must test fetch behavior"
    assert "loadSprintMgmt" in content, "Board test must test loadSprintMgmt()"
    # Verify it has AC3 fetch-spy tests (flag ON/OFF)
    assert ("flag ON" in content or "AC3" in content), \
        "Board test must have fetch-spy tests for flag ON/OFF"
    # Verify multiple tests for different states
    assert "/api/board" in content, "Board test must assert /api/board fetch"
    assert "/api/sprint-management" in content or "legacy" in content, \
        "Board test must assert legacy endpoint fetch"


def test_ac3_board_aggregate_fetch_spy_test_runs_successfully():
    """Board-aggregate fetch-spy test must run and pass."""
    board_test = FRONTEND_TESTS_DIR / "board-aggregate-flag.test.mjs"
    rc = subprocess.run(
        ["node", "--test", str(board_test)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rc.returncode == 0, f"Board aggregate test failed:\n{rc.stdout}\n{rc.stderr}"


# ── AC4: Documentation standard in CLAUDE.md ────────────────────────────────

def test_ac4_claude_md_documents_behavioral_test_standard():
    """CLAUDE.md must document that AC tests must exercise behavior, not source text."""
    content = CLAUDE_MD.read_text(encoding="utf-8")
    # Verify behavior-vs-regex distinction is documented
    assert "behavioral" in content.lower() or "behavior" in content.lower(), \
        "CLAUDE.md must document behavioral test standard (vs source-regex)"
    assert "source" in content.lower() and "regex" in content.lower(), \
        "CLAUDE.md must explicitly call out source-regex checks as forbidden"
    # Verify forbidden patterns are listed
    assert "forbidden" in content.lower(), "CLAUDE.md must list forbidden patterns"
    # Verify required patterns (fetch spy, frame-through-parser, etc.)
    assert "fetch spy" in content.lower() or "fetch" in content.lower(), \
        "CLAUDE.md must mention fetch spy pattern"
    # Verify #982 incident context is mentioned
    assert "982" in content, "CLAUDE.md must reference #982 incident for context"
    # Verify it explains why (behavioral > regex)
    assert "why" in content.lower() or "regression" in content.lower(), \
        "CLAUDE.md must explain why behavioral tests are required"


def test_ac4_claude_md_forbids_source_regex_checks():
    """CLAUDE.md forbidden patterns must include source-regex checks."""
    content = CLAUDE_MD.read_text(encoding="utf-8")
    # Find the forbidden section
    forbidden_idx = content.lower().find("forbidden")
    assert forbidden_idx != -1, "CLAUDE.md must have a 'Forbidden' section"
    # In the forbidden section, look for examples of bad patterns
    forbidden_section = content[forbidden_idx:forbidden_idx + 2000]
    # Must mention string assertions (the classic source-regex antipattern)
    assert ("assert" in forbidden_section and "in src" in forbidden_section) or \
           ("assert" in forbidden_section and "src" in forbidden_section), \
        "Forbidden section must include assert-in-src as a bad pattern"


def test_ac4_claude_md_requires_behavioral_patterns():
    """CLAUDE.md required patterns must include fetch spy and frame-through-parser."""
    content = CLAUDE_MD.read_text(encoding="utf-8")
    # Find required section
    required_idx = content.lower().find("required")
    assert required_idx != -1, "CLAUDE.md must have a 'Required' section"
    required_section = content[required_idx:required_idx + 2000]
    # Must mention TestClient or actual function calls (behavioral)
    assert ("testclient" in required_section.lower() or "function" in required_section.lower()), \
        "Required section must show behavioral patterns (TestClient, actual calls)"
    # Must mention frame-through-parser pattern
    assert ("parser" in required_section.lower() or "frame" in required_section.lower()), \
        "Required section must show frame-through-parser pattern"

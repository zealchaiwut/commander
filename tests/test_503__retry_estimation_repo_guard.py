"""Tests for issue #503 — Add repo validation to retry estimation handler.

Acceptance criteria tested:

  AC1 — `_smgmtRetryEstimation` guards against a null/falsy repo before the
        fetch call: `if (!repo)` (or equivalent falsy check) appears inside the
        function body, before any `fetch(` call.
  AC2 — When the repo is falsy, a toast notification is shown (call to
        `_smgmtShowToast` or equivalent) with a message indicating repo is
        absent/unconfigured.
  AC3 — The function returns early when the repo is falsy — the `return`
        statement appears inside the null-repo guard block, ensuring no network
        request is made.

The dashboard frontend is no-build vanilla HTML/JS served from disk, so these
tests assert against the static source of `project.html` (same approach as
test_798__sprint_tab_rename_and_subnav.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _extract_function(html: str, fn_name: str) -> str:
    """Extract the body of an async/sync JS function by name."""
    # Find function declaration
    start = html.find(f"function {fn_name}(")
    assert start >= 0, f"Function '{fn_name}' not found in project.html"
    # Walk forward counting braces to find the closing brace
    depth = 0
    i = start
    while i < len(html):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
        i += 1
    raise AssertionError(f"Could not find closing brace for '{fn_name}'")


# ---------------------------------------------------------------------------
# AC1 — Null-repo guard exists before the fetch call
# ---------------------------------------------------------------------------

class TestRepoGuardExists:
    def test_function_is_present(self, html: str) -> None:
        """_smgmtRetryEstimation must exist in project.html."""
        assert "function _smgmtRetryEstimation(" in html or \
               "async function _smgmtRetryEstimation(" in html, \
               "_smgmtRetryEstimation not found in project.html"

    def test_repo_variable_assigned_from_smgmt_repo(self, html: str) -> None:
        """repo must be assigned from _smgmtRepo() inside the function."""
        body = _extract_function(html, "_smgmtRetryEstimation")
        assert "_smgmtRepo()" in body, \
            "_smgmtRetryEstimation must call _smgmtRepo() to obtain the repo"

    def test_falsy_repo_guard_present_before_fetch(self, html: str) -> None:
        """if (!repo) guard must appear before fetch( inside the function."""
        body = _extract_function(html, "_smgmtRetryEstimation")
        guard_pos = body.find("if (!repo)")
        fetch_pos = body.find("fetch(")
        assert guard_pos >= 0, \
            "if (!repo) guard not found in _smgmtRetryEstimation"
        assert fetch_pos >= 0, \
            "fetch( call not found in _smgmtRetryEstimation"
        assert guard_pos < fetch_pos, \
            "if (!repo) guard must appear before the fetch( call"


# ---------------------------------------------------------------------------
# AC2 — Toast is shown when repo is falsy
# ---------------------------------------------------------------------------

class TestToastOnNullRepo:
    def test_toast_call_inside_null_repo_guard(self, html: str) -> None:
        """A toast notification must be triggered inside the !repo guard block."""
        body = _extract_function(html, "_smgmtRetryEstimation")
        guard_pos = body.find("if (!repo)")
        assert guard_pos >= 0, "if (!repo) guard not found"
        # Extract the guard block by scanning braces
        block_start = body.find("{", guard_pos)
        assert block_start >= 0, "Guard block opening brace not found"
        depth = 1
        i = block_start + 1
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        guard_block = body[block_start : i]
        assert "_smgmtShowToast(" in guard_block, \
            "_smgmtShowToast must be called inside the !repo guard block"

    def test_toast_message_mentions_repo(self, html: str) -> None:
        """The toast message must reference 'repo' (case-insensitive)."""
        body = _extract_function(html, "_smgmtRetryEstimation")
        # Find the toast call and its string argument
        match = re.search(r"_smgmtShowToast\(['\"]([^'\"]+)['\"]", body)
        assert match, "_smgmtShowToast call with a string literal not found"
        message = match.group(1).lower()
        assert "repo" in message, \
            f"Toast message should mention 'repo', got: '{match.group(1)}'"


# ---------------------------------------------------------------------------
# AC3 — Early return inside the guard (no fetch when repo is null)
# ---------------------------------------------------------------------------

class TestEarlyReturnOnNullRepo:
    def test_return_inside_null_repo_guard(self, html: str) -> None:
        """return must appear inside the !repo guard block to abort early."""
        body = _extract_function(html, "_smgmtRetryEstimation")
        guard_pos = body.find("if (!repo)")
        assert guard_pos >= 0, "if (!repo) guard not found"
        block_start = body.find("{", guard_pos)
        assert block_start >= 0, "Guard block opening brace not found"
        depth = 1
        i = block_start + 1
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        guard_block = body[block_start : i]
        # Allow `return;` or `return` followed by whitespace/semicolon
        assert re.search(r"\breturn\b", guard_block), \
            "return statement must be inside the !repo guard block"

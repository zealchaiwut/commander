"""Acceptance tests for issue #1588 — preserve #1154's history.js export/lint cleanup.

This follow-up ticket re-applies (intentionally, in its own ticket) the lint/
testability cleanup that #1154 bundled into its diff: seven internal history
helpers regain the ``export`` keyword, and three identifiers that are only
referenced inside template-literal ``onclick`` strings are removed from the
eslint ``/* global */`` comment. It also documents the convention that such
lint/export refactors must be filed separately from feature work.

Each test is anchored to a specific acceptance criterion from the issue.

AC map:
  AC1  the seven helpers carry the ``export`` keyword
  AC2  smgmtFinishSprint / smgmtDeleteSprint / _smgmtRepo are absent from the
       eslint /* global */ comment
  AC3  CLAUDE.md documents that lint/export refactors go in their own ticket
  AC4  bundle.js builds without errors after the history.js changes
  AC5  no regression: the #1154 amber loose-end band + Details rendering remain
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "history.js"
BUNDLE_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# The seven functions #1154 exported (AC1).
EXPORTED_FUNCTIONS = [
    "_histVerbsHtml",
    "_histHeadLinksHtml",
    "_histHeadActionsHtml",
    "_histMetricsHtml",
    "_histGanttHtml",
    "_histHeadHintsHtml",
    "_histPartition",
]

# Identifiers #1154 removed from the eslint /* global */ comment (AC2). They are
# only referenced inside template-literal onclick strings, so eslint never sees
# them as free identifiers and they do not belong in the global declaration.
REMOVED_GLOBALS = ["smgmtFinishSprint", "smgmtDeleteSprint", "_smgmtRepo"]


@pytest.fixture(scope="module")
def history_src():
    return HISTORY_JS.read_text(encoding="utf-8")


def _global_comment(src: str) -> str:
    """Return the text of the leading eslint /* global ... */ comment block."""
    m = re.search(r"/\*\s*global\b.*?\*/", src, re.DOTALL)
    assert m, "no /* global */ eslint comment found in history.js"
    return m.group(0)


# ---------------------------------------------------------------------------
# AC1 — the seven helpers retain the export keyword
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fn", EXPORTED_FUNCTIONS)
def test_ac1_helper_has_export_keyword(history_src, fn):
    pattern = re.compile(rf"^export function {re.escape(fn)}\b", re.MULTILINE)
    assert pattern.search(history_src), (
        f"{fn} must be declared with `export function {fn}` in history.js (AC1)"
    )


# ---------------------------------------------------------------------------
# AC2 — the three identifiers are absent from the /* global */ comment
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ident", REMOVED_GLOBALS)
def test_ac2_identifier_absent_from_global_comment(history_src, ident):
    comment = _global_comment(history_src)
    assert not re.search(rf"\b{re.escape(ident)}\b", comment), (
        f"{ident} must NOT appear in the eslint /* global */ comment (AC2)"
    )


def test_ac2_global_comment_still_declares_real_globals(history_src):
    # Guard: removing the three must not corrupt the comment — genuinely free
    # identifiers used in real code paths must remain declared.
    comment = _global_comment(history_src)
    for ident in ("escHtml", "sprintLabelDisplay", "_smgmtBySprint", "CSS"):
        assert re.search(rf"\b{re.escape(ident)}\b", comment), (
            f"{ident} should remain in the /* global */ comment"
        )


# ---------------------------------------------------------------------------
# AC3 — CLAUDE.md documents the separate-ticket convention
# ---------------------------------------------------------------------------
def test_ac3_claude_md_documents_lint_refactor_convention():
    text = CLAUDE_MD.read_text(encoding="utf-8").lower()
    assert "lint" in text and "refactor" in text, "CLAUDE.md missing lint/refactor guidance"
    # The convention: such refactors belong in their own ticket, separate from
    # feature work. Require the key signal words to co-occur.
    assert "separate ticket" in text or "own ticket" in text or "separate from feature" in text, (
        "CLAUDE.md must document that lint/export refactors go in a separate ticket (AC3)"
    )


# ---------------------------------------------------------------------------
# AC4 — bundle.js builds without errors after the history.js changes
# ---------------------------------------------------------------------------
def test_ac4_bundle_builds_without_errors():
    if shutil.which("npm") is None and shutil.which("esbuild") is None:
        pytest.skip("no npm/esbuild toolchain available in this environment")
    runner = (
        ["npm", "run", "build"]
        if shutil.which("npm")
        else [
            "esbuild",
            "apps/dashboard/static/src/index.js",
            "--bundle",
            "--format=iife",
            "--sourcemap",
            "--outfile=apps/dashboard/static/dist/bundle.js",
        ]
    )
    result = subprocess.run(
        runner, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, (
        f"bundle build failed (AC4):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert BUNDLE_JS.exists() and BUNDLE_JS.stat().st_size > 0, "bundle.js was not produced"


# ---------------------------------------------------------------------------
# AC5 — no regression: #1154's amber loose-end band + Details rendering remain
# ---------------------------------------------------------------------------
def test_ac5_loose_end_band_preserved(history_src):
    assert "_histLooseEndCount" in history_src, "loose-end count helper missing (AC5 regression)"
    assert re.search(r"loose end", history_src), "loose-end band copy missing (AC5 regression)"
    assert "--amber" in history_src, "amber tone token missing (AC5 regression)"


def test_ac5_details_rendering_preserved(history_src):
    # The Details/metrics rendering path from #1154 stays reachable.
    assert "_histMetricsHtml" in history_src, "Details/metrics renderer missing (AC5 regression)"
    assert "_histVerbsHtml" in history_src, "card verb renderer missing (AC5 regression)"

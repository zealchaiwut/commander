"""Tests for issue #1588: history.js export/global-comment changes (runs against UAT)"""
import os
import re
import pytest
import subprocess


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# Path to the history.js file being verified
HISTORY_JS = "apps/dashboard/static/src/sprint-board/history.js"
BUNDLE_JS = "apps/dashboard/static/dist/bundle.js"


def read_file(path):
    """Read file content."""
    with open(path, 'r') as f:
        return f.read()


# --- Acceptance Criteria ---

def test_1588__history_export_keywords_retained():
    """AC: _histVerbsHtml, _histHeadLinksHtml, _histHeadActionsHtml, _histMetricsHtml,
    _histGanttHtml, _histHeadHintsHtml, and _histPartition in history.js retain the
    export keywords added in #1154 (no regression from reverting)."""

    content = read_file(HISTORY_JS)

    functions = [
        "_histVerbsHtml",
        "_histHeadLinksHtml",
        "_histHeadActionsHtml",
        "_histMetricsHtml",
        "_histGanttHtml",
        "_histHeadHintsHtml",
        "_histPartition",
    ]

    for func in functions:
        pattern = rf"export\s+function\s+{re.escape(func)}\s*\("
        assert re.search(pattern, content), f"Function {func} missing 'export' keyword in history.js"


def test_1588__eslint_global_comment_cleaned():
    """AC: smgmtFinishSprint, smgmtDeleteSprint, and _smgmtRepo are absent from
    the eslint /* global */ comment in history.js (the removal from #1154 is preserved)."""

    content = read_file(HISTORY_JS)

    # Find the /* global ... */ comment block at the top of the file
    global_match = re.search(r"/\*\s*global\s+([^*]+)\*/", content, re.DOTALL)
    assert global_match, "No eslint /* global */ comment found in history.js"

    global_comment = global_match.group(1)

    forbidden_names = ["smgmtFinishSprint", "smgmtDeleteSprint", "_smgmtRepo"]
    for name in forbidden_names:
        assert name not in global_comment, (
            f"Found '{name}' in eslint global comment; it should have been removed."
        )


def test_1588__bundle_builds_without_errors():
    """AC: The bundle.js builds without errors after any changes to history.js in this ticket."""

    # Run npm run build
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd="apps/dashboard",
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"npm run build failed with exit code {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Verify bundle.js exists and is not empty
    assert os.path.exists(BUNDLE_JS), f"{BUNDLE_JS} does not exist after build"
    assert os.path.getsize(BUNDLE_JS) > 10000, f"{BUNDLE_JS} is suspiciously small"


def test_1588__no_history_tab_regression():
    """AC: No existing History tab functionality (amber band, Details rendering from #1154)
    regresses. (Manual verification — visual check via browser UAT step)"""

    pytest.skip("manual — verified via agent-browser UAT step, not HTTP")


def test_1588__reconcile_flow_no_console_errors():
    """AC: Open a sprint History card that has actionable items and click the Reconcile
    button. Expected: reconcile flow completes normally with no JS console errors.
    (Manual verification — browser interaction via UAT step)"""

    pytest.skip("manual — verified via agent-browser UAT step, not HTTP")

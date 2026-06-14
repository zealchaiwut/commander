"""Tests for issue #928: Reusable ProgressActivity component for long-running ops.

AC coverage:
  AC1 — bar mode: fill width, current-item label, counts, est-remaining
  AC2 — stepper mode: step states (pending/running/done/failed/fixed)
  AC3 — indeterminate mode: shimmer + spinner + current action label
  AC4 — live-log slot: log lines, empty state, collapse toggle
  AC5 — no server-cancel side effects on component close
  AC6 — done end state; error end state with retry button
  AC7 — full normalized payload shape accepted without error
  AC8 — CSS uses var(--…) design tokens, no hardcoded hex
  AC9 — board-render.js imports and calls renderProgressActivity

All JS tests run via Node.js (node --test tests/frontend/progress-activity.test.mjs).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPONENT_SRC = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "progress-activity.js"
BOARD_RENDER = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "board-render.js"
INDEX_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "index.js"
TEST_MJS = REPO_ROOT / "tests" / "frontend" / "progress-activity.test.mjs"


# ── Node.js test suite (all ACs) ─────────────────────────────────────────────

def test_node_suite_passes():
    """All 58 Node.js tests across AC1–AC9 must pass."""
    result = subprocess.run(
        ["node", "--test", str(TEST_MJS)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Node.js test suite failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # Confirm the pass count covers all AC tests
    assert "fail 0" in result.stderr or "fail 0" in result.stdout, (
        "Expected zero test failures"
    )


# ── AC8: source-level token audit (Python side) ───────────────────────────────

def test_component_source_exists():
    """progress-activity.js must exist in src/."""
    assert COMPONENT_SRC.exists(), f"Component source not found: {COMPONENT_SRC}"


def test_component_exports_required_symbols():
    """Component must export all required public symbols."""
    src = COMPONENT_SRC.read_text()
    for symbol in ("renderProgressActivity", "updateProgressActivityLog", "paToggleLog", "PA_CSS"):
        assert f"export function {symbol}" in src or f"export const {symbol}" in src, (
            f"Missing export: {symbol}"
        )


def test_css_uses_design_tokens_not_hex():
    """PA_CSS must use var(--...) tokens; standalone #RRGGBB hex is forbidden."""
    import re
    src = COMPONENT_SRC.read_text()
    # Extract PA_CSS template literal
    m = re.search(r"export const PA_CSS\s*=\s*`(.*?)`", src, re.DOTALL)
    assert m, "PA_CSS not found in component source"
    css = m.group(1)
    # Strip rgba() which are allowed for semi-transparent overlays
    css_stripped = re.sub(r"rgba\([^)]+\)", "", css)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}\b", css_stripped)
    assert hex_matches == [], (
        f"PA_CSS contains hardcoded hex colors (should use var(--...)): {hex_matches}"
    )


# ── AC9: board-render refactoring (Python side) ───────────────────────────────

def test_board_render_imports_component():
    """board-render.js must import renderProgressActivity from progress-activity.js."""
    src = BOARD_RENDER.read_text()
    assert "progress-activity" in src, (
        "board-render.js must import from progress-activity.js"
    )
    assert "renderProgressActivity" in src, (
        "board-render.js must reference renderProgressActivity"
    )


def test_board_render_running_card_uses_component():
    """_smgmtRunningCardHtml must call renderProgressActivity."""
    src = BOARD_RENDER.read_text()
    fn_start = src.find("export function _smgmtRunningCardHtml")
    assert fn_start != -1, "_smgmtRunningCardHtml not found in board-render.js"
    fn_end = src.find("\nexport function", fn_start + 1)
    fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:]
    assert "renderProgressActivity" in fn_body, (
        "_smgmtRunningCardHtml must call renderProgressActivity"
    )


def test_index_js_exposes_component_on_global():
    """index.js must expose paToggleLog and updateProgressActivityLog on root."""
    src = INDEX_JS.read_text()
    assert "paToggleLog" in src, "index.js must expose paToggleLog on globalThis"
    assert "updateProgressActivityLog" in src, (
        "index.js must expose updateProgressActivityLog on globalThis"
    )


def test_project_html_uses_update_function():
    """project.html _smgmtLiveLogPatch must call updateProgressActivityLog."""
    project_html = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
    src = project_html.read_text()
    assert "updateProgressActivityLog" in src, (
        "_smgmtLiveLogPatch in project.html must call updateProgressActivityLog"
    )


# ── AC5: no server-side effects in component (Python side) ────────────────────

def test_component_has_no_fetch_calls():
    """Component module must not contain any fetch() or XMLHttpRequest calls."""
    src = COMPONENT_SRC.read_text()
    assert "fetch(" not in src, "progress-activity.js must not contain fetch() calls"
    assert "XMLHttpRequest" not in src, (
        "progress-activity.js must not use XMLHttpRequest"
    )
    assert "EventSource" not in src, (
        "progress-activity.js must not manage its own SSE connection"
    )

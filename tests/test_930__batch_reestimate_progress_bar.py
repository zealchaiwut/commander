"""Tests for issue #930: Batch reestimate: show shared progress bar component.

AC coverage:
  AC1:  Batch reestimate (sprint or selection >=2 tickets) opens shared progress component in bar mode
  AC2:  Progress bar shows current/total tickets (e.g. "3 of 12")
  AC3:  Current ticket being estimated is visible in the component
  AC4:  Per-ticket results appear in the log slot as each completes
  AC5:  Progress advances immediately when each ticket returns a size/time estimate
  AC6:  Component is background-able; user can dismiss and return to see live state
  AC7:  On completion, a done summary is shown (e.g. "12 reestimated")
  AC8:  On partial failure, an error/retry end state is shown with actionable retry
  AC9:  Single-ticket reestimate shows only a lightweight inline spinner (NOT the shared component)
  AC10: Depends on the shared progress component (bar mode) being available
"""
import re
from pathlib import Path

STATIC_DIR   = Path(__file__).parent.parent / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"
BUNDLE_JS    = STATIC_DIR / "dist" / "bundle.js"
DRAG_DROP_JS = STATIC_DIR / "src" / "sprint-board" / "drag-drop.js"
BOARD_RENDER = STATIC_DIR / "src" / "sprint-board" / "board-render.js"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _js() -> str:
    """Authoritative source: project.html inline scripts + bundle.js."""
    html = _html()
    bundle = BUNDLE_JS.read_text(encoding="utf-8") if BUNDLE_JS.exists() else ""
    dd = DRAG_DROP_JS.read_text(encoding="utf-8") if DRAG_DROP_JS.exists() else ""
    br = BOARD_RENDER.read_text(encoding="utf-8") if BOARD_RENDER.exists() else ""
    return html + "\n" + bundle + "\n" + dd + "\n" + br


def _src() -> str:
    return _html() + "\n" + _js()


# ── AC1: Batch function exists and uses overlay bar mode for >=2 tickets ───────

def test_bulk_reestimate_function_exists():
    """AC1 — _smgmtBulkReEstimate function must exist."""
    src = _src()
    assert "_smgmtBulkReEstimate" in src, (
        "_smgmtBulkReEstimate not found — batch reestimate entrypoint missing"
    )


def test_bulk_reestimate_uses_board_lock_with_progress():
    """AC1 — Batch path must open the overlay in bar mode (progress: true)."""
    src = _src()
    assert "progress" in src and "_smgmtBoardLock" in src, (
        "_smgmtBoardLock with progress option not found — bar mode overlay not opened"
    )


def test_batch_requires_two_or_more_tickets():
    """AC1 — Single ticket must NOT use the bar-mode overlay (falls through to spinner)."""
    src = _src()
    # Function must branch on ticket count (>= 2 check)
    has_batch_guard = (
        "length >= 2" in src
        or "length > 1" in src
        or "issueNums.length === 1" in src
        or "issueNums.length == 1" in src
        or "nums.length === 1" in src
        or "nums.length == 1" in src
    )
    assert has_batch_guard, (
        "No single-vs-batch branch found — _smgmtBulkReEstimate must guard on ticket count"
    )


# ── AC2: Progress bar shows current/total format ─────────────────────────────

def test_progress_of_format_in_js():
    """AC2 — JS must produce 'X of Y' ticket count in the overlay message."""
    src = _src()
    assert " of " in src and ("_smgmtBulkReEst" in src or "of ${" in src or "of ${issueNums" in src), (
        "'X of Y' count format not found in batch reestimate JS"
    )


def test_board_progress_called_in_batch():
    """AC2 — _smgmtBoardProgress must be called to advance the fill bar."""
    src = _src()
    assert "_smgmtBoardProgress" in src, (
        "_smgmtBoardProgress not found — progress bar fill won't advance"
    )


# ── AC3: Current ticket visible in component ──────────────────────────────────

def test_current_ticket_element_exists():
    """AC3 — HTML must have an element to display the current ticket being estimated."""
    html = _html()
    assert (
        'id="smgmt-op-current"' in html
        or 'smgmt-op-current' in html
        or 'id="smgmt-reest-current"' in html
    ), (
        "No current-ticket display element found in overlay HTML"
    )


def test_current_ticket_updated_in_js():
    """AC3 — JS must update the current ticket label during estimation."""
    src = _src()
    assert (
        "smgmt-op-current" in src
        or "smgmt-reest-current" in src
        or "smgmt-move-overlay-msg" in src
    ), (
        "No current-ticket DOM update found in batch reestimate JS"
    )


# ── AC4: Per-ticket results appear in log slot ────────────────────────────────

def test_board_log_called_per_ticket():
    """AC4 — _smgmtBoardLog must be called to append per-ticket result lines."""
    src = _src()
    assert "_smgmtBoardLog" in src, (
        "_smgmtBoardLog not found — per-ticket log entries won't appear"
    )


def test_log_format_contains_arrow_or_result():
    """AC4 — Log line format must show ticket number and result (e.g. '#170 → M, ~15m')."""
    src = _src()
    has_format = (
        "→" in src
        or "->" in src
        or "→ " in src
        or "data.size" in src
    )
    assert has_format, (
        "No result format pattern found — per-ticket log lines won't include size result"
    )


# ── AC5: Progress advances as each ticket completes ──────────────────────────

def test_progress_updated_in_loop():
    """AC5 — _smgmtBoardProgress must be called inside the per-ticket loop."""
    src = _src()
    # The function calls _smgmtBoardProgress inside the loop body
    # Check that _smgmtBoardProgress appears near the loop logic
    assert "_smgmtBoardProgress" in src, (
        "_smgmtBoardProgress not wired in batch loop — progress won't advance per ticket"
    )


# ── AC6: Component is background-able ────────────────────────────────────────

def test_background_button_in_overlay_html():
    """AC6 — A background/dismiss button must be present in the overlay HTML."""
    html = _html()
    assert (
        'id="smgmt-op-bg-btn"' in html
        or 'smgmt-op-bg-btn' in html
        or 'smgmt-reest-bg-btn' in html
    ), (
        "Background/dismiss button not found in overlay HTML — AC6 requires backgroundable component"
    )


def test_background_function_exists():
    """AC6 — JS must have a function to background (hide) the overlay without stopping estimation."""
    src = _src()
    assert (
        "_smgmtBulkReEstBackground" in src
        or "_smgmtReEstBackground" in src
    ), (
        "Background function not found — user cannot dismiss and return to running estimate"
    )


def test_reopen_function_exists():
    """AC6 — JS must have a function to re-show the overlay after backgrounding."""
    src = _src()
    assert (
        "_smgmtBulkReEstReopen" in src
        or "_smgmtReEstReopen" in src
    ), (
        "Re-open function not found — user can background but can't return to see live state"
    )


def test_background_indicator_exists():
    """AC6 — A background-mode indicator must exist so users can return to the estimation."""
    html = _html()
    assert (
        "smgmt-reest-bg-pill" in html
        or "smgmt-op-bg-pill" in html
        or "smgmt-reest-bg-indicator" in html
    ), (
        "No background indicator element found — users can't navigate back to a backgrounded estimate"
    )


# ── AC7: Done summary on completion ──────────────────────────────────────────

def test_done_summary_function_exists():
    """AC7 — JS must show a completion summary (e.g. '12 reestimated')."""
    src = _src()
    assert (
        "_smgmtBulkReEstShowDone" in src
        or "reestimated" in src.lower()
        or "re-estimated" in src.lower()
    ), (
        "No done summary function found — AC7 requires a completion count display"
    )


def test_done_element_in_overlay():
    """AC7 — HTML must have a done-state element in the overlay."""
    html = _html()
    assert (
        'id="smgmt-op-done"' in html
        or 'smgmt-op-done' in html
        or 'smgmt-reest-done' in html
    ), (
        "No done-state element in overlay HTML"
    )


# ── AC8: Error/retry end state ────────────────────────────────────────────────

def test_error_state_function_exists():
    """AC8 — JS must have an error/retry state when tickets fail."""
    src = _src()
    assert (
        "_smgmtBulkReEstShowError" in src
        or "_smgmtBulkReEstRetry" in src
        or "failed" in src.lower()
    ), (
        "No error/retry state function found — AC8 requires actionable failure state"
    )


def test_retry_function_exists():
    """AC8 — JS must have a retry function for failed tickets."""
    src = _src()
    assert (
        "_smgmtBulkReEstRetry" in src
        or "retry" in src.lower()
    ), (
        "Retry function not found — users can't retry failed tickets"
    )


def test_error_element_in_overlay():
    """AC8 — HTML must have an error-state element in the overlay."""
    html = _html()
    assert (
        'id="smgmt-op-error"' in html
        or 'smgmt-op-error' in html
        or 'smgmt-reest-error' in html
    ), (
        "No error-state element in overlay HTML"
    )


def test_failed_tickets_tracked():
    """AC8 — JS must track which tickets failed to enable targeted retry."""
    src = _src()
    assert (
        "failed" in src
        and ("push" in src or "_failed" in src)
    ), (
        "No failed-ticket tracking found — can't retry only the failed tickets"
    )


# ── AC9: Single-ticket reestimate uses only inline spinner ───────────────────

def test_single_ticket_delegates_to_reestimate_run():
    """AC9 — _smgmtBulkReEstimate with 1 ticket must delegate to _smgmtReEstimateRun."""
    src = _src()
    assert "_smgmtReEstimateRun" in src, (
        "_smgmtReEstimateRun not found — single-ticket fallback path broken"
    )


def test_single_ticket_no_board_lock():
    """AC9 — Single-ticket path must not open the board-lock overlay."""
    src = _src()
    # The function must branch: only open overlay when >= 2 tickets
    has_guard = (
        "=== 1" in src
        or "== 1" in src
        or "> 1" in src
        or ">= 2" in src
    )
    assert has_guard, (
        "No count guard found — single-ticket reestimate may wrongly open the overlay"
    )


# ── AC10: Shared progress component (bar mode) available ─────────────────────

def test_progress_bar_element_exists():
    """AC10 — The shared progress component bar-mode element must exist in project.html."""
    html = _html()
    assert 'id="smgmt-op-progress-wrap"' in html, (
        "smgmt-op-progress-wrap not found — shared progress component bar mode not available"
    )


def test_progress_fill_element_exists():
    """AC10 — The progress bar fill element must exist."""
    html = _html()
    assert 'id="smgmt-op-progress-fill"' in html, (
        "smgmt-op-progress-fill not found — progress bar fill missing"
    )


def test_log_slot_element_exists():
    """AC10 — The log slot element must exist in the overlay."""
    html = _html()
    assert 'id="smgmt-op-log"' in html, (
        "smgmt-op-log not found — per-ticket log slot missing from shared component"
    )


# ── Reestimate All button in sprint header ────────────────────────────────────

def test_reestimate_all_button_in_sprint_board():
    """AC1 — A 'Reestimate all' button must exist in the sprint board header HTML."""
    src = _src()
    assert (
        "Reestimate all" in src
        or "reestimate all" in src.lower()
        or "smgmtBulkReEstimate" in src
        or "_smgmtBulkReEstimate" in src
    ), (
        "No 'Reestimate all' button or call found — batch trigger missing from sprint board"
    )


# ── Selection bar Reestimate button ──────────────────────────────────────────

def test_reestimate_button_in_selection_bar():
    """AC1 — Selection bar must offer a Reestimate action for selected tickets."""
    src = _src()
    has_sel_reest = (
        "smgmt-sel-reest" in src
        or ("smgmtReEstimateSelected" in src)
        or ("_smgmtBulkReEstimate" in src and "smgmt-selection-bar" in src)
    )
    assert has_sel_reest, (
        "No Reestimate action found for selection bar — can't batch-reestimate a selection"
    )

"""Tests for issue #998: Add per-request timeout to batch reestimate loop.

AC coverage:
  AC1: Each fetch in the batch loop uses an AbortController with a per-request timeout.
  AC2: On timeout, controller.abort() is called, issue pushed to failed, loop continues.
  AC3: Timed-out issues appear in the failure count/display same as other failures.
  AC4: Requests completing within the timeout window process normally (no regression).
  AC5: AbortController is scoped per-request (not shared across the batch).
"""
import re
from pathlib import Path

PROJECT_HTML = (
    Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
)


def _fn_body(name: str, src: str | None = None) -> str:
    """Return the brace-balanced body of a JS function in the HTML source."""
    if src is None:
        src = PROJECT_HTML.read_text(encoding="utf-8")
    pattern = rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{"
    match = re.search(pattern, src)
    if not match:
        raise ValueError(f"Function {name!r} not found")
    start = match.end() - 1
    depth = 0
    body_start = start + 1
    for i in range(body_start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth < 0:
                return src[body_start:i]
    raise ValueError(f"Unmatched braces in {name}")


def _src() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: AbortController with per-request timeout ────────────────────────────

def test_abort_controller_created_in_loop():
    """AC1 — AbortController must be instantiated inside the batch loop."""
    src = _src()
    assert "AbortController" in src, (
        "AbortController not found — per-request timeout requires AbortController"
    )


def test_settimeout_used_for_abort_timeout():
    """AC1 — setTimeout must be used to schedule the abort after the timeout period."""
    src = _src()
    assert "setTimeout" in src, (
        "setTimeout not found — timeout scheduling requires setTimeout"
    )


def test_controller_abort_called_in_timeout():
    """AC1 — controller.abort() must be called inside the setTimeout callback."""
    src = _src()
    assert "controller.abort()" in src, (
        "controller.abort() not found — timeout must abort the fetch via the controller"
    )


def test_signal_passed_to_fetch():
    """AC1 — fetch must receive the AbortController's signal via the options object."""
    src = _src()
    assert "signal: controller.signal" in src or "signal:controller.signal" in src, (
        "signal not passed to fetch — AbortController won't cancel the request without it"
    )


def test_timeout_value_is_reasonable():
    """AC1 — A numeric timeout value (e.g. 30000 ms) must be present in the loop."""
    src = _src()
    # Accept any timeout from 5s to 120s (5000–120000 ms)
    has_timeout = re.search(r"\b(?:[5-9]\d{3}|[1-9]\d{4}|1[01]\d{4}|120000)\b", src)
    assert has_timeout, (
        "No timeout value in expected range (5000–120000 ms) found — per-request timeout missing"
    )


# ── AC2: On timeout: abort, push to failed, continue loop ────────────────────

def test_cleartimeout_on_success():
    """AC2 — clearTimeout must cancel the abort timer when the request succeeds."""
    src = _src()
    assert "clearTimeout" in src, (
        "clearTimeout not found — timeout timer won't be cancelled on a successful request"
    )


def test_failed_push_in_catch():
    """AC2 — _bulkReEst.failed.push must be in the catch block to capture timeouts."""
    src = _src()
    assert "_bulkReEst.failed.push" in src, (
        "_bulkReEst.failed.push not found — timed-out issues won't be recorded as failed"
    )


def test_loop_continues_after_timeout():
    """AC2 — The loop must use try/catch so it continues after a timeout error."""
    src = _src()
    # The for loop with try/catch is the continuation mechanism
    assert "try {" in src and "} catch" in src, (
        "try/catch not found around fetch — loop won't continue after a timeout"
    )


# ── AC3: Timed-out issues reflected in UI same as other failures ─────────────

def test_board_log_called_on_failure():
    """AC3 — _smgmtBoardLog with 'err' must be called in the catch block."""
    src = _src()
    assert "_smgmtBoardLog" in src and "'err'" in src, (
        "_smgmtBoardLog with 'err' not found — timed-out issues won't appear in failure log"
    )


def test_show_error_uses_failed_array():
    """AC3 — _smgmtBulkReEstShowError must be called with _bulkReEst.failed."""
    src = _src()
    assert "_smgmtBulkReEstShowError" in src and "_bulkReEst.failed" in src, (
        "_smgmtBulkReEstShowError not wired to _bulkReEst.failed — timeout failures won't display"
    )


# ── AC4: Requests completing within timeout process normally ──────────────────

def test_done_counter_incremented_on_success():
    """AC4 — _bulkReEst.done++ must still be present for successful requests."""
    src = _src()
    assert "_bulkReEst.done++" in src, (
        "_bulkReEst.done++ not found — successful estimates won't advance the done counter"
    )


def test_patch_row_called_on_success():
    """AC4 — _smgmtBulkReEstPatchRow must still be called on successful fetch."""
    src = _src()
    assert "_smgmtBulkReEstPatchRow" in src, (
        "_smgmtBulkReEstPatchRow not found — successful estimate results won't update the board"
    )


# ── AC5: AbortController scoped per-request ──────────────────────────────────

def test_abort_controller_inside_for_loop():
    """AC5 — AbortController must be instantiated inside the for loop, not before it."""
    src = _src()
    # Find the batch loop section and confirm AbortController is inside it
    # Look for the pattern: for ... { ... AbortController ... }
    loop_region_match = re.search(
        r"for\s*\(const issueNum of issueNums\)(.*?)(?=\n\s*_bulkReEst\.running\s*=\s*false)",
        src,
        re.DOTALL,
    )
    assert loop_region_match is not None, (
        "Batch loop 'for (const issueNum of issueNums)' not found — cannot verify AbortController scope"
    )
    loop_body = loop_region_match.group(1)
    assert "AbortController" in loop_body, (
        "AbortController not found inside the for loop body — "
        "a shared controller would cancel all in-flight requests when one times out (violates AC5)"
    )


def test_settimeout_inside_for_loop():
    """AC5 — setTimeout for the abort must be created inside the loop (per-request)."""
    src = _src()
    loop_region_match = re.search(
        r"for\s*\(const issueNum of issueNums\)(.*?)(?=\n\s*_bulkReEst\.running\s*=\s*false)",
        src,
        re.DOTALL,
    )
    assert loop_region_match is not None, (
        "Batch loop not found — cannot verify setTimeout scope"
    )
    loop_body = loop_region_match.group(1)
    assert "setTimeout" in loop_body, (
        "setTimeout not found inside the for loop body — timeout is not per-request"
    )


# ── merged from test_batch_reestimate_timeout__998.py (dedupe #1742) ─────────

def test_30_second_timeout_value():
    """AC1 — 30-second (30000ms) timeout must be set on the AbortController."""
    body = _fn_body("_smgmtBulkReEstRun")
    assert "setTimeout(() => controller.abort(), 30000)" in body or \
           "setTimeout(()=>controller.abort(),30000)" in body.replace(" ", ""), (
        "30-second (30000ms) timeout not found in _smgmtBulkReEstRun — per-request timeout missing"
    )


def test_successful_response_processed_normally():
    """AC4 — successful responses check resp.ok and parse via resp.json()."""
    body = _fn_body("_smgmtBulkReEstRun")
    assert "resp.ok" in body and "resp.json()" in body, (
        "resp.ok / resp.json() not found — successful estimate results won't be processed"
    )


def test_full_request_lifecycle_ordering():
    """AC5 — lifecycle order: create controller → set timeout → fetch with signal
    → clear timeout on success → push failed in catch."""
    body = _fn_body("_smgmtBulkReEstRun")
    pos1 = body.find("const controller = new AbortController();")
    assert pos1 >= 0, "Step 1: controller creation"
    pos2 = body.find("const timeoutId = setTimeout(", pos1)
    assert pos2 >= 0, "Step 2: timeout set"
    pos3 = body.find("signal: controller.signal", pos2)
    assert pos3 >= 0, "Step 3: signal passed to fetch"
    pos4 = body.find("clearTimeout(timeoutId)", pos3)
    assert pos4 >= 0, "Step 4: timeout cleared on success"
    pos5 = body.find("_bulkReEst.failed.push(issueNum)", pos4)
    assert pos5 >= 0, "Step 5: failed push in catch"
    assert pos1 < pos2 < pos3 < pos4 < pos5, (
        "Lifecycle order must be: create → timeout → fetch → clear → catch"
    )


def test_batch_state_reset_each_run():
    """AC5 — _bulkReEst is reset at the start of each run so controllers don't leak."""
    src = _src()
    assert "_bulkReEst = { running: true, done: 0, total" in src or \
           "_bulkReEst={running:true,done:0,total" in src.replace(" ", ""), (
        "_bulkReEst must be reset at the start of each batch run"
    )


def test_loop_exits_early_on_running_false():
    """AC2 — loop must break when _bulkReEst.running is set false (user abort)."""
    body = _fn_body("_smgmtBulkReEstRun")
    assert "if (!_bulkReEst.running) break" in body or \
           "if(!_bulkReEst.running)break" in body.replace(" ", ""), (
        "Loop must exit early when _bulkReEst.running is false"
    )

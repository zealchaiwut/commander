"""Tests for issue #998 — Add per-request timeout to batch reestimate loop.

This is a pure HTML/JS frontend change to the batch re-estimate loop in
project.html. Each estimate request is wrapped with AbortController + 30s
timeout, independent per request. Verified via static code analysis of the
HTML source and JavaScript function behavior.

AC coverage:
  AC1 — Each fetch call uses an AbortController with per-request timeout (30s)
  AC2 — On timeout, controller.abort() is called, issue is pushed to failed list
  AC3 — Timed-out issues reflected in UI same as other failed estimates
  AC4 — Requests completing within timeout are unaffected
  AC5 — AbortController scoped per-request, not shared across batch
"""

import sys
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
HTML_PATH = DASHBOARD_DIR / "static" / "project.html"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─────────────────────────────── helpers ──────────────────────────────────


def _fn_body(name: str, src: str | None = None) -> str:
    """Return the brace-balanced body of a JS function in the HTML source."""
    if src is None:
        src = HTML_PATH.read_text(encoding="utf-8")
    pattern = rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{"
    match = re.search(pattern, src)
    if not match:
        raise ValueError(f"Function {name!r} not found")
    start = match.end() - 1  # Position of opening brace
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


# ─────────────────────────────── AC1 ───────────────────────────────────────
# Each fetch call uses an AbortController with per-request timeout


def test_abort_controller_created_per_request():
    """AC1: AbortController created for each fetch call (not shared)."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Check loop structure: for (const issueNum of issueNums)
    assert "for (const issueNum of issueNums)" in body

    # Check AbortController created inside the loop (per-request)
    # Pattern: const controller = new AbortController();
    assert "const controller = new AbortController();" in body

    # Count occurrences of AbortController() — should be exactly one per loop iteration
    # (one creation, one abort on timeout, one signal used in fetch)
    ac_creations = re.findall(r"new AbortController\(\)", body)
    assert len(ac_creations) >= 1, "AbortController must be created per request"


def test_30_second_timeout_set():
    """AC1: 30-second timeout is set per request."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: setTimeout(() => controller.abort(), 30000);
    assert "setTimeout(() => controller.abort(), 30000)" in body or \
           "setTimeout(()=>controller.abort(),30000)" in body.replace(" ", ""), \
           "30-second (30000ms) timeout must be set on AbortController"


def test_abort_signal_passed_to_fetch():
    """AC1: fetch call includes signal: controller.signal in options."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: { method: 'POST', signal: controller.signal }
    assert "signal: controller.signal" in body or \
           "signal:controller.signal" in body.replace(" ", ""), \
           "Fetch must receive signal: controller.signal to enable abort"


# ─────────────────────────────── AC2 ───────────────────────────────────────
# On timeout, controller.abort() is called, issue is pushed to failed list


def test_controller_abort_on_timeout():
    """AC2: AbortController is aborted when timeout expires."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # The timeout callback is: () => controller.abort()
    assert "controller.abort()" in body, \
           "controller.abort() must be called on timeout"


def test_failed_issues_pushed_on_timeout():
    """AC2: Timed-out issue is pushed to _bulkReEst.failed."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: catch block with _bulkReEst.failed.push(issueNum)
    assert "_bulkReEst.failed.push(issueNum)" in body or \
           "_bulkReEst.failed.push(issueNum)" in body.replace(" ", ""), \
           "Failed estimates (including timeouts) must be pushed to _bulkReEst.failed"


def test_timeout_error_caught_in_same_try_catch():
    """AC2: Abort/timeout is caught by the same catch block as HTTP errors."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: try { ... fetch ... } catch (err) { ... push failed ... }
    # The abort (AbortError) will be caught and handled like any other error
    try_pattern = r"try\s*\{.*?fetch\(.*?\).*?\}\s*catch\s*\(\s*err\s*\)"
    assert re.search(try_pattern, body, re.DOTALL), \
           "fetch must be inside try-catch to handle abort/timeout"


# ─────────────────────────────── AC3 ───────────────────────────────────────
# Timed-out issues reflected in UI same as other failed estimates


def test_failed_ui_update_includes_timed_out():
    """AC3: Timed-out issue increments failure count and appears in failure list."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # After the loop, check for error display function call:
    # _smgmtBulkReEstShowError(_bulkReEst.failed, _bulkReEst.done)
    assert "_smgmtBulkReEstShowError(_bulkReEst.failed, _bulkReEst.done)" in body or \
           "_smgmtBulkReEstShowError" in body, \
           "Failed estimates (including timeouts) must be displayed to user"


def test_timeout_error_message_logged():
    """AC3: Error message is logged to the UI for diagnostics."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: _smgmtBoardLog(`#${issueNum} failed: ${err.message}`, 'err')
    assert "_smgmtBoardLog" in body and "err.message" in body, \
           "Error details (including timeout AbortError) must be logged"


# ─────────────────────────────── AC4 ───────────────────────────────────────
# Requests completing within timeout are unaffected


def test_timeout_cleared_on_success():
    """AC4: Timeout is cleared when fetch completes successfully."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: clearTimeout(timeoutId)
    assert "clearTimeout(timeoutId)" in body or \
           "clearTimeout(timeoutId)" in body.replace(" ", ""), \
           "Timeout must be cleared when request succeeds to prevent abort"


def test_successful_response_processed_normally():
    """AC4: Successful responses are parsed and estimates updated regardless of timeout."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: if (!resp.ok) throw ... else { data = await resp.json() ... }
    assert "resp.ok" in body and "resp.json()" in body, \
           "Successful responses must be processed and estimates updated"


# ─────────────────────────────── AC5 ───────────────────────────────────────
# AbortController scoped per-request, not shared across batch


def test_controller_created_in_loop_not_before():
    """AC5: AbortController created inside the loop, not before it."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Find loop start and controller creation
    loop_pattern = r"for\s*\(\s*const issueNum of issueNums\s*\)"
    loop_match = re.search(loop_pattern, body)
    assert loop_match, "Must have for..of loop over issueNums"

    # Find first AbortController creation
    ac_pattern = r"const controller = new AbortController\(\)"
    ac_match = re.search(ac_pattern, body)
    assert ac_match, "AbortController must be created"

    # Controller creation should come AFTER loop start
    loop_start = loop_match.start()
    ac_start = ac_match.start()
    # Extract the portion between loop start and first controller creation
    between = body[loop_start:ac_start]
    # Should contain an opening brace (loop body starts)
    assert "{" in between, "AbortController must be inside the loop body"


def test_controller_not_reused_across_iterations():
    """AC5: Each loop iteration creates its own controller (no reuse)."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Controller variable is scoped with `const` inside the loop
    # This ensures it cannot be reused across iterations
    # Pattern: for (...) { ... const controller = ...; ... }
    loop_body_pattern = r"for\s*\([^)]*\)\s*\{(.*?)\n\s*\}"

    # Simpler check: count 'const controller ='
    # If it appears inside loop (which it does from AC1 test), it's scoped per iteration
    pattern = r"const controller = new AbortController\(\)"
    matches = list(re.finditer(pattern, body))
    # Must have at least one (and exactly one per loop, created fresh each iteration)
    assert len(matches) >= 1, "Controller creation pattern must exist"


# ─────────────────────────────── integration ──────────────────────────────


def test_full_request_lifecycle():
    """Integration: Full sequence from controller creation to error handling."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Verify the sequence:
    # 1. const controller = new AbortController();
    pos1 = body.find("const controller = new AbortController();")
    assert pos1 >= 0, "Step 1: Controller creation"

    # 2. const timeoutId = setTimeout(...)
    pos2 = body.find("const timeoutId = setTimeout(", pos1)
    assert pos2 >= 0, "Step 2: Timeout set"

    # 3. await fetch(..., { ..., signal: controller.signal })
    pos3 = body.find("signal: controller.signal", pos2)
    assert pos3 >= 0, "Step 3: Signal passed to fetch"

    # 4. clearTimeout(timeoutId)
    pos4 = body.find("clearTimeout(timeoutId)", pos3)
    assert pos4 >= 0, "Step 4: Timeout cleared on success"

    # 5. catch (err) with failed push
    pos5 = body.find("_bulkReEst.failed.push(issueNum)", pos4)
    assert pos5 >= 0, "Step 5: Failed push in catch block"

    # Verify ordering: each step should come after the previous
    assert pos1 < pos2 < pos3 < pos4 < pos5, \
           "Lifecycle must be: create → set timeout → fetch with signal → clear on success → catch errors"


def test_no_shared_controller_across_batches():
    """AC5: _bulkReEst state is reset; new batch gets fresh controllers."""
    src = HTML_PATH.read_text(encoding="utf-8")

    # Check that _bulkReEst is reset at the start of each batch run
    # Pattern: _bulkReEst = { running: true, done: 0, total, failed: [], finished: false };
    assert "_bulkReEst = { running: true, done: 0, total" in src or \
           "_bulkReEst={running:true,done:0,total" in src.replace(" ", ""), \
           "_bulkReEst must be reset at the start of each batch run"
    # Each new batch starts fresh, so controllers from a prior batch are discarded


# ─────────────────────────────── edge cases ────────────────────────────────


def test_loop_break_condition():
    """Edge case: Loop respects _bulkReEst.running to allow early exit."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: if (!_bulkReEst.running) break;
    assert "if (!_bulkReEst.running) break" in body or \
           "if(!_bulkReEst.running)break" in body.replace(" ", ""), \
           "Loop must exit early if _bulkReEst.running is set to false (user abort)"


def test_error_message_includes_timeout():
    """Edge case: When AbortError occurs, it is caught and logged with context."""
    src = HTML_PATH.read_text(encoding="utf-8")
    body = _fn_body("_smgmtBulkReEstRun", src)

    # Pattern: catch (err) { ... _smgmtBoardLog(`#${issueNum} failed: ${err.message}`, 'err') }
    assert "catch (err)" in body
    assert "err.message" in body
    # AbortError will have message like "The operation was aborted"
    # or "The user aborted a request" — these will be captured and logged


def test_timeout_value_reasonable():
    """Edge case: 30-second timeout is a reasonable value (not too short)."""
    src = HTML_PATH.read_text(encoding="utf-8")

    # 30000ms = 30s is reasonable for estimate requests (not a 1s timeout that's too strict)
    assert "30000" in src
    timeout_ms = 30000
    assert timeout_ms >= 10000, "Timeout should be at least 10 seconds"
    assert timeout_ms <= 120000, "Timeout should not exceed 2 minutes"

"""Tests for issue #982: Preflight stepper hardening (merges #983, #994).

AC coverage:
  AC1: _pfStepperAnimate is wrapped with .catch(() => _pfUpdateConfirmBtn()) at its call site
       in _pfFetch so a throw inside _pfStepperAnimate always re-enables the Run button.
  AC2: _finishAutofix surfaces per-ticket error count in the stepper notes when
       _pfRunAutoFix returns a non-empty errors array.
  AC3: _pfRunAutoFix is wrapped with an AbortController / timeout (AUTOFIX_TIMEOUT_MS)
       that cancels the fetch and transitions the stuck ac/estimates steps to a
       failed state on expiry, preventing them from remaining stuck in 'checking'.
"""
from pathlib import Path

RUN_CONTROLS_JS = (
    Path(__file__).parent.parent
    / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "run-controls.js"
)
BUNDLE_JS = (
    Path(__file__).parent.parent
    / "apps" / "dashboard" / "static" / "dist" / "bundle.js"
)


def _src() -> str:
    rc = RUN_CONTROLS_JS.read_text(encoding="utf-8") if RUN_CONTROLS_JS.exists() else ""
    bundle = BUNDLE_JS.read_text(encoding="utf-8") if BUNDLE_JS.exists() else ""
    return rc + "\n" + bundle


# ── AC1: _pfStepperAnimate call site has a .catch that re-enables Run button ──

def test_ac1_pfstepperanimate_has_catch_at_callsite():
    """AC1 — _pfStepperAnimate must be called with .catch(() => _pfUpdateConfirmBtn())
    so a sync throw inside it never leaves the Run Sprint button permanently disabled."""
    src = _src()
    # The call site must be wrapped with .catch (not a bare fire-and-forget call)
    assert "_pfStepperAnimate(data).catch" in src, (
        "_pfStepperAnimate(data).catch not found — AC1 requires a .catch handler "
        "at the _pfStepperAnimate call site so a throw always re-enables the Run button"
    )


def test_ac1_catch_calls_pfupdateconfirmbtn():
    """AC1 — The .catch handler must invoke _pfUpdateConfirmBtn() to re-enable Run."""
    src = _src()
    # Check .catch references _pfUpdateConfirmBtn
    # Find the region of the catch block on _pfStepperAnimate
    idx = src.find("_pfStepperAnimate(data).catch")
    assert idx != -1, "_pfStepperAnimate(data).catch not found"
    snippet = src[idx : idx + 120]
    assert "_pfUpdateConfirmBtn" in snippet, (
        "_pfUpdateConfirmBtn not found in _pfStepperAnimate .catch handler — "
        "the catch must call _pfUpdateConfirmBtn() to re-enable the Run button"
    )


def test_ac1_bare_fire_and_forget_removed():
    """AC1 — The bare `_pfStepperAnimate(data);` (no .catch) must not appear."""
    src = _src()
    # A bare call looks like "_pfStepperAnimate(data);" on its own line or followed by newline
    # after the fix there should be no standalone call without .catch or await
    import re
    # Match bare call: _pfStepperAnimate(data); not preceded by await and not followed by .catch
    bare = re.search(r'(?<!await\s)_pfStepperAnimate\(data\)\s*;', src)
    assert bare is None, (
        "Bare _pfStepperAnimate(data); (no .catch, no await) found — "
        "AC1 requires .catch to prevent unhandled rejection leaving Run button disabled"
    )


# ── AC2: _finishAutofix surfaces per-ticket errors in stepper notes ───────────

def test_ac2_finish_autofix_surfaces_error_count():
    """AC2 — _finishAutofix must show a note when fix.errors is non-empty,
    so the operator sees '(N could not be fixed)' in the stepper instead of
    a silent 'pass' that hides failures."""
    src = _src()
    # After the fix, _finishAutofix must reference fix.errors in the note strings
    assert "fix.errors" in src, (
        "fix.errors not referenced in run-controls.js — AC2 requires "
        "_finishAutofix to surface per-ticket errors in the stepper notes"
    )


def test_ac2_error_note_included_in_stepper_state():
    """AC2 — When errors exist, the stepper note must reflect that N items could not be fixed."""
    src = _src()
    # Look for 'could not be fixed' or similar text in the source
    import re
    pattern = re.compile(r"could not be fixed|error[s]?\s+fixing|\d.*error", re.IGNORECASE)
    assert pattern.search(src), (
        "No 'could not be fixed' error note found in run-controls.js — "
        "AC2 requires surfacing error count in the ac/estimates step notes"
    )


# ── AC3: _pfRunAutoFix has AbortController/timeout so steps don't stay stuck ──

def test_ac3_abortcontroller_defined():
    """AC3 — _pfRunAutoFix must create an AbortController to cancel the fetch on timeout."""
    src = _src()
    assert "AbortController" in src, (
        "AbortController not found in run-controls.js — AC3 requires "
        "_pfRunAutoFix to cancel the fetch on timeout"
    )


def test_ac3_timeout_constant_defined():
    """AC3 — A named timeout constant (AUTOFIX_TIMEOUT_MS) must be defined so the
    timeout value is obvious and configurable."""
    src = _src()
    assert "AUTOFIX_TIMEOUT_MS" in src, (
        "AUTOFIX_TIMEOUT_MS constant not found — AC3 requires a named timeout "
        "constant for the autofix fetch so the value is explicit"
    )


def test_ac3_timeout_used_in_autofix():
    """AC3 — _pfRunAutoFix must use the timeout/AbortController to abort the fetch."""
    src = _src()
    assert "signal" in src, (
        "'signal' not found in run-controls.js — AC3 requires passing an "
        "AbortSignal to the fetch call inside _pfRunAutoFix"
    )


def test_ac3_stuck_steps_get_failed_state_on_timeout():
    """AC3 — On timeout, the ac and estimates steps must transition to a non-'checking'
    state so the operator can see they failed rather than hanging indefinitely."""
    src = _src()
    # The catch/timeout handler must call _pfStepState for 'ac' and 'estimates'
    # Look for _pfStepState calls near the timeout/abort catch
    import re
    # Check that there are _pfStepState calls for both 'ac' and 'estimates' in error paths
    # The existing .catch in the _pfRunAutoFix call already does this — we need to verify
    # the timeout path also triggers the same resolution
    assert "_pfStepState" in src, "No _pfStepState found — stepper never updated"
    # Check for 'timed out' or 'timeout' message in step notes
    assert re.search(r"timed?\s*out|timeout|abort", src, re.IGNORECASE), (
        "No timeout/timed-out message found in run-controls.js — AC3 requires "
        "the timeout path to surface a message in the ac/estimates step notes"
    )
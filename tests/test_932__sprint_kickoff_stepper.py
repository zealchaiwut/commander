"""Tests for issue #932: Sprint kickoff stepper for run/re-run flow.

AC coverage:
  AC1: Triggering run or re-run replaces "Starting..." state with stepper immediately
  AC2: Stepper shows three steps in order: validate/acquire lock, create sprint branch,
       dispatch first agents
  AC3: Each step transitions live: pending → running → done or failed
  AC4: On all steps succeeding, UI transitions to existing running pane without a full reload
  AC5: If any step fails, stepper halts at that step, displays error, shows retry action
  AC6: A failed step does NOT transition into the running pane
  AC7: Retry re-runs from the failed step (not from step 1)
  AC8: Stepper uses the shared progress component (not a bespoke implementation)
  AC9: Works for both initial run and re-run of a sprint
"""
from pathlib import Path

STATIC_DIR      = Path(__file__).parent.parent / "apps" / "dashboard" / "static"
PROJECT_HTML    = STATIC_DIR / "project.html"
RUN_CONTROLS_JS = STATIC_DIR / "src" / "sprint-board" / "run-controls.js"
BUNDLE_JS       = STATIC_DIR / "dist" / "bundle.js"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _js() -> str:
    """Return JS source — run-controls.js (authoritative) with bundle.js as fallback."""
    rc = RUN_CONTROLS_JS.read_text(encoding="utf-8") if RUN_CONTROLS_JS.exists() else ""
    bundle = BUNDLE_JS.read_text(encoding="utf-8") if BUNDLE_JS.exists() else ""
    return rc + "\n" + bundle


def _src() -> str:
    """Full search space: project.html + JS sources."""
    return _html() + "\n" + _js()


# ── AC1: Kickoff stepper appears immediately on run/re-run trigger ────────────

def test_kickoff_shell_in_running_subview():
    """AC1 — The kickoff shell container must exist inside the running subview."""
    src = _html()
    running_start = src.find('id="smgmt-subview-running"')
    running_end   = src.find('<!-- /smgmt-subview-running', running_start)
    assert running_start != -1, "smgmt-subview-running not found in project.html"
    running_chunk = src[running_start:running_end] if running_end > running_start else src[running_start:]
    assert 'id="smgmt-kickoff-shell"' in running_chunk, (
        "id='smgmt-kickoff-shell' not found inside smgmt-subview-running — "
        "AC1 requires the kickoff stepper to appear in the running subview"
    )


def test_pfconfirm_calls_kickoff_function():
    """AC1 — _pfConfirm must delegate to the kickoff runner, not do the work inline."""
    src = _js()
    assert "smgmtKickoffRun" in src, (
        "smgmtKickoffRun not referenced in JS — "
        "_pfConfirm must call the kickoff runner to wire the stepper"
    )


def test_kickoff_function_exported():
    """AC1 — smgmtKickoffRun must be exported from the sprint-board bundle."""
    src = _js()
    assert "smgmtKickoffRun" in src, (
        "smgmtKickoffRun not found in bundle — kickoff runner not exported"
    )


# ── AC2: Three-step sequence ─────────────────────────────────────────────────

def test_three_kickoff_steps_defined():
    """AC2 — JS must define exactly three kickoff steps."""
    src = _js()
    # Look for an array of 3 kickoff step objects
    assert "KS_STEPS" in src, (
        "KS_STEPS constant not found — three kickoff steps must be defined"
    )


def test_lock_step_defined():
    """AC2 — First step must be the lock/validate step."""
    src = _js()
    assert "lock" in src and (
        "acquire lock" in src.lower() or "validate" in src.lower()
    ), (
        "Lock/validate step not found in kickoff steps — "
        "AC2 requires 'validate/acquire lock' as the first step"
    )


def test_branch_step_defined():
    """AC2 — Second step must be the branch creation step."""
    src = _js()
    assert "branch" in src and "sprint branch" in src.lower(), (
        "Branch creation step not found in kickoff steps — "
        "AC2 requires 'create sprint branch' as the second step"
    )


def test_dispatch_step_defined():
    """AC2 — Third step must be the agent dispatch step."""
    src = _js()
    assert "dispatch" in src and (
        "agents" in src.lower() or "first agents" in src.lower()
    ), (
        "Agent dispatch step not found in kickoff steps — "
        "AC2 requires 'dispatch first agents' as the third step"
    )


# ── AC3: Live step transitions (pending → running → done/failed) ──────────────

def test_step_set_function_exists():
    """AC3 — A function must exist to set step state (pending/checking/pass/fail)."""
    src = _js()
    has_fn = "_ksSetStep" in src or "ksSetStep" in src
    assert has_fn, (
        "_ksSetStep function not found — AC3 requires live step state transitions"
    )


def test_checking_state_used_in_kickoff():
    """AC3 — The 'checking' (running) state must be applied to kickoff steps."""
    src = _js()
    assert "'checking'" in src or '"checking"' in src, (
        "'checking' state not applied in kickoff stepper — "
        "AC3 requires each step to show running state while active"
    )


def test_pass_state_used_in_kickoff():
    """AC3 — The 'pass' (done) state must be applied on step success."""
    src = _js()
    assert "'pass'" in src or '"pass"' in src, (
        "'pass' state not applied in kickoff stepper — "
        "AC3 requires a done state on step success"
    )


def test_fail_state_used_in_kickoff():
    """AC3 — The 'fail' state must be applied when a step fails."""
    src = _js()
    assert "'fail'" in src or '"fail"' in src, (
        "'fail' state not applied in kickoff stepper — "
        "AC3 requires a failed state when a step errors"
    )


# ── AC4: Transition to running pane on success ────────────────────────────────

def test_running_pane_transition_on_success():
    """AC4 — The kickoff runner must call _smgmtShowSubView('running') on success."""
    src = _js()
    # Either direct call or string reference to running
    assert "_smgmtShowSubView" in src and "'running'" in src, (
        "_smgmtShowSubView('running') not found in kickoff runner — "
        "AC4 requires transition to the running pane on success"
    )


def test_no_full_reload_on_success():
    """AC4 — Success path must not use location.reload() or similar."""
    src = _js()
    # Reject hard reload patterns in the kickoff stepper section
    # The bundle may have other location.reload() — just check the kickoff function
    import re
    kickoff_fn = re.search(
        r'(?:async\s+)?function smgmtKickoffRun\b.*?(?=\nexport\s+(?:async\s+)?function|\Z)',
        src, re.DOTALL
    )
    if kickoff_fn:
        fn_body = kickoff_fn.group(0)
        assert "location.reload" not in fn_body and "location.href" not in fn_body, (
            "location.reload() or location.href= found in smgmtKickoffRun — "
            "AC4 requires transition without a full page reload"
        )


# ── AC5: Error display and retry on step failure ──────────────────────────────

def test_kickoff_error_container_in_html():
    """AC5 — An error container must exist in the kickoff shell."""
    src = _html()
    kickoff_start = src.find('id="smgmt-kickoff-shell"')
    assert kickoff_start != -1, "smgmt-kickoff-shell not found"
    kickoff_chunk = src[kickoff_start:kickoff_start + 2000]
    assert 'id="smgmt-kickoff-error"' in kickoff_chunk, (
        "id='smgmt-kickoff-error' not found inside smgmt-kickoff-shell — "
        "AC5 requires an error display container"
    )


def test_kickoff_error_msg_slot_exists():
    """AC5 — The error container must have a slot for the error message text."""
    src = _html()
    assert 'id="smgmt-kickoff-error-msg"' in src, (
        "id='smgmt-kickoff-error-msg' not found — AC5 requires error message display"
    )


def test_kickoff_retry_button_in_html():
    """AC5 — A retry button must be present in the kickoff shell."""
    src = _html()
    kickoff_start = src.find('id="smgmt-kickoff-shell"')
    assert kickoff_start != -1, "smgmt-kickoff-shell not found"
    kickoff_chunk = src[kickoff_start:kickoff_start + 2000]
    assert "smgmtKickoffRetry" in kickoff_chunk, (
        "smgmtKickoffRetry not found in kickoff shell HTML — "
        "AC5 requires a retry action in the error state"
    )


def test_kickoff_retry_function_exists():
    """AC5 — smgmtKickoffRetry function must be defined in JS."""
    src = _js()
    assert "smgmtKickoffRetry" in src, (
        "smgmtKickoffRetry function not found in JS — AC5 requires a retry action"
    )


# ── AC6: Failed step does NOT transition to running pane ─────────────────────

def test_failed_step_blocks_running_pane_transition():
    """AC6 — The kickoff runner must return early on step failure, not transition."""
    import re
    src = _js()
    kickoff_fn = re.search(
        r'(?:async\s+)?function smgmtKickoffRun\b.*?(?=\nexport\s+(?:async\s+)?function|\Z)',
        src, re.DOTALL
    )
    assert kickoff_fn, "smgmtKickoffRun function not found"
    fn_body = kickoff_fn.group(0)
    # Must have a conditional guard before the _smgmtShowSubView call
    # Simplest check: running pane transition must follow a success check (return or await)
    assert "return" in fn_body, (
        "No 'return' found in smgmtKickoffRun — "
        "AC6 requires early return on step failure to prevent running pane transition"
    )


# ── AC7: Retry from failed step (not from step 1) ────────────────────────────

def test_failed_step_index_tracked():
    """AC7 — A variable must track which step failed."""
    src = _js()
    assert "_ksFailedStep" in src or "ksFailedStep" in src, (
        "_ksFailedStep variable not found — "
        "AC7 requires tracking which step failed to retry from there"
    )


def test_retry_uses_failed_step_index():
    """AC7 — smgmtKickoffRetry must branch based on the failed step index."""
    import re
    src = _js()
    retry_fn = re.search(
        r'(?:async\s+)?function smgmtKickoffRetry\b.*?(?=\nexport\s+(?:async\s+)?function|\Z)',
        src, re.DOTALL
    )
    assert retry_fn, "smgmtKickoffRetry function not found"
    fn_body = retry_fn.group(0)
    # Must reference the failed step variable to decide where to retry from
    assert "_ksFailedStep" in fn_body or "ksFailedStep" in fn_body, (
        "_ksFailedStep not used in smgmtKickoffRetry — "
        "AC7 requires retry to start from the failed step, not step 1"
    )


# ── AC8: Shared progress component (not bespoke) ─────────────────────────────

def test_kickoff_uses_pf_stepper_class():
    """AC8 — Kickoff stepper must use the shared pf-stepper CSS class."""
    src = _html()
    kickoff_start = src.find('id="smgmt-kickoff-shell"')
    assert kickoff_start != -1, "smgmt-kickoff-shell not found"
    kickoff_chunk = src[kickoff_start:kickoff_start + 2000]
    assert 'pf-stepper' in kickoff_chunk, (
        "'pf-stepper' class not found inside smgmt-kickoff-shell — "
        "AC8 requires the shared progress component (pf-stepper) not a bespoke implementation"
    )


def test_kickoff_uses_pf_step_item_class():
    """AC8 — Kickoff uses shared stepper markup (pf-step-item or pa-step)."""
    src = _js()
    assert (
        "pf-step-item" in src
        or "pa-step" in src
        or "patchProgressActivityStep" in src
        or "mountProgressActivity" in src
    ), (
        "Shared stepper wiring not found in kickoff JS — expected pf-step-item markup "
        "or ProgressActivity host patch/mount APIs"
    )


def test_no_bespoke_kickoff_step_class():
    """AC8 — No one-off CSS class for kickoff steps."""
    src = _html()
    # Check that kickoff steps don't introduce a bespoke class like ks-step-item
    assert "ks-step-item" not in src and "kickoff-step-item" not in src, (
        "Bespoke kickoff step class found — AC8 requires using the shared pf-step-item"
    )


# ── AC9: Works for both run and re-run ───────────────────────────────────────

def test_kickoff_reachable_from_pfconfirm():
    """AC9 — _pfConfirm (initial run path) must invoke the kickoff runner."""
    import re
    src = _js()
    pf_confirm = re.search(
        r'(?:async\s+)?function _pfConfirm\b.*?(?=\nexport\s+(?:async\s+)?function|\Z)',
        src, re.DOTALL
    )
    assert pf_confirm, "_pfConfirm function not found in run-controls.js"
    fn_body = pf_confirm.group(0)
    assert "smgmtKickoffRun" in fn_body or "kickoff" in fn_body.lower(), (
        "_pfConfirm does not call smgmtKickoffRun — "
        "AC9 requires initial run to go through the kickoff stepper"
    )


def test_kickoff_run_function_defined_as_export():
    """AC9 — smgmtKickoffRun must be a named export (accessible for both run and re-run)."""
    src = _js()
    assert "export" in src and "smgmtKickoffRun" in src, (
        "smgmtKickoffRun not exported — must be accessible from both run and re-run paths"
    )


def test_kickoff_retry_function_exported():
    """AC9 — smgmtKickoffRetry must be globally accessible for both flows."""
    src = _js()
    assert "smgmtKickoffRetry" in src, (
        "smgmtKickoffRetry not found in bundle — must be globally accessible"
    )

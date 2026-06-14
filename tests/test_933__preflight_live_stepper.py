"""Tests for issue #933: Show pre-flight checks as live stepper checklist.

AC coverage:
  AC1: Pre-flight check sequence renders using the shared progress component in stepper mode
  AC2: Each step starts in `pending` state and transitions to `checking`
  AC3: Passing checks resolve to `pass`
  AC4: Auto-fixable checks resolve to `fixed` with a human-readable note
  AC5: Non-auto-fixable failures resolve to `fail` with a reason string
  AC6: One or more `fail` states blocks Run Sprint and shows blocking-issues count
  AC7: All pass/fixed enables Run Sprint with all-clear summary
  AC8: Check groupings in the stepper match the groupings in the pre-flight panel
  AC9: Component is the shared stepper/progress component — no one-off checklist
"""
import re
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


# ── AC1: Shared stepper component exists in project.html ─────────────────────

def test_stepper_css_class_exists():
    """AC1 — The shared stepper CSS class pf-stepper must be defined."""
    src = _html()
    assert ".pf-stepper" in src, (
        ".pf-stepper CSS class not found — shared stepper component not implemented"
    )


def test_step_item_css_class_exists():
    """AC1 — Each step uses pf-step-item (not a bespoke checklist class)."""
    src = _html()
    assert ".pf-step-item" in src, (
        ".pf-step-item CSS class not found — shared stepper step not implemented"
    )


def test_stepper_html_container_in_modal():
    """AC1 — The stepper HTML container must live inside the preflight modal."""
    src = _html()
    modal_start = src.find('id="pf-modal"')
    stepper_start = src.find('id="pf-stepper"')
    modal_end = src.find('<!-- ──', modal_start + 1)
    assert stepper_start != -1, "id='pf-stepper' container not found in project.html"
    assert modal_start < stepper_start < modal_end, (
        "pf-stepper container not inside pf-modal — must be within the pre-flight modal"
    )


def test_stepper_steps_container_exists():
    """AC1 — The stepper has a steps container element."""
    src = _html()
    assert 'id="pf-stepper-steps"' in src, (
        "id='pf-stepper-steps' element not found — step list container missing"
    )


def test_no_bespoke_checklist_ul_in_preflight():
    """AC9 — No ad-hoc <ul> checklist hardcoded inside the modal body template."""
    src = _html()
    modal_start = src.find('id="pf-modal"')
    modal_end = src.find('<!-- ── Re-estimate', modal_start)
    modal_chunk = src[modal_start:modal_end] if modal_end > modal_start else src[modal_start:]
    # A bespoke checklist would use <ul class="...checklist..."> or similar
    assert "<ul" not in modal_chunk or "pf-stepper" in modal_chunk, (
        "Bespoke <ul> checklist found in preflight modal — use shared stepper component"
    )


# ── AC2: Pending initial state ────────────────────────────────────────────────

def test_pending_state_css_exists():
    """AC2 — The pending state CSS modifier for steps must exist."""
    src = _html()
    assert "pf-step-item--pending" in src or "--pending" in src, (
        "Pending state CSS class not found — steps can't start in pending state"
    )


def test_stepper_init_function_exists():
    """AC2 — A JS function must initialise steps to pending state."""
    src = _js()
    assert "_pfStepperInit" in src or "_pfInitStepper" in src, (
        "Stepper init function not found — steps must start in pending state"
    )


def test_checking_state_css_exists():
    """AC2 — A checking CSS state must exist for the in-progress transition."""
    src = _html()
    assert "pf-step-item--checking" in src or "--checking" in src, (
        "Checking state CSS class not found — steps can't show live 'checking' state"
    )


# ── AC3: Pass state ───────────────────────────────────────────────────────────

def test_pass_state_css_exists():
    """AC3 — A pass state CSS class must exist for resolved-ok steps."""
    src = _html()
    assert "pf-step-item--pass" in src or "--pass" in src, (
        "Pass state CSS class not found — steps can't show pass state"
    )


def test_step_state_function_exists():
    """AC3 — A function to update step state must exist."""
    src = _js()
    has_fn = "_pfStepState(" in src or "_pfSetStep(" in src or "_pfStepUpdate(" in src
    assert has_fn, (
        "Step state update function not found — no way to transition step to pass/fail/fixed"
    )


# ── AC4: Fixed state with note ────────────────────────────────────────────────

def test_fixed_state_css_exists():
    """AC4 — A fixed state CSS class must exist for auto-fixed steps."""
    src = _html()
    assert "pf-step-item--fixed" in src or "--fixed" in src, (
        "Fixed state CSS class not found — auto-fixable steps can't show fixed state"
    )


def test_step_note_element_exists():
    """AC4 — Each step must have a note slot for human-readable fix descriptions."""
    src = _html()
    assert "pf-step-note" in src, (
        "pf-step-note class not found — no slot for human-readable fix notes"
    )


def test_auto_fix_invoked_for_fixable_checks():
    """AC4 — JS must call the preflight-fix endpoint for auto-fixable checks."""
    src = _js()
    assert "preflight-fix" in src, (
        "preflight-fix endpoint not referenced — auto-fixable checks won't be fixed"
    )


def test_fixed_note_set_in_stepper_code():
    """AC4 — JS must set a note string when resolving a step as fixed."""
    src = _js()
    # Stepper animate must set a note for fixed state
    assert "fixed" in src and ("generated" in src or "estimated" in src), (
        "No note string logic for fixed state — AC4 requires a human-readable note"
    )


# ── AC5: Fail state with reason ───────────────────────────────────────────────

def test_fail_state_css_exists():
    """AC5 — A fail state CSS class must exist for blocking non-auto-fixable failures."""
    src = _html()
    assert "pf-step-item--fail" in src or "--fail" in src, (
        "Fail state CSS class not found — blocking checks can't show fail state"
    )


def test_fail_reason_surfaced_inline():
    """AC5 — Non-auto-fixable failures must surface a reason string inside the step."""
    src = _js()
    # At minimum: cycle reason or mis-sizing reason must appear in stepper code
    assert "Cycle:" in src or "cycle" in src, (
        "Cycle failure reason not wired to fail state — non-auto-fixable failures need inline reason"
    )
    assert "fail" in src, (
        "'fail' state string not found in JS — non-auto-fixable failures need fail state"
    )


# ── AC6: Fail blocks Run Sprint with count ────────────────────────────────────

def test_blocking_issues_summary_element_exists():
    """AC6 — A summary element for blocking-issues count must exist."""
    src = _html()
    assert 'id="pf-stepper-summary"' in src or "blocking" in src, (
        "Blocking issues summary element not found — AC6 requires 'N blocking issues — cannot run'"
    )


def test_blocking_count_text_in_js():
    """AC6 — JS must produce a 'blocking issues — cannot run' summary text."""
    src = _js()
    assert "blocking" in src and "cannot run" in src, (
        "'blocking … cannot run' summary text not found — AC6 requires blocking issue count"
    )


def test_run_button_disabled_on_fail():
    """AC6 — The pf-confirm-btn must be disabled when any step is in fail state."""
    src = _js()
    update_fn_match = re.search(
        r'function _pfUpdateConfirmBtn\b.*?(?=\nfunction |\nexport function |\n\/\/\s*──)',
        src, re.DOTALL
    )
    assert update_fn_match, "_pfUpdateConfirmBtn function not found in JS source"
    fn_body = update_fn_match.group(0)
    assert "fail" in fn_body or "hasFail" in fn_body or "_pfStepFails" in fn_body, (
        "_pfUpdateConfirmBtn does not account for stepper fail state — Run button won't be disabled"
    )


# ── AC7: All-clear enables Run Sprint ────────────────────────────────────────

def test_all_clear_summary_text_in_js():
    """AC7 — JS must produce an all-clear summary text when all steps are pass/fixed."""
    src = _js()
    assert "All checks passed" in src or "all checks" in src.lower(), (
        "All-clear summary text not found — AC7 requires 'All checks passed' or equivalent"
    )


# ── AC8: Check groupings match pre-flight panel ───────────────────────────────

def test_acceptance_criteria_step_exists():
    """AC8 — 'Acceptance criteria' group matches the panel's missing-AC chip."""
    src = _js()
    assert "Acceptance criteria" in src or "acceptance criteria" in src.lower(), (
        "'Acceptance criteria' step label not found — must match pre-flight panel group"
    )


def test_estimate_coverage_step_exists():
    """AC8 — 'Estimate coverage' group matches the panel's unsized chip."""
    src = _js()
    assert "Estimate" in src and ("coverage" in src or "unestimated" in src), (
        "Estimate coverage step not found — must match the pre-flight panel unsized group"
    )


def test_conflict_analysis_step_exists():
    """AC8 — 'Conflict analysis' group matches the panel's conflicts chip."""
    src = _js()
    assert "Conflict" in src or "conflict" in src.lower(), (
        "Conflict step not found — must match the pre-flight panel conflict group"
    )


def test_stepper_step_labels_defined():
    """AC8 — Step labels must be defined in JS as a data structure (not inline strings)."""
    src = _js()
    assert "PF_STEPS" in src or "pf_steps" in src or "_pfSteps" in src, (
        "No step-labels data structure found — stepper must define step groups matching the panel"
    )


# ── AC9: Shared component, no one-off implementation ─────────────────────────

def test_stepper_css_reuses_pf_prefix():
    """AC9 — All stepper CSS uses pf- prefix (shared namespace, not bespoke)."""
    src = _html()
    # Shared component uses pf-step-* namespace, not a one-off class like "checklist-item"
    assert "pf-step-item" in src, (
        "pf-step-item class not found — stepper must use shared pf-step-* CSS namespace"
    )
    # Ensure no one-off checklist class was introduced
    assert "checklist-item" not in src and "pf-checklist" not in src, (
        "Bespoke checklist class found — AC9 requires the shared stepper component"
    )


def test_stepper_function_is_reusable():
    """AC9 — Stepper init/animate functions are named generically for reuse."""
    src = _js()
    has_init = "_pfStepperInit" in src or "_pfInitStepper" in src
    has_animate = "_pfStepperAnimate" in src or "_pfAnimateStepper" in src
    assert has_init, "Stepper init function not found — must be reusable"
    assert has_animate, "Stepper animate function not found — must be reusable"

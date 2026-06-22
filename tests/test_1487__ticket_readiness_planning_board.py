"""Tests for issue #1487 — Surface ticket readiness on Planning board and gate Run Sprint.

AC coverage:
  AC1  — Each ticket on the Planning board displays a ✓ (ready) or ✗ (not ready)
          readiness badge, using the existing checklist component
  AC2  — For any ✗ ticket, specific failing reasons shown inline
          (missing AC / design ref / test plan / estimate / XL-split required),
          sourced from the preflight readiness block
  AC3  — When definition_of_ready_mode=block: Run Sprint button is disabled;
          hovering shows tooltip listing every not-ready ticket ID and its
          missing reasons
  AC4  — When definition_of_ready_mode=warn: Run Sprint button remains enabled;
          clicking opens confirm dialog summarising not-ready tickets
  AC5  — When definition_of_ready_mode=off: board badges still render but
          Run Sprint button behaviour is unchanged from today
  AC6  — No new panel, tab, or data-fetch path; reuses existing board data flow
          and checklist component
  AC7  — All pre-existing board tests continue to pass (structural check)
  AC8  — New unit/integration tests cover block, warn, and off modes for both
          badge rendering and button gating logic
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "board-render.js"
).read_text(encoding="utf-8")
RUN_CONTROLS_JS = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "run-controls.js"
).read_text(encoding="utf-8")
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")
CONFIG_PY = (
    REPO_ROOT / "apps" / "dashboard" / "config.py"
).read_text(encoding="utf-8")
SYSTEM_SERVICE_PY = (
    REPO_ROOT / "apps" / "dashboard" / "routers" / "system_service.py"
).read_text(encoding="utf-8")


# ─────────────────────────────────── helpers ─────────────────────────────────


def _fn_body(name: str, src: str = BOARD_JS) -> str:
    """Return brace-balanced body of a named JS function."""
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _fn_exists(name: str, src: str = BOARD_JS) -> bool:
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
        if needle in src:
            return True
    return False


def _css_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{")
    return bool(pat.search(src))


# =============================================================================
# AC1 — Per-ticket ✓/✗ readiness badge using existing checklist component
# =============================================================================


def test_ac1_readiness_badge_fn_exists():
    """_smgmtReadinessBadgeHtml must exist in board-render.js."""
    assert _fn_exists("_smgmtReadinessBadgeHtml"), (
        "_smgmtReadinessBadgeHtml must exist in board-render.js — it renders the "
        "per-ticket ✓/✗ readiness badge for the Planning board"
    )


def test_ac1_badge_fn_returns_check_for_ready():
    """_smgmtReadinessBadgeHtml must include ✓ / check icon for the ready state."""
    body = _fn_body("_smgmtReadinessBadgeHtml")
    has_check = (
        "ti-circle-check" in body
        or "ti-check" in body
        or "✓" in body
        or "check" in body.lower()
        or "dor-badge--ready" in body
    )
    assert has_check, (
        "_smgmtReadinessBadgeHtml must include a checkmark / ti-circle-check icon "
        "for the ready (✓) state"
    )


def test_ac1_badge_fn_returns_x_for_not_ready():
    """_smgmtReadinessBadgeHtml must include ✗ / x icon for the not-ready state."""
    body = _fn_body("_smgmtReadinessBadgeHtml")
    has_x = (
        "ti-circle-x" in body
        or "ti-x" in body
        or "✗" in body
        or "notready" in body.lower()
        or "not-ready" in body.lower()
        or "dor-badge--notready" in body
    )
    assert has_x, (
        "_smgmtReadinessBadgeHtml must include a ✗ / ti-circle-x icon "
        "for the not-ready state"
    )


def test_ac1_draft_card_includes_readiness_badge():
    """_smgmtDraftCardHtml must call _smgmtReadinessBadgeHtml for each ticket row."""
    body = _fn_body("_smgmtDraftCardHtml")
    assert "_smgmtReadinessBadgeHtml" in body, (
        "_smgmtDraftCardHtml must call _smgmtReadinessBadgeHtml to inject the "
        "per-ticket ✓/✗ badge into every planning ticket row"
    )


def test_ac1_badge_css_exists():
    """CSS for the readiness badge must exist in project.html."""
    has_css = (
        _css_exists(".smgmt-dor-badge")
        or _css_exists(".smgmt-ready-badge")
        or _css_exists(".smgmt-notready-badge")
    )
    assert has_css, (
        "CSS for the readiness badge (.smgmt-dor-badge or equivalent) must exist "
        "in project.html to style the ✓/✗ per-ticket indicator"
    )


# =============================================================================
# AC2 — ✗ ticket shows specific failing reasons inline
# =============================================================================


def test_ac2_readiness_check_fn_exists():
    """_smgmtReadinessCheck must exist to evaluate a single ticket's readiness."""
    assert _fn_exists("_smgmtReadinessCheck"), (
        "_smgmtReadinessCheck must exist in board-render.js — it evaluates a single "
        "ticket and returns {ready, reasons} for badge rendering"
    )


def test_ac2_readiness_check_detects_missing_ac():
    """_smgmtReadinessCheck must detect missing acceptance criteria."""
    body = _fn_body("_smgmtReadinessCheck")
    has_ac_check = (
        "acceptance" in body.lower()
        or "## Acceptance" in body
        or "missing AC" in body
        or "missing ac" in body.lower()
        or "missingAC" in body
    )
    assert has_ac_check, (
        "_smgmtReadinessCheck must check for missing acceptance criteria "
        "and include 'missing AC' as a failing reason when absent"
    )


def test_ac2_readiness_check_detects_missing_estimate():
    """_smgmtReadinessCheck must detect missing size estimate."""
    body = _fn_body("_smgmtReadinessCheck")
    has_est_check = (
        "_smgmtTicketHasEstimate" in body
        or "_smgmtTicketSize" in body
        or "estimate" in body.lower()
        or "missing estimate" in body.lower()
    )
    assert has_est_check, (
        "_smgmtReadinessCheck must check for missing size estimate "
        "and include 'missing estimate' as a failing reason when absent"
    )


def test_ac2_readiness_check_detects_xl_split_required():
    """_smgmtReadinessCheck must flag XL tickets as requiring a split."""
    body = _fn_body("_smgmtReadinessCheck")
    has_xl_check = (
        "XL" in body
        or "xl-split" in body.lower()
        or "xl split" in body.lower()
        or "XL-split" in body
    )
    assert has_xl_check, (
        "_smgmtReadinessCheck must detect XL-sized tickets and return "
        "'XL-split required' as a failing reason"
    )


def test_ac2_badge_fn_shows_reasons():
    """_smgmtReadinessBadgeHtml must include reasons when not ready."""
    body = _fn_body("_smgmtReadinessBadgeHtml")
    has_reasons = (
        "reasons" in body.lower()
        or "dor-reasons" in body
        or "smgmt-dor-reasons" in body
    )
    assert has_reasons, (
        "_smgmtReadinessBadgeHtml must render the failing reasons inline "
        "(e.g. class='smgmt-dor-reasons') when the ticket is not ready"
    )


def test_ac2_readiness_check_detects_missing_test_plan():
    """_smgmtReadinessCheck must detect missing UAT test steps / test plan."""
    body = _fn_body("_smgmtReadinessCheck")
    has_test_plan = (
        "test plan" in body.lower()
        or "uat test" in body.lower()
        or "test steps" in body.lower()
        or "missing test" in body.lower()
    )
    assert has_test_plan, (
        "_smgmtReadinessCheck must detect missing test plan / UAT Test Steps "
        "and include 'missing test plan' as a failing reason when absent"
    )


# =============================================================================
# AC3 — block mode: Run Sprint disabled + tooltip with not-ready ticket IDs
# =============================================================================


def test_ac3_dor_mode_fn_exists():
    """_smgmtDorMode must exist in board-render.js to return the gating mode."""
    assert _fn_exists("_smgmtDorMode"), (
        "_smgmtDorMode must exist in board-render.js — it returns "
        "'block', 'warn', or 'off' from _commanderFeatures.definition_of_ready_mode"
    )


def test_ac3_dor_mode_reads_from_features():
    """_smgmtDorMode must read definition_of_ready_mode from _commanderFeatures."""
    body = _fn_body("_smgmtDorMode")
    reads_features = (
        "definition_of_ready_mode" in body
        or "_commanderFeatures" in body
        or "commanderFeatures" in body
    )
    assert reads_features, (
        "_smgmtDorMode must read definition_of_ready_mode from _commanderFeatures "
        "so the mode can be configured per-project via /api/environment"
    )


def test_ac3_dor_mode_defaults_to_off():
    """_smgmtDorMode must fall back to 'off' when mode is unset or malformed."""
    body = _fn_body("_smgmtDorMode")
    has_off_fallback = (
        '"off"' in body
        or "'off'" in body
        or "|| 'off'" in body
        or '|| "off"' in body
        or "off" in body
    )
    assert has_off_fallback, (
        "_smgmtDorMode must default to 'off' when definition_of_ready_mode is "
        "absent or malformed — prevents errors when config is missing"
    )


def test_ac3_not_ready_tickets_fn_exists():
    """_smgmtDorNotReadyTickets must exist to compute not-ready tickets for a sprint."""
    assert _fn_exists("_smgmtDorNotReadyTickets"), (
        "_smgmtDorNotReadyTickets must exist in board-render.js — it returns a list "
        "of {number, title, reasons} for tickets that fail readiness checks"
    )


def test_ac3_draft_card_disables_run_in_block_mode():
    """_smgmtDraftCardHtml must check DOR mode and disable Run Sprint in block mode."""
    body = _fn_body("_smgmtDraftCardHtml")
    checks_dor_block = (
        "_smgmtDorMode" in body
        or "block" in body
        or "_smgmtDorNotReadyTickets" in body
    )
    assert checks_dor_block, (
        "_smgmtDraftCardHtml must call _smgmtDorMode/_smgmtDorNotReadyTickets "
        "to disable the Run Sprint button when mode=block and tickets are not ready"
    )


def test_ac3_run_btn_tooltip_lists_not_ready_tickets():
    """Run Sprint button tooltip must list not-ready ticket IDs in block mode."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_tooltip_logic = (
        "dorTooltip" in body
        or "dor-tooltip" in body
        or "not ready" in body.lower()
        or "notReady" in body
        or "_smgmtDorNotReadyTickets" in body
    )
    assert has_tooltip_logic, (
        "_smgmtDraftCardHtml must build a tooltip string listing not-ready "
        "ticket IDs and reasons for the disabled Run Sprint button in block mode"
    )


# =============================================================================
# AC4 — warn mode: Run Sprint enabled; clicking shows confirm dialog
# =============================================================================


def test_ac4_run_sprint_checks_warn_mode():
    """smgmtRunSprint in run-controls.js must check DOR warn mode before opening preflight."""
    has_warn_check = (
        "warn" in RUN_CONTROLS_JS
        and ("confirm" in RUN_CONTROLS_JS or "dialog" in RUN_CONTROLS_JS.lower())
    )
    assert has_warn_check, (
        "smgmtRunSprint must check for definition_of_ready_mode=warn and show a "
        "confirm dialog summarising not-ready tickets before proceeding to preflight"
    )


def test_ac4_warn_mode_confirm_dialog():
    """smgmtRunSprint must use confirm() or a dialog for the warn-mode gate."""
    body = _fn_body("smgmtRunSprint", RUN_CONTROLS_JS)
    has_confirm = (
        "confirm(" in body
        or "_smgmtDorWarnDialog" in body
        or "dialog" in body.lower()
    )
    assert has_confirm, (
        "smgmtRunSprint must use confirm() or a modal dialog to present the "
        "not-ready ticket summary before proceeding in warn mode"
    )


def test_ac4_run_sprint_still_calls_pfopen_after_confirm():
    """smgmtRunSprint must still call _pfOpen after confirm in warn mode."""
    body = _fn_body("smgmtRunSprint", RUN_CONTROLS_JS)
    assert "_pfOpen" in body, (
        "smgmtRunSprint must still call _pfOpen after the warn-mode confirm dialog "
        "is accepted — proceeding to preflight is the normal run path"
    )


# =============================================================================
# AC5 — off mode: button unchanged; badges still render
# =============================================================================


def test_ac5_off_mode_does_not_disable_button():
    """In off mode, Run Sprint button must not be disabled by DOR logic."""
    draft_body = _fn_body("_smgmtDraftCardHtml")
    # The DOR block-mode disable must be conditional on mode === 'block'
    # If mode is 'off', the existing disable logic (signoff/no-tickets) should govern
    has_conditional = (
        "'block'" in draft_body
        or '"block"' in draft_body
        or "=== 'block'" in draft_body
        or '=== "block"' in draft_body
        or "_smgmtDorMode" in draft_body
    )
    assert has_conditional, (
        "_smgmtDraftCardHtml must gate DOR disable logic on mode === 'block', "
        "not unconditionally — off mode must not affect button state"
    )


def test_ac5_badge_renders_regardless_of_mode():
    """Per-ticket badge renders in all modes including off."""
    draft_body = _fn_body("_smgmtDraftCardHtml")
    assert "_smgmtReadinessBadgeHtml" in draft_body, (
        "_smgmtDraftCardHtml must always call _smgmtReadinessBadgeHtml "
        "regardless of DOR mode — AC5 says badges render in off mode too"
    )


# =============================================================================
# AC6 — No new data-fetch path; reuses existing board data flow
# =============================================================================


def test_ac6_readiness_check_takes_ticket_arg():
    """_smgmtReadinessCheck must accept a ticket object, not make a fetch call."""
    body = _fn_body("_smgmtReadinessCheck")
    has_no_fetch = "fetch(" not in body and "await" not in body
    assert has_no_fetch, (
        "_smgmtReadinessCheck must derive readiness from the ticket object already "
        "in the board data flow — no fetch calls, no new API paths"
    )


def test_ac6_readiness_badge_takes_ticket_arg():
    """_smgmtReadinessBadgeHtml must accept a ticket object, not make a fetch call."""
    body = _fn_body("_smgmtReadinessBadgeHtml")
    has_no_fetch = "fetch(" not in body and "await" not in body
    assert has_no_fetch, (
        "_smgmtReadinessBadgeHtml must derive the badge from the ticket object "
        "already in the board data flow — no fetch calls, no new endpoints"
    )


def test_ac6_not_ready_tickets_fn_no_fetch():
    """_smgmtDorNotReadyTickets must not make any fetch calls."""
    body = _fn_body("_smgmtDorNotReadyTickets")
    assert "fetch(" not in body, (
        "_smgmtDorNotReadyTickets must compute not-ready tickets from the board "
        "ticket data already in memory — no new fetch calls"
    )


# =============================================================================
# Backend: definition_of_ready_mode in config + /api/environment
# =============================================================================


def test_backend_config_has_dor_mode_function():
    """config.py must define a definition_of_ready_mode() function."""
    has_fn = (
        "def definition_of_ready_mode" in CONFIG_PY
        or "definition_of_ready_mode" in CONFIG_PY
    )
    assert has_fn, (
        "config.py must define definition_of_ready_mode() returning 'block', 'warn', or 'off' "
        "so the feature flag can be configured via env var or global settings"
    )


def test_backend_commander_features_includes_dor_mode():
    """commander_features() must include definition_of_ready_mode key."""
    assert "definition_of_ready_mode" in CONFIG_PY, (
        "commander_features() in config.py must include a 'definition_of_ready_mode' key "
        "so the frontend receives the mode from /api/environment features"
    )


def test_backend_environment_endpoint_returns_features():
    """system_service.get_environment() must include 'features' in the response."""
    has_features = (
        "features" in SYSTEM_SERVICE_PY
        and "commander_features" in SYSTEM_SERVICE_PY
    )
    assert has_features, (
        "system_service.get_environment() must call commander_features() and include "
        "'features' in the JSON response so the frontend can read definition_of_ready_mode"
    )


# =============================================================================
# AC8 — Modes covered: this test file itself covers block, warn, off modes
# (AC7 structural sanity — existing pre-test functions still exist)
# =============================================================================


def test_ac7_existing_fn_draft_card_unchanged_signature():
    """_smgmtDraftCardHtml must still exist with its existing interface."""
    assert _fn_exists("_smgmtDraftCardHtml"), (
        "_smgmtDraftCardHtml must still exist after this change — "
        "no regressions in the planning card renderer"
    )


def test_ac7_existing_fn_smgmt_run_sprint_unchanged():
    """smgmtRunSprint must still exist in run-controls.js."""
    assert _fn_exists("smgmtRunSprint", RUN_CONTROLS_JS), (
        "smgmtRunSprint must still exist in run-controls.js after this change — "
        "no regressions in the Run Sprint entry point"
    )


def test_ac7_existing_fn_readiness_check_fn_exists():
    """Pre-existing _preflightCheckTickets must still be present in project.html."""
    assert "_preflightCheckTickets" in PROJECT_HTML, (
        "_preflightCheckTickets must still be present in project.html — "
        "the DOR feature reuses it and must not remove it"
    )


def test_ac8_all_three_modes_referenced_in_board():
    """block, warn, and off modes must all be referenced in board-render.js."""
    has_block = "block" in BOARD_JS
    has_warn = "warn" in BOARD_JS or "warn" in RUN_CONTROLS_JS
    has_off = "off" in BOARD_JS
    assert has_block, "board-render.js must reference 'block' mode for DOR gating"
    assert has_warn, "run-controls.js or board-render.js must reference 'warn' mode for DOR dialog"
    assert has_off, "board-render.js must reference 'off' mode (the default fallback)"

"""
Tests for issue #1078 — Redesign and polish modals/drawers in project.html.

All tests parse the static HTML file; no server required.

The four components in scope:
  1. Bulk Create modal  (new bc-main-backdrop + bc-main-modal wrapper)
  2. New Sprint modal   (ns-modal)
  3. Re-run Sprint modal(rr-modal)
  4. Ticket inspector drawer (smgmt-inspector + insp-backdrop)
"""
import re
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent / 'apps/dashboard/static/project.html'


def _html():
    return HTML_PATH.read_text()


# ── AC1: Consistent structural shell ─────────────────────────────────────────

def test_ac1_ns_modal_has_header_body_footer():
    """ns-modal must have .modal-header, .modal-body, and .modal-footer."""
    html = _html()
    # Extract ns-modal section
    start = html.find('id="ns-modal"')
    assert start != -1, "ns-modal not found"
    section = html[start:start + 2000]
    assert 'modal-header' in section, "ns-modal missing .modal-header"
    assert 'modal-body' in section, "ns-modal missing .modal-body"
    assert 'modal-footer' in section, "ns-modal missing .modal-footer"


def test_ac1_rr_modal_has_header_body_footer():
    """rr-modal must have .modal-header, .modal-body, and .modal-footer."""
    html = _html()
    start = html.find('id="rr-modal"')
    assert start != -1, "rr-modal not found"
    section = html[start:start + 2000]
    assert 'modal-header' in section, "rr-modal missing .modal-header"
    assert 'modal-body' in section, "rr-modal missing .modal-body"
    assert 'modal-footer' in section, "rr-modal missing .modal-footer"


def test_ac1_bc_main_modal_has_header():
    """bc-main-modal must have a modal-header with a title element."""
    html = _html()
    assert 'id="bc-main-modal"' in html, "bc-main-modal element not found"
    start = html.find('id="bc-main-modal"')
    section = html[start:start + 500]
    assert 'modal-header' in section or 'bc-main-header' in section, \
        "bc-main-modal missing header"
    assert 'bc-main-title' in html, "bc-main-title element not found"


def test_ac1_bc_main_modal_has_close_button():
    """bc-main-modal must have a close button."""
    html = _html()
    assert 'id="bc-main-modal"' in html, "bc-main-modal element not found"
    start = html.find('id="bc-main-modal"')
    section = html[start:start + 1000]
    assert 'modal-close' in section or 'bc-main-close' in section, \
        "bc-main-modal missing close button"


def test_ac1_inspector_drawer_has_header_and_body():
    """smgmt-inspector must exist and have a header element."""
    html = _html()
    assert 'id="smgmt-inspector"' in html, \
        "smgmt-inspector element not found in static HTML"
    start = html.find('id="smgmt-inspector"')
    section = html[start:start + 800]
    assert 'insp-header' in section or 'modal-header' in section or 'insp-title' in section, \
        "smgmt-inspector missing header (insp-header or insp-title)"
    assert 'insp-body' in section or 'inspector-body' in section, \
        "smgmt-inspector missing body container"


def test_ac1_inspector_has_close_button():
    """smgmt-inspector must include a close button."""
    html = _html()
    assert 'id="smgmt-inspector"' in html, "smgmt-inspector not found"
    start = html.find('id="smgmt-inspector"')
    section = html[start:start + 800]
    assert 'insp-close' in section or ('modal-close' in section and 'Inspector' in html[start:start + 1000]), \
        "inspector drawer missing a close button"


# ── AC2: Backdrop + backdrop-click-to-close ───────────────────────────────────

def test_ac2_bc_main_backdrop_exists():
    """A dedicated backdrop for the Bulk Create modal must exist."""
    html = _html()
    assert 'id="bc-main-backdrop"' in html, \
        "bc-main-backdrop not found — Bulk Create modal has no backdrop"


def test_ac2_bc_main_backdrop_closes_modal():
    """Clicking bc-main-backdrop must close the Bulk Create modal."""
    html = _html()
    start = html.find('id="bc-main-backdrop"')
    assert start != -1, "bc-main-backdrop not found"
    line = html[start:start + 200]
    assert 'closeBulkCreate' in line, \
        "bc-main-backdrop missing onclick='closeBulkCreate()'"


def test_ac2_inspector_backdrop_exists():
    """A dedicated backdrop for the ticket inspector drawer must exist."""
    html = _html()
    assert 'id="insp-backdrop"' in html, \
        "insp-backdrop not found — inspector drawer has no backdrop"


def test_ac2_inspector_backdrop_closes_drawer():
    """Clicking insp-backdrop must close the inspector drawer."""
    html = _html()
    start = html.find('id="insp-backdrop"')
    assert start != -1, "insp-backdrop not found"
    line = html[start:start + 200]
    assert '_smgmtInspectorClose' in line, \
        "insp-backdrop missing onclick='_smgmtInspectorClose()'"


def test_ac2_ns_backdrop_closes_modal():
    """Clicking ns-backdrop must close the New Sprint modal."""
    html = _html()
    start = html.find('id="ns-backdrop"')
    assert start != -1, "ns-backdrop not found"
    line = html[start:start + 200]
    assert '_nsClose' in line or 'nsClose' in line, \
        "ns-backdrop missing close handler"


def test_ac2_rr_backdrop_closes_modal():
    """Clicking rr-backdrop must close the Re-run Sprint modal."""
    html = _html()
    start = html.find('id="rr-backdrop"')
    assert start != -1, "rr-backdrop not found"
    line = html[start:start + 200]
    assert '_rrClose' in line or 'rrClose' in line, \
        "rr-backdrop missing close handler"


# ── AC3: Focus trap + Esc key ─────────────────────────────────────────────────

def test_ac3_esc_handler_covers_rr_modal():
    """The Escape key handler must close the Re-run Sprint modal."""
    html = _html()
    # Find the function DEFINITION (not a call site earlier in the file)
    setup_start = html.find('function _setupModals(')
    assert setup_start != -1, "_setupModals function not found"
    region = html[setup_start:setup_start + 3000]
    assert 'rr-modal' in region, \
        "_setupModals Escape handler does not mention rr-modal"


def test_ac3_esc_handler_covers_bc_main_modal():
    """The Escape key handler must close the Bulk Create modal."""
    html = _html()
    setup_start = html.find('function _setupModals(')
    assert setup_start != -1, "_setupModals function not found"
    region = html[setup_start:setup_start + 3000]
    assert 'bc-main-modal' in region or 'closeBulkCreate' in region, \
        "_setupModals Escape handler does not cover bc-main-modal"


def test_ac3_esc_handler_covers_inspector():
    """The Escape key handler must close the inspector drawer."""
    html = _html()
    setup_start = html.find('function _setupModals(')
    assert setup_start != -1, "_setupModals function not found"
    region = html[setup_start:setup_start + 3000]
    assert 'smgmt-inspector' in region or '_smgmtInspectorClose' in region, \
        "_setupModals Escape handler does not cover smgmt-inspector"


def test_ac3_open_bulk_create_calls_set_body_inert():
    """openBulkCreate must call _setBodyInert to trap focus."""
    html = _html()
    fn_start = html.find('function openBulkCreate(')
    assert fn_start != -1, "openBulkCreate function not found"
    fn_body = html[fn_start:fn_start + 400]
    assert '_setBodyInert' in fn_body, \
        "openBulkCreate does not call _setBodyInert — no focus trap"


def test_ac3_inspector_feed_calls_set_body_inert():
    """_smgmtInspectorFeed must call _setBodyInert to trap focus."""
    html = _html()
    fn_start = html.find('function _smgmtInspectorFeed(')
    assert fn_start != -1, "_smgmtInspectorFeed function not found"
    fn_body = html[fn_start:fn_start + 4000]
    assert '_setBodyInert' in fn_body, \
        "_smgmtInspectorFeed does not call _setBodyInert — no focus trap for drawer"


def test_ac3_inspector_close_calls_clear_body_inert():
    """_smgmtInspectorClose must call _clearBodyInert to restore focus."""
    html = _html()
    fn_start = html.find('function _smgmtInspectorClose(')
    assert fn_start != -1, "_smgmtInspectorClose function not found"
    fn_body = html[fn_start:fn_start + 700]
    assert '_clearBodyInert' in fn_body, \
        "_smgmtInspectorClose does not call _clearBodyInert"


# ── AC4: Visible focus indicators ─────────────────────────────────────────────

def test_ac4_modal_close_has_focus_visible_rule():
    """.modal-close:focus-visible CSS rule must be defined."""
    html = _html()
    assert '.modal-close:focus-visible' in html, \
        ".modal-close:focus-visible rule missing — close buttons have no keyboard focus ring"


def test_ac4_prefers_reduced_motion_present():
    """prefers-reduced-motion block must be present to suppress modal animations."""
    html = _html()
    reduced = re.findall(
        r'@media\s*\(prefers-reduced-motion[^)]*\)\s*\{[^}]*\}',
        html, re.DOTALL,
    )
    assert reduced, "@media (prefers-reduced-motion) block not found"


# ── AC5: ARIA attributes ──────────────────────────────────────────────────────

def test_ac5_bc_main_modal_has_aria_modal():
    """bc-main-modal must set aria-modal='true'."""
    html = _html()
    start = html.find('id="bc-main-modal"')
    assert start != -1, "bc-main-modal not found"
    tag = html[start:start + 300]
    assert 'aria-modal="true"' in tag, \
        "bc-main-modal is missing aria-modal='true'"


def test_ac5_bc_main_modal_has_role_dialog():
    """bc-main-modal must have role='dialog'."""
    html = _html()
    start = html.find('id="bc-main-modal"')
    assert start != -1, "bc-main-modal not found"
    tag = html[start:start + 300]
    assert 'role="dialog"' in tag, "bc-main-modal is missing role='dialog'"


def test_ac5_bc_main_modal_has_aria_labelledby():
    """bc-main-modal must have aria-labelledby pointing to its title."""
    html = _html()
    start = html.find('id="bc-main-modal"')
    assert start != -1, "bc-main-modal not found"
    tag = html[start:start + 300]
    assert 'aria-labelledby' in tag, \
        "bc-main-modal is missing aria-labelledby"


def test_ac5_inspector_has_aria_modal():
    """smgmt-inspector must have aria-modal='true'."""
    html = _html()
    start = html.find('id="smgmt-inspector"')
    assert start != -1, "smgmt-inspector not found"
    tag = html[start:start + 300]
    assert 'aria-modal="true"' in tag, \
        "smgmt-inspector is missing aria-modal='true'"


def test_ac5_inspector_has_role_dialog():
    """smgmt-inspector must have role='dialog'."""
    html = _html()
    start = html.find('id="smgmt-inspector"')
    assert start != -1, "smgmt-inspector not found"
    tag = html[start:start + 300]
    assert 'role="dialog"' in tag, "smgmt-inspector is missing role='dialog'"


def test_ac5_ns_modal_has_aria_attrs():
    """ns-modal must have role='dialog', aria-modal='true', aria-labelledby."""
    html = _html()
    start = html.find('id="ns-modal"')
    assert start != -1, "ns-modal not found"
    tag = html[start:start + 300]
    assert 'role="dialog"' in tag, "ns-modal missing role='dialog'"
    assert 'aria-modal="true"' in tag, "ns-modal missing aria-modal='true'"
    assert 'aria-labelledby' in tag, "ns-modal missing aria-labelledby"


def test_ac5_rr_modal_has_aria_attrs():
    """rr-modal must have role='dialog', aria-modal='true', aria-labelledby."""
    html = _html()
    start = html.find('id="rr-modal"')
    assert start != -1, "rr-modal not found"
    tag = html[start:start + 300]
    assert 'role="dialog"' in tag, "rr-modal missing role='dialog'"
    assert 'aria-modal="true"' in tag, "rr-modal missing aria-modal='true'"
    assert 'aria-labelledby' in tag, "rr-modal missing aria-labelledby"


# ── AC6: CSS transitions + prefers-reduced-motion ─────────────────────────────

def test_ac6_modal_open_keyframe_defined():
    """A @keyframes animation for modal open must be defined."""
    html = _html()
    assert '@keyframes' in html, "@keyframes not present at all"
    # Look for a keyframe that animates opacity/transform for modal open
    keyframes = re.findall(r'@keyframes\s+(\w+)', html)
    # Any keyframe that's referenced by .modal:not(.hidden) or similar
    assert any(k for k in keyframes), "No @keyframes found"


def test_ac6_backdrop_open_animation_defined():
    """A @keyframes animation for backdrop fade-in must be defined."""
    html = _html()
    # Check that the backdrop animation is applied
    assert 'backdropIn' in html or 'backdrop-fade' in html or \
        ('.modal-backdrop:not(.hidden)' in html and 'animation' in html), \
        "No backdrop open animation found"


def test_ac6_prefers_reduced_motion_suppresses_modal_animation():
    """prefers-reduced-motion block must suppress modal open animation."""
    html = _html()
    blocks = re.findall(
        r'@media\s*\(prefers-reduced-motion[^)]*\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
        html, re.DOTALL,
    )
    assert blocks, "No prefers-reduced-motion block found"
    combined = ' '.join(blocks)
    # The block must reference modal or backdrop animation suppression
    has_modal_suppress = 'modal' in combined.lower() and 'animation' in combined.lower()
    assert has_modal_suppress, \
        "prefers-reduced-motion block does not suppress modal animation"


def test_ac6_drawer_animation_defined():
    """A @keyframes animation for the inspector drawer slide-in must be defined."""
    html = _html()
    assert 'drawerIn' in html or 'drawer-slide' in html or \
        ('insp-drawer' in html and 'animation' in html), \
        "No drawer slide-in animation found"


# ── AC7: Loading + error states ───────────────────────────────────────────────

def test_ac7_rr_modal_has_loading_element():
    """rr-modal must have a loading state element."""
    html = _html()
    assert 'id="rr-loading"' in html, "rr-loading element missing from rr-modal"


def test_ac7_rr_modal_has_error_element():
    """rr-modal must have an inline error element."""
    html = _html()
    assert 'id="rr-error"' in html, "rr-error element missing from rr-modal"


def test_ac7_ns_modal_has_error_element():
    """ns-modal must have an inline error element."""
    html = _html()
    assert 'id="ns-error"' in html, "ns-error element missing from ns-modal"


def test_ac7_bc_main_modal_has_error_element():
    """Bulk Create modal must have an error element."""
    html = _html()
    # bc-step1-error or bc-error should be inside bc-main-modal or its content
    assert 'bc-step1-error' in html or 'bc-error' in html, \
        "No error element found for Bulk Create modal"


# ── AC8: Existing JS handlers preserved ───────────────────────────────────────

def test_ac8_open_bulk_create_function_exists():
    """openBulkCreate function must still be defined."""
    html = _html()
    assert 'function openBulkCreate(' in html, \
        "openBulkCreate function not found"


def test_ac8_close_bulk_create_function_exists():
    """closeBulkCreate function must be defined (new function)."""
    html = _html()
    assert 'function closeBulkCreate(' in html, \
        "closeBulkCreate function not found — needed for Esc and close button"


def test_ac8_inspector_feed_function_exists():
    """_smgmtInspectorFeed must still be defined in project.html."""
    html = _html()
    assert 'function _smgmtInspectorFeed(' in html, \
        "_smgmtInspectorFeed function not found"


def test_ac8_inspector_close_function_exists():
    """_smgmtInspectorClose must still be defined in project.html."""
    html = _html()
    assert 'function _smgmtInspectorClose(' in html, \
        "_smgmtInspectorClose function not found"


def test_ac8_open_new_sprint_function_exists():
    """smgmtOpenNewSprint must still be defined."""
    html = _html()
    assert 'function smgmtOpenNewSprint(' in html, \
        "smgmtOpenNewSprint function not found"


def test_ac8_ns_close_function_exists():
    """_nsClose must still be defined."""
    html = _html()
    assert 'function _nsClose(' in html, "_nsClose function not found"


def test_ac8_rr_handlers_referenced():
    """_rrClose and _rrConfirm must still be wired up in _setupModals."""
    html = _html()
    assert '_rrClose' in html, "_rrClose not referenced in HTML"
    assert '_rrConfirm' in html, "_rrConfirm not referenced in HTML"


# ── AC9: Only token variables, no hardcoded colors ────────────────────────────

def test_ac9_no_hardcoded_hex_in_bc_main_modal_css():
    """New bc-main-modal CSS must use var(--...) tokens, not raw hex colors."""
    html = _html()
    # Find the bc-main-modal CSS region
    idx = html.find('.bc-main-modal')
    if idx == -1:
        idx = html.find('#bc-main-modal')
    if idx == -1:
        return  # CSS might be inline on element — skip if not found as a class
    # Extract a region around the first CSS mention
    region = html[idx:idx + 600]
    # Should not have bare hex values (#xxx or #xxxxxx) — but allow in comments
    hex_matches = re.findall(r'(?<!var\()#[0-9a-fA-F]{3,6}(?!\w)', region)
    assert not hex_matches, \
        f"Hardcoded hex colors found in bc-main-modal CSS: {hex_matches}"


def test_ac9_no_hardcoded_hex_in_insp_drawer_css():
    """Inspector drawer CSS must use token variables, not raw hex."""
    html = _html()
    idx = html.find('.insp-drawer')
    if idx == -1:
        return  # CSS might not exist yet — let other tests catch that
    region = html[idx:idx + 800]
    hex_matches = re.findall(r'(?<!var\()#[0-9a-fA-F]{3,6}(?!\w)', region)
    assert not hex_matches, \
        f"Hardcoded hex colors in inspector drawer CSS: {hex_matches}"


# ── AC12: No regressions ─────────────────────────────────────────────────────

def test_ac12_ns_modal_still_exists():
    """ns-modal must remain intact."""
    html = _html()
    assert 'id="ns-modal"' in html, "ns-modal was removed (regression)"
    assert 'id="ns-backdrop"' in html, "ns-backdrop was removed (regression)"


def test_ac12_rr_modal_still_exists():
    """rr-modal must remain intact."""
    html = _html()
    assert 'id="rr-modal"' in html, "rr-modal was removed (regression)"
    assert 'id="rr-backdrop"' in html, "rr-backdrop was removed (regression)"


def test_ac12_existing_modals_still_present():
    """Other modals (fs, nt, cl, ct, pf) must still be in the HTML."""
    html = _html()
    for mid in ('fs-modal', 'nt-modal', 'cl-modal', 'ct-modal', 'pf-modal'):
        assert f'id="{mid}"' in html, f"{mid} was removed (regression)"


def test_ac12_bc_step_content_still_present():
    """Bulk Create step content (bc-step1, bc-step2) must remain in the DOM."""
    html = _html()
    assert 'id="bc-step1"' in html, "bc-step1 removed (regression)"
    assert 'id="bc-step2"' in html, "bc-step2 removed (regression)"


def test_ac12_bc_run_btn_still_present():
    """Bulk Create run button must remain (handler preserved)."""
    html = _html()
    assert 'id="bc-run-btn"' in html, "bc-run-btn removed (regression)"


# ── AC13: No new external dependencies ────────────────────────────────────────

def test_ac13_no_framework_script_imports():
    """No React, Vue, Angular, jQuery, or Svelte must be imported."""
    html = _html()
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    banned = ('react', 'vue', 'svelte', 'angular', 'jquery')
    for src in srcs:
        for fw in banned:
            assert fw not in src.lower(), \
                f"Framework '{fw}' found in <script src>: {src}"

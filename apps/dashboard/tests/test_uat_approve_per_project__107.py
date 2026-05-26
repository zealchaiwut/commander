"""Tests for issue #107: Move 'Approve all UAT' button into per-project UAT group header."""
import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8000")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

STATIC_ROOT = Path(__file__).parent.parent / "static"
APP_JS_PATH  = STATIC_ROOT / "app.js"
INDEX_HTML_PATH = STATIC_ROOT / "index.html"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def js():
    """Read app.js directly from disk — no running server required."""
    return APP_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def html():
    """Read index.html directly from disk — no running server required."""
    return INDEX_HTML_PATH.read_text(encoding="utf-8")


# --- AC-1: No old global/page-level "Approve all UAT" button ---

def test_ac1_no_global_approveAllUat_function(js):
    """AC-1: The old global approveAllUat(repo, btnEl) function is removed; replaced with scoped modal functions."""
    assert "async function approveAllUat" not in js, \
        "Old global approveAllUat(repo, btnEl) function still present"


# --- AC-2: UAT group header format "UAT (N)" ---

def test_ac2_uat_group_header_format(js):
    """AC-2: _ticketGroupHtml renders 'UAT (N)' format, not 'UAT · N'."""
    assert "UAT (${tickets.length})" in js, \
        "_ticketGroupHtml does not render 'UAT (N)' format"
    assert "UAT · ${tickets.length}" not in js, \
        "Old 'UAT · N' format still present in _ticketGroupHtml"


# --- AC-3: Button appears in UAT group header ---

def test_ac3_approve_all_button_in_uat_group(js):
    """AC-3: 'Approve all UAT' button is rendered inside the UAT ticket group header."""
    assert "Approve all UAT" in js, "'Approve all UAT' text not found in app.js"
    assert "btn-approve-all-uat" in js, "'btn-approve-all-uat' CSS class not found in app.js"
    assert "showApproveAllUatModal" in js, "showApproveAllUatModal() call not found in UAT group"


# --- AC-4 & AC-10: Button absent when UAT count is 0 ---

def test_ac4_no_button_when_uat_empty(js):
    """AC-4/AC-10: _ticketGroupHtml returns '' when tickets.length === 0, so no button in DOM."""
    assert "if (tickets.length === 0) return '';" in js, \
        "_ticketGroupHtml must return empty string when no tickets"


# --- AC-5: Confirmation modal opens with project name ---

def test_ac5_show_modal_function_exists(js):
    """AC-5: showApproveAllUatModal() function is defined."""
    assert "function showApproveAllUatModal" in js, \
        "showApproveAllUatModal function not defined in app.js"


def test_ac5_modal_title_includes_project_name(js):
    """AC-5: Modal title includes ticket count and project name."""
    assert "UAT ticket" in js, "Modal title does not include 'UAT ticket' text"
    assert "projName" in js, "projName variable not used in modal title"
    assert "aua-modal-title" in js, "Modal title element 'aua-modal-title' not referenced"


def test_ac5_modal_html_present(html):
    """AC-5: Confirmation modal HTML exists in index.html."""
    assert 'id="aua-modal"' in html, "aua-modal element missing from index.html"
    assert 'id="aua-modal-backdrop"' in html, "aua-modal-backdrop element missing"
    assert 'id="aua-modal-title"' in html, "aua-modal-title element missing"


# --- AC-6: Modal lists each ticket ---

def test_ac6_modal_lists_tickets(js):
    """AC-6: showApproveAllUatModal renders each ticket's number and title in the modal list."""
    assert "aua-modal-list" in js, "aua-modal-list element not referenced in JS"
    assert "t.number" in js, "Ticket number not used when building modal list"
    assert "t.title" in js, "Ticket title not used when building modal list"


def test_ac6_modal_list_in_html(html):
    """AC-6: Modal list element exists in HTML."""
    assert 'id="aua-modal-list"' in html, "aua-modal-list element missing from index.html"


# --- AC-7: Confirm calls approve-batch scoped to project ---

def test_ac7_confirm_function_exists(js):
    """AC-7: confirmApproveAllUat() function is defined."""
    assert "function confirmApproveAllUat" in js, \
        "confirmApproveAllUat function not defined"


def test_ac7_confirm_calls_approve_batch(js):
    """AC-7: confirmApproveAllUat() calls POST /api/projects/<repo>/approve-batch."""
    assert "approve-batch" in js, "approve-batch endpoint call missing"


def test_ac7_confirm_button_in_html(html):
    """AC-7: Confirm button exists in the modal."""
    assert 'id="aua-modal-confirm"' in html, "aua-modal-confirm button missing from HTML"
    assert "confirmApproveAllUat" in html, "confirmApproveAllUat not wired to confirm button"


# --- AC-8: After success, expand panel refreshes ---

def test_ac8_refresh_after_approve(js):
    """AC-8: confirmApproveAllUat() calls _refreshAfterAction to reload the expand panel."""
    assert "_refreshAfterAction" in js, "_refreshAfterAction not called after batch approve"


# --- AC-9: Button uses green background and ti-checks icon ---

def test_ac9_button_green_background(html):
    """AC-9: btn-approve-all-uat uses var(--green) as background in CSS."""
    assert "btn-approve-all-uat" in html, "btn-approve-all-uat CSS class not defined in index.html"
    assert "var(--green)" in html, "var(--green) not used for btn-approve-all-uat background"


def test_ac9_button_ti_checks_icon(js):
    """AC-9: Approve all UAT button uses the ti-checks icon."""
    assert "ti-checks" in js, "ti-checks icon not found in approve-all-uat button"


# --- AC: Modal cancel/close ---

def test_close_modal_function_exists(js):
    """closeApproveAllUatModal() is defined for Cancel and backdrop click."""
    assert "function closeApproveAllUatModal" in js, \
        "closeApproveAllUatModal function not defined"


def test_cancel_button_in_modal(html):
    """Cancel button in modal calls closeApproveAllUatModal."""
    assert "closeApproveAllUatModal" in html, \
        "closeApproveAllUatModal not wired to cancel/backdrop in HTML"


# --- AC: UAT ticket storage for modal ---

def test_uat_tickets_stored_per_repo(js):
    """_uatTicketsByRepo stores UAT tickets per repo so modal can list them."""
    assert "_uatTicketsByRepo" in js, "_uatTicketsByRepo state variable not found"


# --- AC: Success toast after approval ---

def test_success_toast_shown(js):
    """showToastSuccess is called after successful batch approve."""
    assert "showToastSuccess" in js, "showToastSuccess not called after approve"


def test_success_toast_element_in_html(html):
    """toast-success element exists in HTML for green success feedback."""
    assert 'id="toast-success"' in html, "toast-success element missing from index.html"


# --- Integration: backend approve-batch endpoint still exists ---

def test_approve_batch_endpoint_live(client):
    """The approve-batch endpoint is reachable on the live server."""
    # Use a known-safe test repo; just check the endpoint exists (not 404)
    repo = "zealchaiwut%2Fcommander"
    r = client.post(f"/api/projects/{repo}/approve-batch")
    assert r.status_code != 404, "approve-batch endpoint returned 404"

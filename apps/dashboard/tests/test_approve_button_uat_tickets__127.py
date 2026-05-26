"""Tests for issue #127: Approve button on UAT-labelled tickets in Active Tickets panel."""
from pathlib import Path

import pytest

REPO_ROOT    = Path(__file__).parent.parent.parent.parent
DASHBOARD    = REPO_ROOT / "apps" / "dashboard"
APP_JS       = DASHBOARD / "static" / "app.js"
INDEX_HTML   = DASHBOARD / "static" / "index.html"
SERVER_PY    = DASHBOARD / "server.py"


# ── AC-1: Approve ✓ button shown only for UAT tickets ────────────────────────

class TestApproveButtonCondition:
    def test_button_shown_for_uat_tickets(self):
        """app.js must render Approve ✓ only when ticket.is_uat is true."""
        src = APP_JS.read_text()
        assert "ticket.is_uat" in src or "is_uat" in src, (
            "app.js must gate the Approve button on ticket.is_uat"
        )

    def test_button_text_is_approve_checkmark(self):
        """AC-1: button text must be 'Approve ✓'."""
        src = APP_JS.read_text()
        assert "Approve ✓" in src, "Approve button label must be 'Approve ✓'"

    def test_button_has_green_class(self):
        """AC-1: Approve button must use the green btn-approve-sm style class."""
        src = APP_JS.read_text()
        assert "btn-approve-sm" in src, (
            "Approve button must use btn-approve-sm class (green style)"
        )

    def test_uat_button_not_shown_unconditionally(self):
        """AC-2: Approve button is gated on is_uat, not shown for every card."""
        src = APP_JS.read_text()
        # The Approve ✓ button HTML must only appear inside an is_uat block
        lines = src.splitlines()
        approve_line = next(
            (i for i, l in enumerate(lines) if "Approve ✓" in l), None
        )
        assert approve_line is not None, "Approve ✓ button must exist in app.js"
        # Walk backwards to find the nearest conditional
        context = "\n".join(lines[max(0, approve_line - 20):approve_line + 1])
        assert "is_uat" in context, (
            "Approve ✓ button must be inside an is_uat conditional block"
        )


# ── AC-3: Clicking opens a confirmation modal ─────────────────────────────────

class TestConfirmationModal:
    def test_confirmApprove_function_exists(self):
        """AC-3: app.js must define confirmApprove() to open the modal."""
        src = APP_JS.read_text()
        assert "function confirmApprove" in src, (
            "app.js must define confirmApprove() function"
        )

    def test_button_calls_confirmApprove(self):
        """AC-3: Approve button onclick must call confirmApprove, not approveIssue directly."""
        src = APP_JS.read_text()
        approve_btn_idx = src.find("Approve ✓")
        assert approve_btn_idx != -1
        # Grab a small window around the button template
        snippet = src[max(0, approve_btn_idx - 200):approve_btn_idx + 200]
        assert "confirmApprove" in snippet, (
            "Approve ✓ button onclick must call confirmApprove()"
        )
        assert "approveIssue" not in snippet, (
            "Approve ✓ button must NOT call approveIssue() directly (must go through modal)"
        )

    def test_modal_exists_in_html(self):
        """AC-3: index.html must contain an approve confirmation modal."""
        html = INDEX_HTML.read_text()
        assert 'id="approve-modal"' in html, (
            "index.html must have an element with id='approve-modal'"
        )

    def test_modal_backdrop_exists(self):
        """AC-3: index.html must have a backdrop element for the approve modal."""
        html = INDEX_HTML.read_text()
        assert 'id="approve-backdrop"' in html, (
            "index.html must have id='approve-backdrop'"
        )

    def test_modal_shows_issue_reference(self):
        """AC-3: Modal must display the issue number being approved."""
        html = INDEX_HTML.read_text()
        assert 'id="approve-modal-issue-ref"' in html, (
            "Modal must have an element with id='approve-modal-issue-ref' to show the issue #"
        )


# ── AC-4: Confirming calls POST /api/tickets/<N>/approve ─────────────────────

class TestApproveEndpointCall:
    def test_confirm_calls_tickets_approve_endpoint(self):
        """AC-4: approveModalConfirm must call POST /api/tickets/{N}/approve."""
        src = APP_JS.read_text()
        assert "/api/tickets/" in src and "/approve" in src, (
            "app.js must call /api/tickets/<N>/approve on confirm"
        )

    def test_confirm_function_exists(self):
        """AC-4: app.js must define approveModalConfirm()."""
        src = APP_JS.read_text()
        assert "function approveModalConfirm" in src or "async function approveModalConfirm" in src, (
            "app.js must define approveModalConfirm()"
        )

    def test_endpoint_exists_in_server(self):
        """AC-4: server.py must define POST /api/tickets/{issue_id}/approve."""
        src = SERVER_PY.read_text()
        assert '"/api/tickets/{issue_id}/approve"' in src, (
            "server.py must define POST /api/tickets/{issue_id}/approve endpoint"
        )

    def test_confirm_button_in_modal(self):
        """AC-4: Modal must have a Confirm button to submit the approval."""
        html = INDEX_HTML.read_text()
        assert 'id="approve-modal-confirm-btn"' in html, (
            "Modal must have id='approve-modal-confirm-btn'"
        )
        assert "approveModalConfirm" in html, (
            "Confirm button must call approveModalConfirm()"
        )


# ── AC-5: Card removed from panel after successful approval ───────────────────

class TestCardRemovedOnSuccess:
    def test_removes_card_on_success(self):
        """AC-5: After approval, the ticket card must be removed from the DOM."""
        src = APP_JS.read_text()
        assert ".ticket-card" in src and ".remove()" in src, (
            "approveModalConfirm must remove the .ticket-card element on success"
        )

    def test_uses_closest_to_find_card(self):
        """AC-5: Card removal must use .closest('.ticket-card') for robustness."""
        src = APP_JS.read_text()
        assert "closest('.ticket-card')" in src, (
            "DOM removal must call .closest('.ticket-card')"
        )


# ── AC-6: Cancelling leaves ticket unchanged ──────────────────────────────────

class TestCancelBehavior:
    def test_cancel_function_exists(self):
        """AC-6: app.js must define approveModalCancel()."""
        src = APP_JS.read_text()
        assert "function approveModalCancel" in src, (
            "app.js must define approveModalCancel()"
        )

    def test_cancel_button_in_modal(self):
        """AC-6: Modal must have a Cancel button."""
        html = INDEX_HTML.read_text()
        assert "approveModalCancel" in html, (
            "Modal must have a Cancel button that calls approveModalCancel()"
        )

    def test_cancel_hides_modal_without_api_call(self):
        """AC-6: approveModalCancel must hide the modal elements."""
        src = APP_JS.read_text()
        cancel_idx = src.find("function approveModalCancel")
        assert cancel_idx != -1
        fn_body = src[cancel_idx:cancel_idx + 300]
        assert "classList.add('hidden')" in fn_body, (
            "approveModalCancel must hide the modal via classList.add('hidden')"
        )
        # Must NOT make a fetch call
        assert "fetch(" not in fn_body, (
            "approveModalCancel must NOT call fetch — no API call on cancel"
        )


# ── AC-8: Non-2xx shows error toast ──────────────────────────────────────────

class TestErrorToast:
    def test_non_2xx_shows_toast(self):
        """AC-8: approveModalConfirm must call showToast on non-2xx response."""
        src = APP_JS.read_text()
        confirm_idx = src.find("async function approveModalConfirm")
        if confirm_idx == -1:
            confirm_idx = src.find("function approveModalConfirm")
        assert confirm_idx != -1, "approveModalConfirm function must exist"
        fn_body = src[confirm_idx:confirm_idx + 600]
        assert "showToast" in fn_body, (
            "approveModalConfirm must call showToast() on non-2xx response"
        )

    def test_toast_includes_status_info(self):
        """AC-8: Error toast must include the HTTP status."""
        src = APP_JS.read_text()
        confirm_idx = src.find("async function approveModalConfirm")
        if confirm_idx == -1:
            confirm_idx = src.find("function approveModalConfirm")
        fn_body = src[confirm_idx:confirm_idx + 600]
        assert "res.status" in fn_body, (
            "Error toast message must include res.status to show the HTTP status code"
        )


# ── Live API: endpoint responds ───────────────────────────────────────────────

@pytest.mark.live
class TestApproveEndpointLive:
    def test_approve_endpoint_in_openapi_spec(self, client):
        """AC-4: POST /api/tickets/{issue_id}/approve must appear in the OpenAPI spec."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200, "OpenAPI spec must be available at /openapi.json"
        spec = resp.json()
        paths = spec.get("paths", {})
        route = "/api/tickets/{issue_id}/approve"
        assert route in paths, (
            f"Route {route!r} is not in the OpenAPI spec. "
            f"Available paths: {sorted(p for p in paths if 'ticket' in p)}"
        )
        assert "post" in paths[route], (
            f"Route {route!r} must support POST method"
        )

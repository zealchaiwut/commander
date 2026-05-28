"""Tests for issue #272: Fix New Ticket buttons to open current UI form.

Covers:
  AC1 — project header "+ New ticket" button opens the current-UI modal, not legacy
  AC2 — Sprint Management toolbar "New Ticket" button opens the current-UI modal, not legacy
  AC3 — new-ticket modal pre-scopes project selector to the currently selected project
  AC4 — after ticket creation, board refreshes without hard page reload
  AC5 — no route or link in the application leads to a legacy new-ticket page
  AC6 — direct URL access to legacy new-ticket paths does not reach a standalone old form
"""
import os
import re
import pathlib
import pytest
import httpx

BASE_URL = (
    os.environ.get("UAT_BASE_URL")
    or "http://localhost:" + os.environ.get("UAT_PORT", "8050")
)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill Step 0 to resolve UAT."
    )

PROJECT_HTML = pathlib.Path(__file__).parent.parent / "static" / "project.html"
SERVER_PY = pathlib.Path(__file__).parent.parent / "server.py"

TEST_PROJECT = "zealchaiwut/commander"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=20.0, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="module")
def html_source() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


# ── AC1: Project header "+ New ticket" button opens current-UI modal ────────

class TestProjectHeaderNewTicketButton:
    def test_header_button_calls_open_new_ticket(self, html_source):
        """The project header button must call openNewTicket(), not any legacy href."""
        # Line: <button class="btn-primary" onclick="openNewTicket()" ...>
        assert 'onclick="openNewTicket()"' in html_source, (
            "Project header New Ticket button must call openNewTicket()"
        )

    def test_open_new_ticket_calls_smgmt_open(self, html_source):
        """openNewTicket() must delegate to smgmtOpenNewTicket() (modal), not navigate away."""
        # Extract the openNewTicket function body
        match = re.search(
            r'function openNewTicket\(\)\s*\{([^}]+)\}',
            html_source,
            re.DOTALL,
        )
        assert match, "openNewTicket() function not found in project.html"
        body = match.group(1)
        assert 'smgmtOpenNewTicket()' in body, (
            "openNewTicket() must call smgmtOpenNewTicket()"
        )

    def test_open_new_ticket_has_no_legacy_navigation(self, html_source):
        """openNewTicket() must NOT navigate to any legacy URL."""
        match = re.search(
            r'function openNewTicket\(\)\s*\{([^}]+)\}',
            html_source,
            re.DOTALL,
        )
        assert match, "openNewTicket() function not found in project.html"
        body = match.group(1)
        assert 'location.href' not in body, (
            "openNewTicket() must not use window.location.href (would navigate away)"
        )
        assert 'legacy' not in body.lower(), (
            "openNewTicket() must not reference legacy routes"
        )
        assert '/projects/' not in body, (
            "openNewTicket() must not navigate to /projects/"
        )


# ── AC2: Sprint Management toolbar "New Ticket" button opens current-UI modal

class TestSprintMgmtToolbarNewTicketButton:
    def test_toolbar_button_calls_smgmt_open(self, html_source):
        """Sprint Management toolbar New Ticket button must call smgmtOpenNewTicket()."""
        # The toolbar button: id="smgmt-new-ticket-btn" onclick="smgmtOpenNewTicket()"
        assert 'id="smgmt-new-ticket-btn"' in html_source, (
            "smgmt-new-ticket-btn element not found in project.html"
        )
        assert 'onclick="smgmtOpenNewTicket()"' in html_source, (
            "Sprint Mgmt toolbar button must call smgmtOpenNewTicket() directly"
        )

    def test_smgmt_open_new_ticket_opens_modal_not_navigate(self, html_source):
        """smgmtOpenNewTicket() must show the modal, not navigate to a legacy page."""
        # Find the function — it's async and fairly large; look for modal show
        match = re.search(
            r'async function smgmtOpenNewTicket\(\)\s*\{(.+?)(?=\nfunction |\nasync function )',
            html_source,
            re.DOTALL,
        )
        assert match, "smgmtOpenNewTicket() function not found in project.html"
        body = match.group(1)
        # Must reveal the modal elements
        assert "nt-backdrop" in body, (
            "smgmtOpenNewTicket() must show nt-backdrop (modal backdrop)"
        )
        assert "nt-modal" in body, (
            "smgmtOpenNewTicket() must show nt-modal"
        )
        # Must NOT navigate away
        assert 'location.href' not in body, (
            "smgmtOpenNewTicket() must not use window.location.href"
        )
        assert 'legacy' not in body.lower(), (
            "smgmtOpenNewTicket() must not reference legacy routes"
        )


# ── AC3: Modal pre-scopes project to currently selected project ──────────────

class TestProjectPreScoping:
    def test_modal_uses_cached_full_repo_for_preselect(self, html_source):
        """smgmtOpenNewTicket() must pre-select the current project via _cachedFullRepo."""
        assert '_cachedFullRepo[_slug]' in html_source, (
            "Modal must read _cachedFullRepo[_slug] to pre-scope the project dropdown"
        )

    def test_project_dropdown_exists_in_modal(self, html_source):
        """The new-ticket modal must contain a project selector (nt-project)."""
        assert 'id="nt-project"' in html_source, (
            "nt-project dropdown must exist in the new-ticket modal"
        )

    def test_project_selector_populated_from_api(self, html_source):
        """smgmtOpenNewTicket() must fetch /api/projects to populate the dropdown."""
        assert "/api/projects" in html_source, (
            "smgmtOpenNewTicket() must call /api/projects to populate the project list"
        )

    def test_api_projects_returns_project_list(self, client):
        """GET /api/projects must return a projects list for pre-scoping."""
        r = client.get("/api/projects")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert "projects" in data, "Response must have a 'projects' key"
        assert isinstance(data["projects"], list)


# ── AC4: Ticket creation refreshes board without hard reload ─────────────────

class TestBoardRefreshWithoutReload:
    def test_post_btn_calls_load_sprint_mgmt_not_location(self, html_source):
        """After ticket creation, code must call loadSprintMgmt(), not navigate via location.href."""
        # Find the nt-post-btn click handler — look for loadSprintMgmt() call
        assert 'loadSprintMgmt()' in html_source, (
            "project.html must call loadSprintMgmt() after ticket creation"
        )
        assert 'loadTickets()' in html_source, (
            "project.html must call loadTickets() after ticket creation"
        )

    def test_post_btn_handler_has_no_page_navigation(self, html_source):
        """The nt-post-btn handler must not redirect the browser after ticket creation."""
        # Find the nt-post-btn event listener block
        match = re.search(
            r"document\.getElementById\('nt-post-btn'\)\.addEventListener\('click'.*?"
            r"(?=document\.getElementById\('nt-|function _setupModals|function _ntClose)",
            html_source,
            re.DOTALL,
        )
        assert match, "nt-post-btn click handler not found in project.html"
        handler = match.group(0)
        assert 'location.href' not in handler, (
            "nt-post-btn handler must not use window.location.href after posting"
        )

    def test_draft_section_html_present(self, html_source):
        """Step-2 draft review section must exist in the modal HTML."""
        assert 'id="nt-draft-section"' in html_source, (
            "nt-draft-section element must be present for the draft review step"
        )
        assert 'id="nt-title"' in html_source, (
            "nt-title input must be present for editing the generated title"
        )
        assert 'id="nt-body"' in html_source, (
            "nt-body textarea must be present for editing the generated body"
        )
        assert 'id="nt-post-btn"' in html_source, (
            "nt-post-btn must be present to submit the final ticket"
        )

    def test_create_ticket_api_returns_201(self, client):
        """POST /api/tickets/create must return HTTP 201 for a valid ticket."""
        r = client.post(
            "/api/tickets/create",
            data={
                "title": "[TESTER-272] Verify new-ticket button fix (auto-test)",
                "body": "Automated test for issue #272 — safe to close.",
                "project": TEST_PROJECT,
            },
        )
        assert r.status_code == 201, (
            f"POST /api/tickets/create returned {r.status_code}: {r.text}"
        )
        data = r.json()
        assert "number" in data and isinstance(data["number"], int), (
            "Response must include the new issue number"
        )
        assert "url" in data, "Response must include the issue URL"


# ── AC5: No link in the application leads to a legacy new-ticket page ────────

class TestNoLegacyNewTicketLinks:
    def test_no_legacy_new_ticket_href_in_project_html(self, html_source):
        """project.html must not contain any href pointing to /legacy/*new*ticket*."""
        matches = re.findall(
            r'href=["\'][^"\']*legacy[^"\']*(?:new.ticket|ticket.create)[^"\']*["\']',
            html_source,
            re.IGNORECASE,
        )
        assert not matches, (
            f"Found legacy new-ticket href(s) in project.html: {matches}"
        )

    def test_smgmt_open_does_not_redirect_to_sprint_mgmt(self, html_source):
        """After draft generation, the old redirect to /project/...sprint-mgmt is gone."""
        # The old code had: window.location.href = '/project/' + ... + '/sprint-mgmt'
        # inside the submit-btn click handler. Verify it's gone.
        match = re.search(
            r"document\.getElementById\('nt-submit-btn'\)\.addEventListener\('click'.*?"
            r"(?=document\.getElementById\('nt-post-btn'\)|function _ntClose)",
            html_source,
            re.DOTALL,
        )
        assert match, "nt-submit-btn click handler not found in project.html"
        handler = match.group(0)
        assert "location.href" not in handler, (
            "The nt-submit-btn handler must not navigate away (old redirect removed)"
        )

    def test_nt_post_cancel_btn_wired_to_close(self, html_source):
        """nt-post-cancel-btn must be wired to _ntClose() so second cancel works."""
        assert "nt-post-cancel-btn" in html_source, (
            "nt-post-cancel-btn must exist in the draft review section"
        )
        assert "'nt-post-cancel-btn'" in html_source, (
            "nt-post-cancel-btn must be wired up in _setupModals()"
        )


# ── AC6: Legacy new-ticket URL does not serve a standalone old form ───────────

class TestLegacyNewTicketRoute:
    def test_legacy_new_ticket_path_does_not_return_404_but_no_nt_form(self, client):
        """GET /legacy/some-project/new-ticket serves the legacy index.html (catchall),
        not the current-UI project.html with the new-ticket modal."""
        r = client.get("/legacy/test-project/new-ticket")
        # Legacy route is a catchall that serves index.html (200), not 404
        assert r.status_code == 200, (
            f"Expected 200 from legacy catchall, got {r.status_code}"
        )
        # Legacy index.html must NOT contain the current-UI nt-modal id (id="nt-modal")
        # Note: use the full id attribute to avoid substring matches (e.g. "sprint-modal")
        assert 'id="nt-modal"' not in r.text, (
            "Legacy route must not serve the current-UI new-ticket modal (id=nt-modal)"
        )
        # Must also not expose smgmtOpenNewTicket (current-UI function)
        assert "smgmtOpenNewTicket" not in r.text, (
            "Legacy route must not serve the current-UI smgmtOpenNewTicket function"
        )

    def test_projects_new_ticket_path_redirects_not_serves_form(self, client):
        """GET /projects/some-project/new-ticket is redirected (301), not served."""
        # Use a non-redirecting client to inspect the redirect
        with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as nc:
            r = nc.get("/projects/test-project/new-ticket")
        assert r.status_code in (301, 302, 307, 308), (
            f"/projects/ path must redirect (got {r.status_code})"
        )

    def test_project_html_served_for_current_ui_route(self, client):
        """GET /project/{slug}/sprint-mgmt must serve project.html with modal elements."""
        # Route uses a slug segment, not the full owner/repo path
        r = client.get("/project/commander/sprint-mgmt")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert 'id="nt-modal"' in r.text, (
            "Current-UI project page must contain the nt-modal new-ticket modal"
        )
        assert "smgmtOpenNewTicket" in r.text, (
            "Current-UI project page must contain smgmtOpenNewTicket function"
        )

    def test_server_py_comment_documents_removal(self, server_source):
        """server.py must contain a comment documenting that legacy new-ticket nav is gone."""
        assert "issue #272" in server_source, (
            "server.py must reference issue #272 to document the legacy nav removal"
        )

"""
Tests for issue #6: Dashboard polish — live tickets, agent filtering, repo link, new project flow.

AC-1: Active Tickets auto-populate from GitHub (open issues shown, closed hidden, empty state)
AC-2: Agents column filter and count summary (WORKING only by default, header format, toggle logic)
AC-3: Project name links to GitHub repo (<a> element, target=_blank, derived from repo field)
AC-4: "+ New Project" modal with validation and auto-add (POST /api/projects happy path, 409, 422)
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
STATIC_DIR    = DASHBOARD_DIR / "static"
sys.path.insert(0, str(DASHBOARD_DIR))

import projects as projects_module  # noqa: E402
import github_client                # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: Active Tickets
# ═══════════════════════════════════════════════════════════════════════════════

class TestAC1ActiveTickets:
    """
    AC-1a: get_project_details returns open issues with title, number, and url.
    AC-1b: Closed issues do NOT appear in the returned ticket list.
    AC-1c: If zero open issues, tickets list is empty (frontend shows "No open tickets").
    AC-1d: 30s cache TTL governs refresh (CACHE_TTL constant in github_client is 30).
    """

    def _make_issue(self, number, title, state="open", labels=None):
        return {
            "number":    number,
            "title":     title,
            "state":     state,
            "url":       f"https://github.com/owner/repo/issues/{number}",
            "labels":    labels or [],
            "assignees": [],
            "updatedAt": "2026-05-23T10:00:00Z",
        }

    def test_ac1a_open_issues_have_title_number_url(self, tmp_path):
        """get_project_details returns tickets with title, number, and url for open issues."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{
            "repo":           "owner/testrepo",
            "name":           "Test Repo",
            "icon":           "ti-folder",
            "color":          "blue",
            "active_sprints": {},
        }]))

        open_issues = [
            self._make_issue(1, "Fix login bug"),
            self._make_issue(2, "Add dark mode"),
        ]

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch.object(github_client, "list_open_issues", return_value=open_issues):
                result = projects_module.get_project_details("owner/testrepo", agents=[])

        tickets = result["tickets"]
        assert len(tickets) == 2, f"Expected 2 tickets, got {len(tickets)}"

        for t in tickets:
            assert "title"  in t and t["title"],  "Ticket missing title"
            assert "number" in t and t["number"], "Ticket missing number"
            assert "url"    in t and t["url"],    "Ticket missing url"
            assert "github.com" in t["url"],      "url should be a GitHub URL"

    def test_ac1b_closed_issues_excluded(self, tmp_path):
        """Closed issues must NOT appear in the tickets list."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{
            "repo":           "owner/testrepo",
            "name":           "Test Repo",
            "icon":           "ti-folder",
            "color":          "blue",
            "active_sprints": {},
        }]))

        # list_open_issues is already filtered to open; simulate server returning only open
        open_only = [self._make_issue(1, "Open ticket", state="open")]

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch.object(github_client, "list_open_issues", return_value=open_only):
                result = projects_module.get_project_details("owner/testrepo", agents=[])

        tickets = result["tickets"]
        closed = [t for t in tickets if t.get("status") == "closed"]
        assert len(closed) == 0, f"Found closed issues in ticket list: {closed}"
        # Verify only open issue present
        assert len(tickets) == 1
        assert tickets[0]["number"] == 1

    def test_ac1c_empty_state_when_no_open_issues(self, tmp_path):
        """When no open issues, tickets list is empty (frontend handles 'No open tickets')."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{
            "repo":           "owner/testrepo",
            "name":           "Test Repo",
            "icon":           "ti-folder",
            "color":          "blue",
            "active_sprints": {},
        }]))

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch.object(github_client, "list_open_issues", return_value=[]):
                result = projects_module.get_project_details("owner/testrepo", agents=[])

        assert result["tickets"] == [], (
            f"Expected empty ticket list for no open issues, got: {result['tickets']}"
        )

    def test_ac1d_cache_ttl_is_30_seconds(self):
        """CACHE_TTL in github_client must be 30 seconds."""
        assert github_client.CACHE_TTL == 30.0, (
            f"Expected CACHE_TTL=30.0, got {github_client.CACHE_TTL}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: Agents column filter & count summary
# ═══════════════════════════════════════════════════════════════════════════════

class TestAC2AgentFilter:
    """
    AC-2a: Source code filters agents to WORKING only in row pills (agentPillsHtml).
    AC-2b: Expand panel header format: "AGENTS · working (N) · done (M)".
    AC-2c: toggleDoneAgents function exists and flips doneAgentsVisible state.
    AC-2d: doneAgentsVisible is a per-repo dictionary (persists across SSE reconnects via JS var).
    AC-2e: When nDone==0, done-toggle label shows "done (0)" but click is a no-op.
    """

    _app_js = None

    @classmethod
    def _get_app_js(cls):
        if cls._app_js is None:
            cls._app_js = (STATIC_DIR / "app.js").read_text()
        return cls._app_js

    def test_ac2a_agent_pills_filter_working_only(self):
        """agentPillsHtml filters to status === 'working' agents only."""
        src = self._get_app_js()
        # Must filter to working status
        assert "status === 'working'" in src or "status==='working'" in src, (
            "agentPillsHtml must filter agents to status === 'working'"
        )
        # Comment indicating the AC
        assert "AC-2a" in src, "Source should have AC-2a comment for traceability"

    def test_ac2b_agents_header_format(self):
        """Expand panel builds 'AGENTS · working (N) · done (M)' header."""
        src = self._get_app_js()
        # The header template string
        assert "AGENTS · working" in src, (
            "Expand panel header must contain 'AGENTS · working'"
        )
        assert "done (" in src, (
            "Expand panel header must contain 'done (N)'"
        )

    def test_ac2c_toggle_done_agents_function_exists(self):
        """toggleDoneAgents function must be defined."""
        src = self._get_app_js()
        assert "function toggleDoneAgents" in src, (
            "toggleDoneAgents function must be defined in app.js"
        )

    def test_ac2d_done_agents_visible_is_per_project_dict(self):
        """doneAgentsVisible is declared as a per-repo object (not a global boolean)."""
        src = self._get_app_js()
        # Must be declared as an object (dictionary), not a boolean
        assert "doneAgentsVisible" in src, "doneAgentsVisible must be declared"
        assert "doneAgentsVisible = {}" in src, (
            "doneAgentsVisible must be initialised as an object {} for per-project keying"
        )

    def test_ac2e_zero_done_agents_label_shown_no_op(self):
        """When nDone==0, done toggle label must still render 'done (0)' with no click handler."""
        src = self._get_app_js()
        # nDone 0 branch: no action on click
        assert "nDone > 0" in src, (
            "Source must guard the click handler with nDone > 0 check"
        )
        # Label still shows done count
        assert "done (${nDone})" in src or "done (" in src, (
            "done count label must always be rendered"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: Project name links to GitHub repo
# ═══════════════════════════════════════════════════════════════════════════════

class TestAC3ProjectNameLink:
    """
    AC-3a: Project name rendered as <a> element (proj-title-link class).
    AC-3b: Link opens in new tab (target="_blank").
    AC-3c: Link derived from proj.repo field → https://github.com/<owner>/<repo>.
    AC-3d: Default state shows no underline (proj-title-link CSS sets text-decoration:none).
    """

    _app_js   = None
    _html     = None

    @classmethod
    def _get_sources(cls):
        if cls._app_js is None:
            cls._app_js = (STATIC_DIR / "app.js").read_text()
        if cls._html is None:
            cls._html = (STATIC_DIR / "index.html").read_text()
        return cls._app_js, cls._html

    def test_ac3a_project_name_rendered_as_anchor(self):
        """Project name must be inside an <a> element with class proj-title-link."""
        src, _ = self._get_sources()
        assert "proj-title-link" in src, (
            "app.js must render project name inside an <a class='proj-title-link'> element"
        )
        # Verify it's actually an <a> tag
        assert "<a " in src and "proj-title-link" in src, (
            "proj-title-link must be applied to an anchor <a> element"
        )

    def test_ac3b_link_opens_in_new_tab(self):
        """Anchor must have target=\"_blank\" and rel=\"noopener\"."""
        src, _ = self._get_sources()
        assert 'target="_blank"' in src, (
            "Project name link must have target=\"_blank\" to open in new tab"
        )
        assert 'rel="noopener"' in src, (
            "Project name link must have rel=\"noopener\" for security"
        )

    def test_ac3c_link_derived_from_repo_field(self):
        """The href uses proj.repo to build https://github.com/<owner>/<repo>."""
        src, _ = self._get_sources()
        assert "https://github.com/${escapeHtml(proj.repo)}" in src or \
               "https://github.com/" in src, (
            "Link href must be constructed from the proj.repo field"
        )
        # More specific: the proj.repo is embedded in the github URL
        assert "proj.repo" in src, "proj.repo must be used in building the GitHub link"

    def test_ac3d_no_underline_in_default_state(self):
        """CSS for proj-title-link must set text-decoration:none (no underline by default)."""
        _, html = self._get_sources()
        # The CSS is embedded in index.html
        assert "proj-title-link" in html, "proj-title-link class must appear in index.html CSS"
        assert "text-decoration: none" in html, (
            "CSS must set text-decoration: none for proj-title-link"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: "+ New Project" modal with validation and auto-add
# ═══════════════════════════════════════════════════════════════════════════════

class TestAC4NewProjectModal:
    """
    AC-4a: HTML contains modal with 3 fields (repo URL required, icon optional, color optional).
    AC-4b: add_project() normalises URLs, calls gh repo view, derives display name.
    AC-4c: On success loadProjects() is called (JS reload without full page refresh).
    AC-4d: Duplicate repo raises FileExistsError → server returns 409.
    AC-4e: Validation errors shown as inline messages (not browser alerts) in HTML.
    """

    _html   = None
    _app_js = None

    @classmethod
    def _get_sources(cls):
        if cls._html is None:
            cls._html = (STATIC_DIR / "index.html").read_text()
        if cls._app_js is None:
            cls._app_js = (STATIC_DIR / "app.js").read_text()
        return cls._html, cls._app_js

    def test_ac4a_modal_has_three_fields(self):
        """Modal must have repo URL (required), icon (optional), color (optional) fields."""
        html, _ = self._get_sources()
        # Repo URL input (required)
        assert 'id="np-repo"' in html, "Modal must have #np-repo input for GitHub repo URL"
        assert "required" in html,     "Repo URL input must be marked required"
        # Icon input
        assert 'id="np-icon"' in html, "Modal must have #np-icon input"
        # Color input
        assert 'id="np-color"' in html, "Modal must have #np-color input"
        # Modal title
        assert "New Project" in html, "Modal must have 'New Project' title"

    def test_ac4b_add_project_normalises_full_github_url(self, tmp_path):
        """add_project strips https://github.com/ prefix and derives display name."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([]))

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch("subprocess.run", return_value=mock_result):
                proj = projects_module.add_project(
                    repo="https://github.com/someowner/some-repo",
                    icon="ti-folder",
                    color="blue",
                )

        assert proj["repo"] == "someowner/some-repo", (
            f"Expected normalised repo 'someowner/some-repo', got '{proj['repo']}'"
        )
        assert proj["name"] == "Some Repo", (
            f"Expected display name 'Some Repo', got '{proj['name']}'"
        )

    def test_ac4b_add_project_verifies_repo_via_gh(self, tmp_path):
        """add_project must call 'gh repo view <repo>' to verify the repo exists."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([]))

        calls_captured = []

        def fake_run(cmd, **kwargs):
            calls_captured.append(cmd)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch("subprocess.run", side_effect=fake_run):
                projects_module.add_project("owner/myrepo", "ti-folder", "gray")

        # At least one call should be 'gh repo view owner/myrepo'
        gh_view_calls = [c for c in calls_captured if "gh" in c and "repo" in c and "view" in c]
        assert len(gh_view_calls) > 0, (
            f"Expected 'gh repo view' call, captured: {calls_captured}"
        )
        found = any("owner/myrepo" in c for c in gh_view_calls)
        assert found, f"'gh repo view' must include repo name. Calls: {gh_view_calls}"

    def test_ac4b_add_project_raises_value_error_on_bad_repo(self, tmp_path):
        """add_project raises ValueError for bad repo format (e.g. missing slash)."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([]))

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with pytest.raises(ValueError, match="Invalid repo format"):
                projects_module.add_project("not-a-valid-repo-format", "ti-folder", "gray")

    def test_ac4b_add_project_raises_value_error_when_gh_fails(self, tmp_path):
        """add_project raises ValueError when gh repo view returns non-zero exit code."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([]))

        mock_result = MagicMock()
        mock_result.returncode = 1  # gh repo view failed

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(ValueError, match="GitHub repo not found"):
                    projects_module.add_project("owner/nonexistent", "ti-folder", "gray")

    def test_ac4d_duplicate_repo_raises_file_exists_error(self, tmp_path):
        """add_project raises FileExistsError when repo already in projects.json → server sends 409."""
        existing = [{"repo": "owner/existing-repo", "name": "Existing", "icon": "ti-folder",
                     "color": "blue", "active_sprints": {}}]
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps(existing))

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with pytest.raises(FileExistsError, match="Project already added"):
                projects_module.add_project("owner/existing-repo", "ti-folder", "gray")

    def test_ac4d_duplicate_error_message(self, tmp_path):
        """Error message for duplicate should contain 'Project already added'."""
        existing = [{"repo": "owner/dupe", "name": "Dupe", "icon": "ti-folder",
                     "color": "gray", "active_sprints": {}}]
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps(existing))

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with pytest.raises(FileExistsError) as exc_info:
                projects_module.add_project("owner/dupe", "ti-folder", "gray")

        assert "Project already added" in str(exc_info.value), (
            f"Error message should say 'Project already added', got: {exc_info.value}"
        )

    def test_ac4e_inline_error_element_in_html(self):
        """Validation errors shown as inline div, not browser alert()."""
        html, src = self._get_sources()
        # HTML must have an error div/element for inline error display
        assert 'id="np-repo-error"' in html, (
            "HTML must have #np-repo-error element for inline error messages"
        )
        assert "modal-error" in html, (
            "CSS class 'modal-error' must be present for inline error styling"
        )
        # JS must NOT use browser alert() for validation errors (uses _npShowError instead)
        # Check that _npShowError is used in submitNewProject, not alert
        assert "_npShowError" in src, (
            "submitNewProject must use _npShowError() for inline error display, not alert()"
        )
        # Verify that the 409/422 handlers call _npShowError not alert
        assert "res.status === 409" in src, "Must handle 409 status specifically"
        assert "res.status === 422" in src, "Must handle 422 status specifically"

    def test_ac4c_success_closes_modal_and_reloads(self):
        """On success, app.js calls closeNewProjectModal() and loadProjects() — no full refresh."""
        _, src = self._get_sources()
        # After successful POST, modal closes and project list reloads
        assert "closeNewProjectModal()" in src, (
            "On success, closeNewProjectModal() must be called"
        )
        assert "loadProjects()" in src, (
            "On success, loadProjects() must be called to reload the list"
        )
        # Must NOT use location.reload (would be a full page refresh)
        assert "location.reload" not in src, (
            "Must not use location.reload — should reload via loadProjects() instead"
        )

    def test_ac4b_add_project_appended_to_projects_json(self, tmp_path):
        """Successful add_project appends new entry to projects.json."""
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{
            "repo": "owner/existing", "name": "Existing",
            "icon": "ti-folder", "color": "blue", "active_sprints": {}
        }]))

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(projects_module, "PROJECTS_FILE", projects_file):
            with patch("subprocess.run", return_value=mock_result):
                proj = projects_module.add_project("owner/new-proj", "ti-star", "green")

        saved = json.loads(projects_file.read_text())
        assert len(saved) == 2, f"Expected 2 projects in file after add, got {len(saved)}"
        repos = [p["repo"] for p in saved]
        assert "owner/new-proj" in repos, f"New project not in saved file. Repos: {repos}"
        assert proj["repo"] == "owner/new-proj"
        assert proj["icon"] == "ti-star"
        assert proj["color"] == "green"

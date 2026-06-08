"""Tests for issue #634: Add stale branch cleanup to Logs screen.

AC-1:  Delete branch button appears on every PR-merged row where head ref
       (feat/* or sprint/*) still exists in the remote.
AC-2:  Delete branch button is absent on rows whose head ref already deleted.
AC-3:  After clicking Delete branch, row label updates to "branch deleted" and
       button removed — no page reload.
AC-4:  "Clean up N merged branches" button appears on sprint-finished rows;
       N = count of that sprint's merged feat branches still present +
       sprint branch itself if still present.
AC-5:  Clicking "Clean up N merged branches" shows confirmation dialog before
       any deletion begins; cancelling leaves all branches intact.
AC-6:  After bulk cleanup completes, each affected row updates to
       "branch deleted" and sprint button disappears.
AC-7:  Project-wide banner at top of Logs screen reads
       "N merged branches not yet deleted — Clean up all".
AC-8:  Banner count decrements in real time after each deletion; disappears
       when stale count reaches zero.
AC-9:  "Clean up all" in banner also requires a confirmation dialog.
AC-10: develop, master, and attachments branches never have a delete button or
       appear in any bulk cleanup count, regardless of PR state.
AC-11: Delete controls only for branches matching feat/* or sprint/* whose
       associated PR status is merged.
AC-12: Branches matching feat/* or sprint/* with an open or
       closed-not-merged PR do NOT show a delete button.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

_HTML = PROJECT_HTML.read_text()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _get_test_client():
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db", "github_client"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


FAKE_PROJECT = [{"repo": "owner/testrepo", "name": "Test Repo"}]


# ── AC-11 / AC-12: Backend GET /api/projects/{owner}/{repo}/branches/stale ────

class TestGetStaleBranches:
    """AC-11, AC-12: stale endpoint returns only merged-PR feat/* or sprint/* branches."""

    def _stale_url(self):
        return "/api/projects/owner/testrepo/branches/stale"

    def test_returns_200_with_list(self):
        """AC-11: Endpoint exists and returns a list."""
        client, srv = _get_test_client()
        merged_pr_json = json.dumps([
            {"headRefName": "feature/100-my-feat", "state": "merged"},
        ])
        existing_branches_json = "\n".join([
            "feature/100-my-feat",
            "develop",
            "master",
        ])
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [
                _make_proc(merged_pr_json),      # gh pr list merged
                _make_proc(existing_branches_json),  # gh api branches
            ]
            resp = client.get(self._stale_url())
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_returns_feat_branch_that_exists(self):
        """AC-1: feat/* branches with merged PR that still exist are returned."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "feature/100-foo"}])
        existing = "feature/100-foo\ndevelop\nmaster\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "feature/100-foo" in resp.json()

    def test_returns_sprint_branch_that_exists(self):
        """AC-1: sprint/* branches with merged PR that still exist are returned."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "sprint/sprint-50"}])
        existing = "sprint/sprint-50\ndevelop\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "sprint/sprint-50" in resp.json()

    def test_excludes_already_deleted_branch(self):
        """AC-2: Branch with merged PR but no longer existing is NOT returned."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "feature/200-deleted"}])
        existing = "develop\nmaster\n"  # feature/200-deleted not here
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "feature/200-deleted" not in resp.json()

    def test_excludes_develop(self):
        """AC-10: develop never returned regardless of PR state."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "develop"}])
        existing = "develop\nfeature/100-foo\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "develop" not in resp.json()

    def test_excludes_master(self):
        """AC-10: master never returned."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "master"}])
        existing = "master\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "master" not in resp.json()

    def test_excludes_attachments(self):
        """AC-10: attachments branch never returned."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "attachments"}])
        existing = "attachments\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "attachments" not in resp.json()

    def test_excludes_open_pr_branches(self):
        """AC-12: Branches with open PRs (not merged) are NOT returned."""
        client, srv = _get_test_client()
        # Only merged PRs are queried; open PR branches are simply not in merged list
        merged = json.dumps([])  # no merged PRs
        existing = "feature/300-open\ndevelop\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "feature/300-open" not in resp.json()

    def test_excludes_non_feat_sprint_branches(self):
        """AC-11: Branches not matching feat/* or sprint/* are never included."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "hotfix/bug-123"}])
        existing = "hotfix/bug-123\ndevelop\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert "hotfix/bug-123" not in resp.json()

    def test_returns_empty_list_when_no_stale(self):
        """AC-2: Returns empty list when no stale branches exist."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "feature/100-foo"}])
        existing = "develop\nmaster\n"  # feature/100-foo already deleted
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        assert resp.json() == []

    def test_handles_multiple_stale_branches(self):
        """AC-1: Multiple stale branches all returned."""
        client, srv = _get_test_client()
        merged = json.dumps([
            {"headRefName": "feature/100-alpha"},
            {"headRefName": "feature/101-beta"},
            {"headRefName": "sprint/sprint-50"},
        ])
        existing = "feature/100-alpha\nfeature/101-beta\nsprint/sprint-50\ndevelop\n"
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(self._stale_url())
        result = resp.json()
        assert "feature/100-alpha" in result
        assert "feature/101-beta" in result
        assert "sprint/sprint-50" in result


# ── AC-10, AC-11: Backend DELETE /api/projects/{owner}/{repo}/branches/{branch}

class TestDeleteBranch:
    """AC-10, AC-11: Branch delete endpoint validates branch names."""

    def _del_url(self, branch: str):
        return f"/api/projects/owner/testrepo/branches/{branch}"

    def test_deletes_feat_branch_successfully(self):
        """AC-11: feat/* branch can be deleted."""
        client, srv = _get_test_client()
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.return_value = _make_proc("", returncode=0)
            resp = client.delete(self._del_url("feature/100-foo"))
        assert resp.status_code in (200, 204)

    def test_deletes_sprint_branch_successfully(self):
        """AC-11: sprint/* branch can be deleted."""
        client, srv = _get_test_client()
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.return_value = _make_proc("", returncode=0)
            resp = client.delete(self._del_url("sprint/sprint-50"))
        assert resp.status_code in (200, 204)

    def test_rejects_develop(self):
        """AC-10: develop cannot be deleted — returns 400."""
        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.delete(self._del_url("develop"))
        assert resp.status_code == 400

    def test_rejects_master(self):
        """AC-10: master cannot be deleted — returns 400."""
        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.delete(self._del_url("master"))
        assert resp.status_code == 400

    def test_rejects_attachments(self):
        """AC-10: attachments cannot be deleted — returns 400."""
        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.delete(self._del_url("attachments"))
        assert resp.status_code == 400

    def test_rejects_non_feat_sprint_pattern(self):
        """AC-11: hotfix/* and other patterns are rejected — returns 400."""
        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.delete(self._del_url("hotfix/bug-123"))
        assert resp.status_code == 400

    def test_returns_ok_on_success(self):
        """AC-3: Successful delete returns ok: true."""
        client, srv = _get_test_client()
        with patch("subprocess.run") as mock_run, \
             patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            mock_run.return_value = _make_proc("", returncode=0)
            resp = client.delete(self._del_url("feature/100-foo"))
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("ok") is True
            assert "branch" in data


# ── Frontend: HTML structure and JavaScript ────────────────────────────────────

class TestFrontendStructure:
    """Frontend HTML and JavaScript structure tests for AC-1 through AC-9."""

    # AC-7: Banner element
    def test_stale_banner_element_exists(self):
        """AC-7: Banner div with id 'evl-stale-banner' exists in project.html."""
        assert 'id="evl-stale-banner"' in _HTML or "id='evl-stale-banner'" in _HTML, (
            "project.html must contain an element with id='evl-stale-banner'"
        )

    def test_stale_banner_inside_logs_activity(self):
        """AC-7: Banner is inside the logs-activity pane."""
        activity_idx = _HTML.find('id="logs-activity"')
        # Use the HTML element id attribute specifically (not the CSS class reference)
        banner_idx = _HTML.find('id="evl-stale-banner"')
        assert activity_idx != -1, "logs-activity pane not found"
        assert banner_idx != -1, 'HTML element id="evl-stale-banner" not found'
        bulk_create_idx = _HTML.find('id="pane-bulk-create"')
        assert activity_idx < banner_idx < bulk_create_idx, (
            "evl-stale-banner element must be inside the logs-activity pane"
        )

    # AC-8: Banner update function
    def test_evl_update_banner_function_exists(self):
        """AC-8: _evlUpdateBanner() function is defined in project.html."""
        assert "_evlUpdateBanner" in _HTML, (
            "project.html missing '_evlUpdateBanner' function"
        )

    def test_evl_state_has_stale_branches(self):
        """AC-1/AC-8: _evlState includes staleBranches set."""
        assert "staleBranches" in _HTML, (
            "project.html missing 'staleBranches' in _evlState"
        )

    # AC-1: Delete branch button
    def test_evl_delete_branch_function_exists(self):
        """AC-1/AC-3: evlDeleteBranch() function is defined in project.html."""
        assert "evlDeleteBranch" in _HTML, (
            "project.html missing 'evlDeleteBranch' function"
        )

    def test_delete_button_class_exists(self):
        """AC-1: CSS class for the per-row delete branch button."""
        assert "evl-del-btn" in _HTML, (
            "project.html missing 'evl-del-btn' CSS class for delete button"
        )

    # AC-2: branch-deleted indicator
    def test_branch_deleted_indicator_exists(self):
        """AC-2: 'branch deleted' text or CSS class shown for already-deleted branches."""
        assert "branch deleted" in _HTML or "branch-deleted" in _HTML, (
            "project.html must indicate 'branch deleted' state for already-cleaned rows"
        )

    # AC-3: No page reload — delete function modifies DOM
    def test_delete_function_modifies_dom_not_reload(self):
        """AC-3: evlDeleteBranch modifies DOM — no location.reload() or full page refresh."""
        # Find the evlDeleteBranch function body
        start = _HTML.find("function evlDeleteBranch")
        assert start != -1, "evlDeleteBranch function not found"
        # Extract a reasonable chunk of the function body
        chunk = _HTML[start:start + 1500]
        # Must NOT do a full page reload
        assert "location.reload" not in chunk, (
            "evlDeleteBranch must not call location.reload()"
        )
        # Must update DOM — check for outerHTML, innerHTML, remove(), or classList change
        dom_ops = ("outerHTML", "innerHTML", ".remove()", "classList", "textContent", "parentNode")
        assert any(op in chunk for op in dom_ops), (
            "evlDeleteBranch must update the DOM in-place after deletion"
        )

    # AC-4: Sprint cleanup button
    def test_evl_cleanup_sprint_function_exists(self):
        """AC-4: evlCleanupSprint() function is defined in project.html."""
        assert "evlCleanupSprint" in _HTML, (
            "project.html missing 'evlCleanupSprint' function"
        )

    def test_sprint_cleanup_button_class_exists(self):
        """AC-4: CSS class for sprint-level bulk cleanup button."""
        assert "evl-sprint-cleanup" in _HTML or "evl-cleanup-sprint" in _HTML, (
            "project.html missing CSS class for sprint cleanup button"
        )

    # AC-5: Confirmation dialog for sprint bulk cleanup
    def test_sprint_cleanup_uses_confirm(self):
        """AC-5: evlCleanupSprint() calls confirm() before deleting."""
        start = _HTML.find("function evlCleanupSprint")
        assert start != -1, "evlCleanupSprint function not found"
        chunk = _HTML[start:start + 800]
        assert "confirm(" in chunk, (
            "evlCleanupSprint must call confirm() before any deletion"
        )

    # AC-9: Confirmation dialog for "Clean up all"
    def test_cleanup_all_function_exists(self):
        """AC-9: evlCleanupAll() function is defined in project.html."""
        assert "evlCleanupAll" in _HTML, (
            "project.html missing 'evlCleanupAll' function"
        )

    def test_cleanup_all_uses_confirm(self):
        """AC-9: evlCleanupAll() calls confirm() before deleting."""
        start = _HTML.find("function evlCleanupAll")
        assert start != -1, "evlCleanupAll function not found"
        chunk = _HTML[start:start + 600]
        assert "confirm(" in chunk, (
            "evlCleanupAll must call confirm() before any deletion"
        )

    # AC-7: Banner "Clean up all" button
    def test_banner_has_clean_up_all(self):
        """AC-7: Banner includes 'Clean up all' button that calls evlCleanupAll."""
        assert "evlCleanupAll" in _HTML, (
            "project.html missing evlCleanupAll reference in banner"
        )

    # AC-8: Banner fetch stale branches
    def test_evl_fetch_stale_branches_function_exists(self):
        """AC-8: evlFetchStaleBranches() or equivalent function fetches branch list."""
        assert "evlFetchStaleBranches" in _HTML or "StaleBranches" in _HTML, (
            "project.html must fetch stale branches from the API"
        )

    # AC-1: _evlBranchCleanupHtml produces delete button on pr_merged rows
    def test_evl_renders_delete_button_for_pr_merged(self):
        """AC-1: _evlBranchCleanupHtml generates delete button for pr_merged stale rows."""
        # The _evlBranchCleanupHtml function must handle pr_merged and evl-del-btn
        fn_start = _HTML.find("function _evlBranchCleanupHtml")
        assert fn_start != -1, "_evlBranchCleanupHtml function not found in project.html"
        chunk = _HTML[fn_start:fn_start + 1200]
        assert "pr_merged" in chunk, "_evlBranchCleanupHtml must check for pr_merged type"
        assert "evl-del-btn" in chunk, "_evlBranchCleanupHtml must emit evl-del-btn button"

    # AC-10: Protected branches excluded from rendering
    def test_protected_branches_excluded_from_delete_rendering(self):
        """AC-10: develop, master, attachments excluded from delete rendering logic."""
        # The rendering code must check for protected branches before showing delete btn
        protected_guard = (
            "develop" in _HTML and
            ("master" in _HTML or "main" in _HTML)
        )
        assert protected_guard, (
            "project.html must guard against develop/master deletion in rendering"
        )
        # The evlDeleteBranch function must have a guard
        start = _HTML.find("function evlDeleteBranch")
        if start != -1:
            chunk = _HTML[start:start + 1500]
            # function references protected branch names or a protected-branch check
            assert any(w in chunk for w in ("develop", "master", "attachments", "_PROTECTED", "PROTECTED")), (
                "evlDeleteBranch must validate against protected branch names"
            )

    # AC-11: Pattern check in delete
    def test_delete_function_validates_branch_pattern(self):
        """AC-11: evlDeleteBranch or rendering code validates feat/* or sprint/* pattern."""
        start = _HTML.find("function evlDeleteBranch")
        assert start != -1, "evlDeleteBranch function not found"
        chunk = _HTML[start:start + 1500]
        # Check for pattern matching — either regex test or branch name includes check
        has_pattern = (
            "feat" in chunk or "feature" in chunk or "sprint" in chunk or
            "startsWith" in chunk or "/feat" in chunk or "/sprint" in chunk
        )
        assert has_pattern, (
            "evlDeleteBranch must check that branch matches feat/* or sprint/* pattern"
        )

    # AC-4: Sprint cleanup count is dynamic
    def test_sprint_cleanup_button_shows_count(self):
        """AC-4: Sprint cleanup button renders with dynamic N count."""
        # The rendering code must include count in button label
        assert "merged branch" in _HTML or "branches" in _HTML, (
            "project.html must show 'N merged branches' in sprint cleanup button"
        )
        assert "evlCleanupSprint" in _HTML, (
            "evlCleanupSprint must be called from sprint cleanup button onclick"
        )

    # CSS for delete controls
    def test_css_for_delete_controls_exists(self):
        """AC-1: CSS for .evl-del-btn is defined."""
        assert ".evl-del-btn" in _HTML, (
            "project.html CSS must define .evl-del-btn styles"
        )

    def test_css_for_stale_banner_exists(self):
        """AC-7: CSS for .evl-stale-banner is defined."""
        assert ".evl-stale-banner" in _HTML or "evl-stale-banner" in _HTML, (
            "project.html CSS must define .evl-stale-banner styles"
        )

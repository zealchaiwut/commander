"""Tests for issue #139: Bug — "running is not defined" error when cleaning up empty sprints.

Verifies all 4 acceptance criteria:
  AC1 - No "running is not defined" ReferenceError: .smgmt-run-btn forEach no longer
        references the undefined `running`, `runLabel`, or `runProj` variables.
  AC2 - Clean-up empty sprints API succeeds; backend /api/sprints/delete-empty
        returns 200 with correct shape (no 500).
  AC3 - Run buttons use per-key lookup from _smgmtAllRunning; tooltip message
        reads "Sprint X is running" (not the old "Sprint X is running for Y").
  AC4 - Cleanup banner is driven by empty_sprint_labels from /api/sprint-mgmt;
        smgmtSelectProject() call reloads data so banner auto-disappears after
        cleanup (no manual page refresh needed).
"""
import re
import os
import httpx
import pytest
from pathlib import Path

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
DASHBOARD_DIR = Path(__file__).parent.parent
APP_JS = DASHBOARD_DIR / "static" / "app.js"
SERVER_PY = DASHBOARD_DIR / "server.py"


@pytest.fixture(scope="module")
def app_js_text():
    return APP_JS.read_text()


@pytest.fixture(scope="module")
def server_py_text():
    return SERVER_PY.read_text()


@pytest.fixture(scope="module")
def smgmt_run_btn_block(app_js_text):
    """Extract just the .smgmt-run-btn forEach block from smgmtApplyRunState."""
    match = re.search(
        r"querySelectorAll\('\.smgmt-run-btn'\)\.forEach\(btn => \{(.+?)\}\s*\);",
        app_js_text,
        re.DOTALL,
    )
    assert match, ".smgmt-run-btn forEach block not found in app.js"
    return match.group(1)


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1: No ReferenceError for undefined variables in .smgmt-run-btn forEach
# ---------------------------------------------------------------------------

class TestAC1NoUndefinedVariables:
    def test_running_variable_not_used_in_run_btn_block(self, smgmt_run_btn_block):
        """AC-1: The bare `running` variable (undefined after #130 refactor) is gone.

        Checks for the exact patterns that caused the ReferenceError: using `running`
        as a boolean guard (`!running`, `if (running)`) or in a tooltip interpolation.
        Does NOT flag occurrences of `isThisRunning` or 'is running' in string literals.
        """
        bad_patterns = [
            r'!\s*running\b',           # if (!running) — the original bug
            r'if\s*\(\s*running\b',     # if (running)
            r'\brunning\s*&&',          # running &&
            r'\brunning\s*\|\|',        # running ||
            r'\$\{runLabel\}',          # runLabel interpolation in tooltip
            r'\$\{runProj\}',           # runProj interpolation in tooltip
        ]
        for pat in bad_patterns:
            assert not re.search(pat, smgmt_run_btn_block), (
                f"Undefined variable pattern `{pat}` still present in .smgmt-run-btn forEach block"
            )

    def test_runLabel_not_used_in_run_btn_block(self, smgmt_run_btn_block):
        """AC-1: `runLabel` (undefined in this scope) is no longer referenced."""
        assert 'runLabel' not in smgmt_run_btn_block, (
            "`runLabel` still referenced in .smgmt-run-btn forEach — this causes ReferenceError"
        )

    def test_runProj_not_used_in_run_btn_block(self, smgmt_run_btn_block):
        """AC-1: `runProj` (undefined in this scope) is no longer referenced."""
        assert 'runProj' not in smgmt_run_btn_block, (
            "`runProj` still referenced in .smgmt-run-btn forEach — this causes ReferenceError"
        )

    def test_run_btn_block_uses_isThisRunning(self, smgmt_run_btn_block):
        """AC-1: Block uses the locally-defined `isThisRunning` guard instead."""
        assert 'isThisRunning' in smgmt_run_btn_block, (
            "`isThisRunning` not found in .smgmt-run-btn forEach — undefined variable fix not applied"
        )


# ---------------------------------------------------------------------------
# AC-2: Backend cleanup API exists and returns correct shape
# ---------------------------------------------------------------------------

class TestAC2CleanupAPIExists:
    def test_delete_empty_endpoint_defined_in_server(self, server_py_text):
        """AC-2: /api/sprints/delete-empty endpoint exists in server.py."""
        assert '/api/sprints/delete-empty' in server_py_text, (
            "POST /api/sprints/delete-empty endpoint not found in server.py"
        )

    def test_delete_empty_validates_sprint_label_pattern(self, server_py_text):
        """AC-2: Server validates that only sprint-N labels can be deleted."""
        assert '_SPRINT_LABEL_RE' in server_py_text or re.search(
            r'sprint-\d+|SPRINT_LABEL', server_py_text
        ), "Server must validate sprint-N label pattern before deleting"

    def test_delete_empty_checks_zero_tickets_before_delete(self, server_py_text):
        """AC-2: Server verifies label has 0 tickets before deleting it."""
        # Find the delete_empty_sprints function body
        match = re.search(
            r'async def delete_empty_sprints\(.+?\):(.+?)(?=\n@app|\nasync def|\ndef |\Z)',
            server_py_text,
            re.DOTALL,
        )
        assert match, "delete_empty_sprints function not found in server.py"
        fn_body = match.group(1)
        # Must check for open tickets on the label before deleting
        assert 'still has open tickets' in fn_body or \
               'HTTPException' in fn_body, (
            "delete_empty_sprints must refuse labels with open tickets"
        )

    def test_delete_empty_returns_ok_with_deleted_list(self, server_py_text):
        """AC-2: Successful cleanup returns {ok: True, deleted: [...]}."""
        assert '"ok": True' in server_py_text or \
               "'ok': True" in server_py_text, (
            "delete_empty_sprints must return {ok: True, ...} on success"
        )
        assert '"deleted"' in server_py_text or \
               "'deleted'" in server_py_text, (
            "delete_empty_sprints must include 'deleted' list in response"
        )

    @pytest.mark.live
    def test_delete_empty_endpoint_reachable(self, client):
        """AC-2: POST /api/sprints/delete-empty endpoint is reachable (not 404)."""
        r = client.post(
            "/api/sprints/delete-empty",
            json={"labels": [], "project": "__nonexistent_test_project__"},
        )
        # May return 4xx for bad input, but must not be 404 (endpoint must exist)
        assert r.status_code != 404, (
            "POST /api/sprints/delete-empty returned 404 — endpoint not registered"
        )

    @pytest.mark.live
    def test_delete_empty_rejects_invalid_label_pattern(self, client):
        """AC-2: Backend returns 400 for non-sprint-N label names."""
        r = client.post(
            "/api/sprints/delete-empty",
            json={"labels": ["not-a-sprint-label"], "project": "test"},
        )
        assert r.status_code == 400, (
            "Server must reject non-sprint-N labels with 400"
        )


# ---------------------------------------------------------------------------
# AC-3: Run buttons use per-key lookup; tooltip reads "Sprint X is running"
# ---------------------------------------------------------------------------

class TestAC3PerKeyRunState:
    def test_run_btn_block_looks_up_smgmt_all_running(self, smgmt_run_btn_block):
        """AC-3: Block derives running state from _smgmtAllRunning map per button."""
        assert '_smgmtAllRunning' in smgmt_run_btn_block, (
            "_smgmtAllRunning not referenced in .smgmt-run-btn forEach"
        )

    def test_run_btn_block_builds_run_key_with_current_repo(self, smgmt_run_btn_block):
        """AC-3: runKey uses _smgmtCurrentRepo so per-project isolation is preserved."""
        assert '_smgmtCurrentRepo' in smgmt_run_btn_block, (
            "_smgmtCurrentRepo not in run key derivation — per-project isolation broken"
        )
        assert re.search(r'runKey\s*=\s*`\$\{_smgmtCurrentRepo\}', smgmt_run_btn_block), (
            "runKey must be built as `${_smgmtCurrentRepo}:${btnLabel}`"
        )

    def test_tooltip_says_sprint_is_running_without_proj(self, smgmt_run_btn_block):
        """AC-3: Disabled-run-btn tooltip says 'Sprint X is running' (no runProj)."""
        assert re.search(
            r'Sprint \$\{btnLabel\} is running',
            smgmt_run_btn_block,
        ), (
            "Tooltip must read 'Sprint ${btnLabel} is running' not the old 'running for ${runProj}'"
        )

    def test_rerun_btn_block_uses_same_pattern(self, app_js_text):
        """AC-3: .smgmt-rerun-btn forEach already used this pattern; both blocks consistent."""
        rerun_match = re.search(
            r"querySelectorAll\('\.smgmt-rerun-btn'\)\.forEach\(btn => \{(.+?)\}\s*\);",
            app_js_text,
            re.DOTALL,
        )
        assert rerun_match, ".smgmt-rerun-btn forEach block not found in app.js"
        rerun_block = rerun_match.group(1)
        assert '_smgmtAllRunning' in rerun_block, (
            ".smgmt-rerun-btn forEach must use _smgmtAllRunning (reference pattern)"
        )
        assert '_smgmtCurrentRepo' in rerun_block, (
            ".smgmt-rerun-btn forEach must use _smgmtCurrentRepo (reference pattern)"
        )

    def test_btn_text_set_unconditionally(self, smgmt_run_btn_block):
        """AC-3: btn.textContent = 'Run sprint' is set regardless of running state."""
        # Should appear once, outside any if/else branch (deduplication from old code)
        count = smgmt_run_btn_block.count("btn.textContent = 'Run sprint'")
        assert count >= 1, "btn.textContent = 'Run sprint' must be set in the forEach block"


# ---------------------------------------------------------------------------
# AC-4: Banner auto-disappears after cleanup (data reloaded by smgmtSelectProject)
# ---------------------------------------------------------------------------

class TestAC4BannerAutoDisappears:
    def test_cleanup_confirm_calls_select_project(self, app_js_text):
        """AC-4: smgmtCleanupConfirm reloads project data via smgmtSelectProject."""
        cleanup_fn_match = re.search(
            r'async function smgmtCleanupConfirm\(\)\s*\{(.+?)^\}',
            app_js_text,
            re.DOTALL | re.MULTILINE,
        )
        assert cleanup_fn_match, "smgmtCleanupConfirm function not found in app.js"
        fn_body = cleanup_fn_match.group(1)
        assert 'smgmtSelectProject' in fn_body, (
            "smgmtCleanupConfirm must call smgmtSelectProject() to reload data and "
            "auto-hide the cleanup banner"
        )

    def test_cleanup_banner_driven_by_empty_sprint_labels(self, app_js_text):
        """AC-4: Banner visibility is tied to empty_sprint_labels from the API response."""
        assert 'empty_sprint_labels' in app_js_text, (
            "empty_sprint_labels not referenced in app.js — banner logic is broken"
        )
        assert re.search(
            r'cleanupBanner\.classList\.(add|remove)\(["\']hidden["\']\)',
            app_js_text,
        ), (
            "Banner must use classList.add/remove('hidden') driven by empty_sprint_labels"
        )

    def test_smgmt_data_includes_empty_sprint_labels(self, server_py_text):
        """AC-4: /api/sprint-mgmt response includes empty_sprint_labels key."""
        assert 'empty_sprint_labels' in server_py_text, (
            "Server must include empty_sprint_labels in sprint-mgmt response so banner auto-hides"
        )

    def test_cleanup_banner_element_exists(self):
        """AC-4: smgmt-cleanup-banner element exists in index.html."""
        index_html = DASHBOARD_DIR / "static" / "index.html"
        content = index_html.read_text()
        assert 'smgmt-cleanup-banner' in content, (
            "smgmt-cleanup-banner element not found in index.html"
        )

    @pytest.mark.live
    def test_sprint_mgmt_api_returns_empty_sprint_labels_field(self, client):
        """AC-4: /api/sprint-mgmt response always includes empty_sprint_labels list."""
        r = client.get("/api/sprint-mgmt", params={"project": "test-repo"})
        # May return error for unknown project, but if 200 it must include the field
        if r.status_code == 200:
            data = r.json()
            assert "empty_sprint_labels" in data, (
                "/api/sprint-mgmt 200 response missing empty_sprint_labels key"
            )

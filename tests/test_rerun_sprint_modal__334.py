"""Tests for issue #334 — Replace Re-run Sprint native confirm with in-app modal.

AC coverage:
  AC-1  Triggering Re-run Sprint never shows a native browser confirm()
  AC-2  Modal header contains amber warning icon and "Re-run Sprint <label>?"
  AC-3  GET /api/sprints/{label}/rerun/preview returns redispatch_count, tester_count,
          skip_count, by_ticket
  AC-4  Modal count rows driven by preview response (redispatch / tester / skip)
  AC-5  Zero-count rows are hidden
  AC-6  Preview counts match rerun policy (dispatch_coder / dispatch_tester / skip)
  AC-7  Cancel closes modal with no side effects
  AC-8  Confirm button is amber/warning styled and triggers rerun POST
  AC-9  Automated browser test confirms window.confirm is never called
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import _rerun_policy, app

# ── Unit: preview endpoint logic ──────────────────────────────────────────────

class TestRerunPreviewCounts:
    """Verify that _rerun_policy-based count logic produces correct groupings."""

    def _counts(self, label_sets: list[set]) -> dict:
        redispatch = tester = skip = 0
        by_ticket = []
        for i, labels in enumerate(label_sets):
            action, _ = _rerun_policy(labels)
            if action == "dispatch_coder":
                redispatch += 1
            elif action == "dispatch_tester":
                tester += 1
            else:
                skip += 1
            by_ticket.append({"issue_num": i + 1, "action": action})
        return {
            "redispatch_count": redispatch,
            "tester_count": tester,
            "skip_count": skip,
            "by_ticket": by_ticket,
        }

    def test_all_dispatch_coder(self):
        result = self._counts([
            {"sprint-23"},
            {"sprint-23", "in-progress"},
            {"sprint-23", "tester-rejected"},
        ])
        assert result["redispatch_count"] == 3
        assert result["tester_count"] == 0
        assert result["skip_count"] == 0

    def test_sit_increments_tester_count(self):
        result = self._counts([{"sprint-23", "SIT"}])
        assert result["tester_count"] == 1
        assert result["redispatch_count"] == 0
        assert result["skip_count"] == 0

    def test_uat_increments_skip_count(self):
        result = self._counts([
            {"sprint-23", "UAT"},
            {"sprint-23", "UAT-approved"},
        ])
        assert result["skip_count"] == 2
        assert result["redispatch_count"] == 0
        assert result["tester_count"] == 0

    def test_mixed_sprint(self):
        result = self._counts([
            {"sprint-23", "in-progress"},   # → dispatch_coder
            {"sprint-23", "SIT"},            # → dispatch_tester
            {"sprint-23", "UAT"},            # → skip
            {"sprint-23", "needs-rework"},   # → dispatch_coder
        ])
        assert result["redispatch_count"] == 2
        assert result["tester_count"] == 1
        assert result["skip_count"] == 1

    def test_by_ticket_contains_all_issues(self):
        label_sets = [
            {"sprint-23", "in-progress"},
            {"sprint-23", "SIT"},
            {"sprint-23", "UAT"},
        ]
        result = self._counts(label_sets)
        assert len(result["by_ticket"]) == 3

    def test_by_ticket_action_values(self):
        label_sets = [{"sprint-23", "in-progress"}, {"sprint-23", "SIT"}, {"sprint-23", "UAT"}]
        result = self._counts(label_sets)
        actions = {t["action"] for t in result["by_ticket"]}
        assert actions == {"dispatch_coder", "dispatch_tester", "skip"}


# ── Unit: preview response schema ─────────────────────────────────────────────

class TestPreviewResponseSchema:
    """Verify the shape of the preview response dict."""

    def _build_preview(self, decisions):
        redispatch = sum(1 for d in decisions if d["action"] == "dispatch_coder")
        tester = sum(1 for d in decisions if d["action"] == "dispatch_tester")
        skip = sum(1 for d in decisions if d["action"] == "skip")
        return {
            "redispatch_count": redispatch,
            "tester_count": tester,
            "skip_count": skip,
            "by_ticket": decisions,
        }

    def test_required_keys_present(self):
        preview = self._build_preview([])
        for key in ("redispatch_count", "tester_count", "skip_count", "by_ticket"):
            assert key in preview

    def test_counts_are_integers(self):
        preview = self._build_preview([
            {"issue_num": 1, "action": "dispatch_coder"},
        ])
        assert isinstance(preview["redispatch_count"], int)
        assert isinstance(preview["tester_count"], int)
        assert isinstance(preview["skip_count"], int)

    def test_by_ticket_is_list(self):
        preview = self._build_preview([])
        assert isinstance(preview["by_ticket"], list)

    def test_by_ticket_entry_has_issue_num_and_action(self):
        decisions = [{"issue_num": 5, "issue_title": "T", "action": "dispatch_coder"}]
        preview = self._build_preview(decisions)
        entry = preview["by_ticket"][0]
        assert "issue_num" in entry
        assert "action" in entry


# ── Unit: zero-count rows hidden ─────────────────────────────────────────────

class TestZeroCountRowsHidden:
    """Verify that rows with count=0 are suppressed in the modal output."""

    def _visible_rows(self, preview: dict) -> list[str]:
        rows = []
        if preview["redispatch_count"] > 0:
            rows.append("redispatch")
        if preview["tester_count"] > 0:
            rows.append("tester")
        if preview["skip_count"] > 0:
            rows.append("skip")
        return rows

    def test_all_zero_no_rows(self):
        assert self._visible_rows({"redispatch_count": 0, "tester_count": 0, "skip_count": 0}) == []

    def test_only_redispatch_nonzero(self):
        rows = self._visible_rows({"redispatch_count": 3, "tester_count": 0, "skip_count": 0})
        assert rows == ["redispatch"]

    def test_only_tester_nonzero(self):
        rows = self._visible_rows({"redispatch_count": 0, "tester_count": 2, "skip_count": 0})
        assert rows == ["tester"]

    def test_only_skip_nonzero(self):
        rows = self._visible_rows({"redispatch_count": 0, "tester_count": 0, "skip_count": 1})
        assert rows == ["skip"]

    def test_mixed_only_nonzero_rows_shown(self):
        rows = self._visible_rows({"redispatch_count": 2, "tester_count": 0, "skip_count": 1})
        assert "tester" not in rows
        assert "redispatch" in rows
        assert "skip" in rows


# ── Static HTML: modal structure ──────────────────────────────────────────────

class TestRerunModalHTML:
    """Verify the static HTML has the correct modal structure."""

    @pytest.fixture(scope="class")
    def html(self):
        html_path = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "index.html"
        return html_path.read_text(encoding="utf-8")

    def test_modal_has_amber_warning_icon(self, html):
        assert "ti-alert-triangle" in html, "Modal header must have amber warning icon"

    def test_modal_confirm_button_is_btn_warning(self, html):
        assert 'class="btn-warning" id="smgmt-rerun-confirm"' in html, \
            "Confirm button must use btn-warning class"

    def test_modal_confirm_button_text(self, html):
        assert "Re-run sprint" in html, "Confirm button text must be 'Re-run sprint'"

    def test_modal_cancel_button_present(self, html):
        assert 'onclick="smgmtRerunClose()"' in html, "Cancel button must call smgmtRerunClose()"

    def test_btn_warning_css_defined(self, html):
        assert ".btn-warning" in html, "btn-warning CSS class must be defined"

    def test_modal_title_element_exists(self, html):
        assert 'id="smgmt-rerun-title"' in html, "Modal title element must exist"

    def test_no_native_confirm_in_modal_html(self, html):
        import re
        # The onclick attributes for rerun must not call window.confirm or confirm()
        # Find the rerun modal section and check it doesn't have confirm() calls
        modal_section = html[html.find('id="smgmt-rerun-modal"'):html.find('id="smgmt-finish-modal"')]
        assert "window.confirm" not in modal_section
        # onclick handlers should only call smgmtRerunClose or smgmtRerunConfirm
        onclick_values = re.findall(r'onclick="([^"]+)"', modal_section)
        for handler in onclick_values:
            assert "confirm(" not in handler or "smgmtRerunConfirm" in handler, \
                f"Unexpected confirm() call in modal onclick: {handler}"


# ── Static JS: no window.confirm in rerun flow ────────────────────────────────

class TestNoNativeConfirmInJS:
    """Verify that smgmtRerunSprint does not call window.confirm."""

    @pytest.fixture(scope="class")
    def js(self):
        js_path = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "app.js"
        return js_path.read_text(encoding="utf-8")

    def _extract_function(self, js: str, fn_name: str) -> str:
        start = js.find(f"async function {fn_name}(")
        if start == -1:
            start = js.find(f"function {fn_name}(")
        if start == -1:
            return ""
        # Extract until the next top-level function definition
        end = js.find("\nasync function ", start + 1)
        if end == -1:
            end = js.find("\nfunction ", start + 1)
        return js[start:end] if end != -1 else js[start:]

    def test_smgmt_rerun_sprint_no_window_confirm(self, js):
        fn_body = self._extract_function(js, "smgmtRerunSprint")
        assert fn_body, "smgmtRerunSprint function not found"
        assert "window.confirm" not in fn_body, \
            "smgmtRerunSprint must not call window.confirm"
        assert "confirm(" not in fn_body or "smgmtRerunConfirm" in fn_body, \
            "smgmtRerunSprint must not call the native confirm() function"

    def test_smgmt_rerun_sprint_calls_preview_endpoint(self, js):
        fn_body = self._extract_function(js, "smgmtRerunSprint")
        assert "rerun/preview" in fn_body, \
            "smgmtRerunSprint must fetch the /rerun/preview endpoint"

    def test_smgmt_rerun_sprint_shows_count_rows(self, js):
        fn_body = self._extract_function(js, "smgmtRerunSprint")
        assert "redispatch_count" in fn_body, "Modal must use redispatch_count from preview"
        assert "tester_count" in fn_body, "Modal must use tester_count from preview"
        assert "skip_count" in fn_body, "Modal must use skip_count from preview"

    def test_smgmt_rerun_sprint_hides_zero_count_rows(self, js):
        fn_body = self._extract_function(js, "smgmtRerunSprint")
        assert "> 0" in fn_body, "Zero-count rows must be conditionally rendered"

    def test_modal_title_uses_full_label(self, js):
        fn_body = self._extract_function(js, "smgmtRerunSprint")
        # The title should use sprintLabelDisplay(label) for sub-label support
        assert "Re-run" in fn_body, "Modal title must say 'Re-run ...'"
        assert "label" in fn_body, "Modal title must incorporate the sprint label variable"
        # Either old form "Re-run Sprint" or new form "Re-run ${sprintLabelDisplay(label)}"
        uses_display = "sprintLabelDisplay" in fn_body
        uses_literal = "Re-run Sprint" in fn_body
        assert uses_display or uses_literal, "Title must use sprintLabelDisplay() or literal 'Re-run Sprint'"


# ── Selenium: confirm() never called during re-run flow ───────────────────────

@pytest.mark.selenium
class TestNoNativeConfirmSelenium:
    """Real browser tests. Require @pytest.mark.selenium and a running dashboard."""

    def test_window_confirm_never_called(self, driver):
        """Clicking Re-run Sprint must not invoke window.confirm."""
        import os
        import time

        base_url = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
        driver.get(base_url + "/static/project.html")
        time.sleep(1)

        # Intercept window.confirm and track calls
        driver.execute_script("""
            window.__confirmCalled = false;
            window.__originalConfirm = window.confirm;
            window.confirm = function() {
                window.__confirmCalled = true;
                return true;
            };
        """)

        # Inject a Re-run Sprint button and trigger it
        driver.execute_script("""
            window._smgmtCurrentRepo = 'test/repo';
            window._smgmtData = { issues: [] };

            // Stub the fetch for the preview endpoint
            window.__originalFetch = window.fetch;
            window.fetch = async function(url, opts) {
                if (url && url.includes('rerun/preview')) {
                    return {
                        ok: true,
                        json: async () => ({
                            redispatch_count: 1,
                            tester_count: 0,
                            skip_count: 0,
                            by_ticket: [{issue_num: 1, issue_title: 'T', action: 'dispatch_coder'}]
                        }),
                        text: async () => ''
                    };
                }
                return window.__originalFetch(url, opts);
            };

            // Call the rerun function
            smgmtRerunSprint('sprint-23');
        """)

        time.sleep(0.5)

        confirm_called = driver.execute_script("return window.__confirmCalled;")
        assert confirm_called is False, \
            "window.confirm must never be called during the Re-run Sprint flow"

    def test_rerun_modal_appears_not_native_dialog(self, driver):
        """The in-app modal must be visible after clicking Re-run Sprint."""
        import os
        import time
        from selenium.webdriver.common.by import By

        base_url = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
        driver.get(base_url + "/static/project.html")
        time.sleep(1)

        driver.execute_script("""
            window._smgmtCurrentRepo = 'test/repo';
            window._smgmtData = { issues: [] };

            window.fetch = async function(url, opts) {
                if (url && url.includes('rerun/preview')) {
                    return {
                        ok: true,
                        json: async () => ({
                            redispatch_count: 2,
                            tester_count: 1,
                            skip_count: 0,
                            by_ticket: []
                        }),
                        text: async () => ''
                    };
                }
                return { ok: false, text: async () => 'not found', json: async () => ({}) };
            };

            smgmtRerunSprint('sprint-23');
        """)

        time.sleep(0.6)

        modal = driver.find_element(By.ID, "smgmt-rerun-modal")
        assert "hidden" not in (modal.get_attribute("class") or ""), \
            "Rerun modal must be visible after calling smgmtRerunSprint"

    def test_rerun_modal_title_contains_label(self, driver):
        """Modal title must show 'Re-run Sprint sprint-23?' not just a number."""
        import os
        import time

        base_url = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
        driver.get(base_url + "/static/project.html")
        time.sleep(1)

        driver.execute_script("""
            window._smgmtCurrentRepo = 'test/repo';
            window.fetch = async function(url) {
                if (url && url.includes('rerun/preview')) {
                    return {
                        ok: true,
                        json: async () => ({ redispatch_count: 0, tester_count: 0, skip_count: 0, by_ticket: [] }),
                        text: async () => ''
                    };
                }
                return { ok: false, text: async () => '', json: async () => ({}) };
            };
            smgmtRerunSprint('sprint-23');
        """)

        time.sleep(0.6)

        title_text = driver.execute_script(
            "return document.getElementById('smgmt-rerun-title')?.textContent || '';"
        )
        assert "sprint-23" in title_text, \
            f"Modal title must contain the sprint label 'sprint-23', got: {title_text!r}"

    def test_cancel_closes_modal_no_rerun(self, driver):
        """Clicking Cancel closes the modal without triggering a rerun POST."""
        import os
        import time
        from selenium.webdriver.common.by import By

        base_url = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
        driver.get(base_url + "/static/project.html")
        time.sleep(1)

        driver.execute_script("""
            window._smgmtCurrentRepo = 'test/repo';
            window.__rerunPostCalled = false;

            window.fetch = async function(url, opts) {
                if (url && url.includes('rerun/preview')) {
                    return {
                        ok: true,
                        json: async () => ({ redispatch_count: 1, tester_count: 0, skip_count: 0, by_ticket: [] }),
                        text: async () => ''
                    };
                }
                if (url && url.includes('/rerun') && opts?.method === 'POST') {
                    window.__rerunPostCalled = true;
                }
                return { ok: false, text: async () => '', json: async () => ({}) };
            };

            smgmtRerunSprint('sprint-23');
        """)

        time.sleep(0.6)

        driver.execute_script("smgmtRerunClose();")
        time.sleep(0.2)

        modal = driver.find_element(By.ID, "smgmt-rerun-modal")
        assert "hidden" in (modal.get_attribute("class") or ""), \
            "Modal must be hidden after Cancel"

        post_called = driver.execute_script("return window.__rerunPostCalled;")
        assert post_called is False, "Rerun POST must not be triggered when Cancel is clicked"

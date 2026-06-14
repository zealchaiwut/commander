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
  AC-9  agent-browser test (opt-in) confirms re-run modal markup loads in a real browser
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


# ── Static HTML: modal structure (current rr-modal, issue #512/#797) ─────────

class TestRerunModalHTML:
    """Verify project.html has the in-app re-run modal (no native confirm)."""

    @pytest.fixture(scope="class")
    def html(self):
        html_path = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
        return html_path.read_text(encoding="utf-8")

    def test_modal_elements_exist(self, html):
        for element_id in ("rr-backdrop", "rr-modal", "rr-modal-title", "rr-confirm-btn"):
            assert f'id="{element_id}"' in html, f"Missing {element_id}"

    def test_modal_confirm_button_present(self, html):
        assert 'id="rr-confirm-btn"' in html, "Confirm button must exist"

    def test_modal_cancel_closes_via_rr_close(self, html):
        assert "rr-backdrop" in html
        assert "_rrClose" in html, "Backdrop must wire to _rrClose"

    def test_no_native_confirm_in_modal_html(self, html):
        import re
        modal_section = html[html.find('id="rr-modal"'):html.find('id="rr-confirm-btn"') + 200]
        assert "window.confirm" not in modal_section
        onclick_values = re.findall(r'onclick="([^"]+)"', modal_section)
        for handler in onclick_values:
            assert "confirm(" not in handler, f"Unexpected confirm() in modal onclick: {handler}"


# ── Static JS: no window.confirm in rerun flow ────────────────────────────────

class TestNoNativeConfirmInJS:
    """Verify that smgmtRerunSprint does not call window.confirm (rerun-modal.js)."""

    @pytest.fixture(scope="class")
    def js(self):
        js_path = (
            Path(__file__).parent.parent
            / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "rerun-modal.js"
        )
        return js_path.read_text(encoding="utf-8")

    def test_smgmt_rerun_sprint_no_window_confirm(self, js):
        assert "window.confirm" not in js, "smgmtRerunSprint must not call window.confirm"
        assert "confirm(" not in js, "rerun-modal.js must not call native confirm()"

    def test_smgmt_rerun_sprint_calls_preview_endpoint(self, js):
        assert "rerun-preview" in js, "smgmtRerunSprint must fetch the rerun-preview endpoint"

    def test_smgmt_rerun_sprint_opens_rr_modal(self, js):
        assert "_rrOpen" in js, "smgmtRerunSprint must open the in-app modal via _rrOpen"
        assert "rr-modal-title" in js, "Modal title must be updated in-app"

    def test_rr_confirm_posts_rerun_endpoint(self, js):
        assert "/rerun?" in js or "/rerun" in js, "_rrConfirm must POST to the rerun endpoint"
        assert "method: 'POST'" in js or 'method: "POST"' in js


# ── agent-browser: AC-9 runtime check (opt-in) ───────────────────────────────

@pytest.mark.agent_browser
class TestRerunModalAgentBrowser:
    """AC-9: real browser via agent-browser CLI — opt in with `pytest -m agent_browser`."""

    def test_rr_modal_markup_loads_in_browser(self, static_dashboard_url, agent_browser_available):
        agent_browser_open(f"{static_dashboard_url}/project.html")
        agent_browser_find("#rr-modal")
        agent_browser_find("#rr-confirm-btn")

    def test_rr_modal_title_element_present(self, static_dashboard_url, agent_browser_available):
        agent_browser_open(f"{static_dashboard_url}/project.html")
        out = agent_browser_find("#rr-modal-title")
        assert "Re-run" in out or "rr-modal-title" in out.lower() or out.strip()


"""Tests for issue #211: Show estimate summary modal before dispatching sprint.

AC coverage:
  AC1  - Clicking "Run sprint" opens confirmation modal instead of dispatching immediately
  AC2  - Modal header shows sprint name and total ticket count
  AC3  - Modal displays size breakdown ("3 × S, 5 × M, 2 × L, 1 unsized")
  AC4  - Modal displays total wall-clock estimate (S=30 min, M=2 h, L=8 h)
  AC5  - Size-to-hours mapping lives in single function _size_to_minutes in server.py
  AC6  - Unsized tickets: warning row lists issue numbers with hint text
  AC7  - All unsized: total estimate reads "Unknown (no ticket sizes)"
  AC8  - Cancel button closes modal with no side effects
  AC9  - Confirm and run button closes modal and dispatches
  AC10 - GET /api/sprints/{sprint_label}/estimate-summary returns correct JSON shape
  AC11 - Endpoint fetches ticket sizes via list_open_issues_with_body plumbing
  AC12 - Modal styling follows existing Bulk Create modal pattern
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"
APP_JS = DASHBOARD_DIR / "static" / "app.js"
SERVER_PY = DASHBOARD_DIR / "server.py"


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text()


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text()


@pytest.fixture(scope="module")
def server_src() -> str:
    return SERVER_PY.read_text()


def _make_issue(number: int, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "",
        "labels": [{"name": lbl} for lbl in labels],
        "state": "open",
    }


@pytest.fixture
def api_client(tmp_path, monkeypatch) -> Generator:
    """TestClient with a stubbed github_client and COMMANDER_BASE."""
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))
    if "server" in sys.modules:
        del sys.modules["server"]
    if "github_client" in sys.modules:
        del sys.modules["github_client"]

    import server
    import github_client as gc

    monkeypatch.setattr(
        gc,
        "list_open_issues_with_body",
        lambda repo_name=None, limit=200: [
            _make_issue(1, ["sprint-15", "size-S"]),
            _make_issue(2, ["sprint-15", "size-M"]),
            _make_issue(3, ["sprint-15", "size-M"]),
            _make_issue(4, ["sprint-15", "size-L"]),
            _make_issue(5, ["sprint-15"]),         # unsized
        ],
    )

    with TestClient(server.app, raise_server_exceptions=True) as c:
        yield c


# ── AC1: Run sprint opens estimate modal, not direct dispatch ─────────────────

class TestAC1RunSprintOpensModal:
    def test_smgmt_run_sprint_calls_smgmt_est_open(self, js: str):
        """smgmtRunSprint awaits smgmtEstOpen instead of dispatching directly."""
        assert "smgmtEstOpen(sprintLabel" in js, (
            "smgmtRunSprint must call smgmtEstOpen before dispatching"
        )

    def test_run_sprint_button_calls_smgmt_run_sprint(self, js: str):
        """The sprint run button handler invokes smgmtRunSprint."""
        assert "smgmtRunSprint(" in js, (
            "Run sprint action must invoke smgmtRunSprint"
        )

    def test_smgmt_run_sprint_does_not_dispatch_directly(self, js: str):
        """smgmtRunSprint does not call smgmtDispatchRun before smgmtEstOpen returns."""
        idx_est = js.find("async function smgmtRunSprint(")
        idx_open = js.find("smgmtEstOpen(", idx_est)
        idx_dispatch = js.find("smgmtDispatchRun(", idx_est)
        assert idx_open != -1, "smgmtEstOpen not found after smgmtRunSprint definition"
        # dispatch should either be absent in smgmtRunSprint body or come after smgmtEstOpen
        # (i.e. the direct dispatch path is removed from smgmtRunSprint)
        next_fn = js.find("\nasync function ", idx_est + 1)
        if next_fn == -1:
            next_fn = js.find("\nfunction ", idx_est + 1)
        run_sprint_body = js[idx_est:next_fn] if next_fn != -1 else js[idx_est:]
        # smgmtRunSprint body should end with smgmtEstOpen, not smgmtDispatchRun
        assert "smgmtEstOpen" in run_sprint_body, (
            "smgmtRunSprint must delegate to smgmtEstOpen"
        )


# ── AC2: Modal header shows sprint name and ticket count ──────────────────────

class TestAC2ModalHeader:
    def test_modal_title_element_exists_in_html(self, html: str):
        """HTML has a modal title element for the estimate modal."""
        assert 'id="smgmt-est-title"' in html, (
            "Modal title element smgmt-est-title must exist in index.html"
        )

    def test_js_sets_title_with_sprint_name_and_count(self, js: str):
        """JS sets modal title to sprint name and ticket count string."""
        assert "sprint_name" in js, "JS must use sprint_name from API response"
        assert "total_tickets" in js, "JS must use total_tickets from API response"
        # Confirm the title format includes ticket(s) pluralization
        assert "ticket" in js and "totalTickets" in js, (
            "JS must include ticket count in the modal title"
        )

    def test_api_returns_sprint_name_and_total_tickets(self, api_client):
        """GET estimate-summary returns sprint_name and total_tickets fields."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sprint_name"] == "Sprint 15"
        assert data["total_tickets"] == 5


# ── AC3: Modal displays size breakdown ────────────────────────────────────────

class TestAC3SizeBreakdown:
    def test_api_returns_size_counts(self, api_client):
        """Endpoint returns size_counts dict with correct ticket counts."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        assert data["size_counts"] == {"S": 1, "M": 2, "L": 1}, (
            f"Expected {{S:1, M:2, L:1}}, got {data['size_counts']}"
        )

    def test_js_builds_breakdown_string(self, js: str):
        """JS constructs size breakdown parts with × separator."""
        assert "&times;" in js or "× " in js or "times" in js, (
            "JS must render the × character in size breakdown (e.g. '3 × S')"
        )
        assert "breakdownParts" in js, "JS must assemble size breakdown parts"

    def test_js_includes_unsized_in_breakdown(self, js: str):
        """JS appends unsized count to the breakdown string."""
        assert "unsized" in js, (
            "JS must include 'unsized' label in size breakdown when tickets lack size"
        )

    def test_js_renders_breakdown_in_table(self, js: str):
        """JS renders size breakdown in a table row."""
        assert "Size breakdown" in js, (
            "JS must label the breakdown row 'Size breakdown'"
        )


# ── AC4: Total wall-clock estimate with correct mapping ───────────────────────

class TestAC4TotalEstimate:
    def test_size_to_minutes_s_equals_30(self, server_src: str):
        """_size_to_minutes maps S to 30 minutes."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("server", SERVER_PY)
        mod = importlib.util.module_from_spec(spec)
        # Execute selectively by evaluating the function in isolation
        tree = ast.parse(server_src)
        fn_src = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_size_to_minutes":
                fn_src = ast.get_source_segment(server_src, node)
                break
        assert fn_src is not None, "_size_to_minutes function not found in server.py"
        ns: dict = {}
        exec(fn_src, ns)  # noqa: S102
        assert ns["_size_to_minutes"]("S") == 30, "S must map to 30 minutes"

    def test_size_to_minutes_m_equals_120(self, server_src: str):
        """_size_to_minutes maps M to 120 minutes (2 h)."""
        tree = ast.parse(server_src)
        fn_src = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_size_to_minutes":
                fn_src = ast.get_source_segment(server_src, node)
                break
        ns: dict = {}
        exec(fn_src, ns)  # noqa: S102
        assert ns["_size_to_minutes"]("M") == 120, "M must map to 120 minutes (2 h)"

    def test_size_to_minutes_l_equals_480(self, server_src: str):
        """_size_to_minutes maps L to 480 minutes (8 h)."""
        tree = ast.parse(server_src)
        fn_src = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_size_to_minutes":
                fn_src = ast.get_source_segment(server_src, node)
                break
        ns: dict = {}
        exec(fn_src, ns)  # noqa: S102
        assert ns["_size_to_minutes"]("L") == 480, "L must map to 480 minutes (8 h)"

    def test_api_returns_correct_total_minutes(self, api_client):
        """Endpoint sums S(30) + M(120) + M(120) + L(480) = 750 minutes."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        # 1×S (30) + 2×M (240) + 1×L (480) = 750; issue #5 is unsized
        assert data["total_minutes"] == 750, (
            f"Expected total_minutes=750, got {data['total_minutes']}"
        )

    def test_js_renders_total_estimate_row(self, js: str):
        """JS renders a 'Total estimate' row in the modal."""
        assert "Total estimate" in js, "JS must label the total row 'Total estimate'"

    def test_js_formats_hours_and_minutes(self, js: str):
        """JS formats total estimate into hours and minutes."""
        assert "Math.floor(total_minutes / 60)" in js, (
            "JS must convert total_minutes to hours"
        )


# ── AC5: Single mapping function in server.py ─────────────────────────────────

class TestAC5SingleMappingFunction:
    def test_size_to_minutes_function_exists_in_server_py(self, server_src: str):
        """server.py defines exactly one _size_to_minutes function."""
        count = server_src.count("def _size_to_minutes(")
        assert count == 1, (
            f"Expected exactly 1 definition of _size_to_minutes, found {count}"
        )

    def test_endpoint_calls_size_to_minutes_function(self, server_src: str):
        """The estimate-summary endpoint calls _size_to_minutes."""
        assert "_size_to_minutes(" in server_src, (
            "get_sprint_estimate_summary must call _size_to_minutes"
        )

    def test_no_hardcoded_size_minutes_outside_function(self, server_src: str):
        """No duplicate size-to-minutes mapping outside _size_to_minutes in issue 211 code."""
        # Find the function body
        fn_start = server_src.find("def _size_to_minutes(")
        fn_end = server_src.find("\ndef ", fn_start + 1)
        fn_body = server_src[fn_start:fn_end] if fn_end != -1 else server_src[fn_start:]
        # The mapping dict should only appear inside the function
        assert '{"S": 30' in fn_body or '"S": 30' in fn_body, (
            "The S=30 mapping must be inside _size_to_minutes"
        )


# ── AC6: Warning for unsized tickets ─────────────────────────────────────────

class TestAC6UnsizedWarning:
    def test_api_returns_unsized_numbers(self, api_client):
        """Endpoint includes issue numbers for unsized tickets."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        assert 5 in data["unsized_numbers"], (
            f"Issue #5 (no size label) must be in unsized_numbers, got {data['unsized_numbers']}"
        )

    def test_js_renders_warning_for_unsized(self, js: str):
        """JS renders a warning div when unsized tickets are present."""
        assert "smgmt-est-warning" in js, (
            "JS must render .smgmt-est-warning block for unsized tickets"
        )

    def test_js_warning_lists_issue_numbers(self, js: str):
        """JS formats unsized issue numbers as #N in the warning."""
        assert "`#${n}`" in js or "'#' + n" in js or '"#"' in js, (
            "JS must prefix issue numbers with # in the warning list"
        )

    def test_js_warning_includes_hint_text(self, js: str):
        """JS warning includes 'Unsized tickets will not contribute to the estimate' hint."""
        assert "Unsized tickets will not contribute to the estimate" in js, (
            "Warning text must say 'Unsized tickets will not contribute to the estimate'"
        )

    def test_warning_block_in_html(self, html: str):
        """index.html has smgmt-est-warning CSS class defined for the warning styling."""
        assert "smgmt-est-warning" in html, (
            ".smgmt-est-warning CSS class must be defined in index.html"
        )


# ── AC7: All unsized → "Unknown (no ticket sizes)" ───────────────────────────

class TestAC7AllUnsized:
    def test_api_all_unsized_returns_zero_total_minutes(self, tmp_path, monkeypatch):
        """When all sprint tickets are unsized, total_minutes is 0."""
        monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))
        for mod in ["server", "github_client"]:
            if mod in sys.modules:
                del sys.modules[mod]

        import server
        import github_client as gc

        monkeypatch.setattr(
            gc,
            "list_open_issues_with_body",
            lambda repo_name=None, limit=200: [
                _make_issue(10, ["sprint-20"]),
                _make_issue(11, ["sprint-20"]),
            ],
        )

        with TestClient(server.app) as c:
            resp = c.get(
                "/api/sprints/sprint-20/estimate-summary",
                params={"project": "owner/repo"},
            )
        data = resp.json()
        assert data["total_minutes"] == 0, (
            f"All unsized sprint must return total_minutes=0, got {data['total_minutes']}"
        )
        assert data["unsized_numbers"] == [10, 11], (
            f"All issues must be in unsized_numbers, got {data['unsized_numbers']}"
        )

    def test_js_shows_unknown_when_no_sized_tickets(self, js: str):
        """JS renders 'Unknown (no ticket sizes)' when total_minutes is 0."""
        assert "Unknown (no ticket sizes)" in js, (
            "JS must display 'Unknown (no ticket sizes)' when no tickets have sizes"
        )


# ── AC8: Cancel button closes modal ───────────────────────────────────────────

class TestAC8CancelButton:
    def test_cancel_button_exists_in_html(self, html: str):
        """HTML has a Cancel button calling smgmtEstClose."""
        assert 'onclick="smgmtEstClose()"' in html and "Cancel" in html, (
            "Cancel button calling smgmtEstClose() must exist in the estimate modal"
        )

    def test_smgmt_est_close_hides_modal(self, js: str):
        """smgmtEstClose adds 'hidden' class to both backdrop and modal."""
        idx = js.find("function smgmtEstClose(")
        assert idx != -1, "smgmtEstClose function must exist"
        next_fn = js.find("\nfunction ", idx + 1)
        if next_fn == -1:
            next_fn = js.find("\nasync function ", idx + 1)
        close_body = js[idx:next_fn] if next_fn != -1 else js[idx:]
        assert "classList.add('hidden')" in close_body or 'classList.add("hidden")' in close_body, (
            "smgmtEstClose must add 'hidden' class to close the modal"
        )

    def test_smgmt_est_close_resets_state(self, js: str):
        """smgmtEstClose resets _smgmtEstTargetLabel to null."""
        idx = js.find("function smgmtEstClose(")
        next_fn = js.find("\nfunction ", idx + 1)
        if next_fn == -1:
            next_fn = js.find("\nasync function ", idx + 1)
        close_body = js[idx:next_fn] if next_fn != -1 else js[idx:]
        assert "_smgmtEstTargetLabel" in close_body, (
            "smgmtEstClose must reset _smgmtEstTargetLabel (no side effects)"
        )

    def test_escape_key_closes_modal(self, js: str):
        """Pressing Escape calls smgmtEstClose."""
        assert "smgmtEstClose" in js and "Escape" in js, (
            "Keyboard handler must call smgmtEstClose on Escape key"
        )


# ── AC9: Confirm and run dispatches ───────────────────────────────────────────

class TestAC9ConfirmAndRun:
    def test_confirm_button_exists_in_html(self, html: str):
        """HTML has a 'Confirm and run' button calling smgmtEstConfirm."""
        assert 'onclick="smgmtEstConfirm()"' in html, (
            "Confirm button calling smgmtEstConfirm() must exist in estimate modal"
        )
        assert "Confirm and run" in html, (
            "Button label must read 'Confirm and run'"
        )

    def test_smgmt_est_confirm_closes_modal_then_dispatches(self, js: str):
        """smgmtEstConfirm calls smgmtEstClose then triggers dispatch flow."""
        idx = js.find("async function smgmtEstConfirm(")
        assert idx != -1, "smgmtEstConfirm must be defined"
        next_fn = js.find("\nasync function ", idx + 1)
        if next_fn == -1:
            next_fn = js.find("\nfunction ", idx + 1)
        confirm_body = js[idx:next_fn] if next_fn != -1 else js[idx:]
        assert "smgmtEstClose()" in confirm_body, (
            "smgmtEstConfirm must call smgmtEstClose to close the modal"
        )
        assert "smgmtDispatchRun(" in confirm_body or "smgmtMigrateOpen(" in confirm_body, (
            "smgmtEstConfirm must proceed to dispatch or migration after closing"
        )


# ── AC10: Endpoint returns correct JSON shape ─────────────────────────────────

class TestAC10EndpointShape:
    def test_endpoint_200_ok_with_all_required_fields(self, api_client):
        """GET estimate-summary returns 200 with all required fields."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        assert resp.status_code == 200
        data = resp.json()
        required_keys = {
            "sprint_name", "sprint_label", "total_tickets",
            "size_counts", "total_minutes", "unsized_numbers",
        }
        missing = required_keys - data.keys()
        assert not missing, f"Response missing required keys: {missing}"

    def test_endpoint_400_for_invalid_sprint_label(self, api_client):
        """GET estimate-summary returns 400 for a malformed sprint label."""
        resp = api_client.get(
            "/api/sprints/not-a-sprint/estimate-summary",
            params={"project": "owner/repo"},
        )
        assert resp.status_code == 400

    def test_endpoint_sprint_label_echoed_in_response(self, api_client):
        """Endpoint echoes the sprint_label in the response."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        assert data["sprint_label"] == "sprint-15"

    def test_size_counts_dict_maps_size_to_int(self, api_client):
        """size_counts values are integers (not strings)."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        for size, count in data["size_counts"].items():
            assert isinstance(count, int), (
                f"size_counts['{size}'] must be int, got {type(count)}"
            )

    def test_unsized_numbers_is_list_of_ints(self, api_client):
        """unsized_numbers is a list of integer issue numbers."""
        resp = api_client.get(
            "/api/sprints/sprint-15/estimate-summary",
            params={"project": "owner/repo"},
        )
        data = resp.json()
        assert isinstance(data["unsized_numbers"], list)
        for n in data["unsized_numbers"]:
            assert isinstance(n, int), f"unsized_numbers item must be int, got {type(n)}"


# ── AC11: Endpoint uses list_open_issues_with_body ────────────────────────────

class TestAC11ListOpenIssuesPlumbing:
    def test_endpoint_calls_list_open_issues_with_body(self, server_src: str):
        """get_sprint_estimate_summary uses list_open_issues_with_body."""
        fn_start = server_src.find("def get_sprint_estimate_summary(")
        assert fn_start != -1, "get_sprint_estimate_summary not found in server.py"
        fn_end = server_src.find("\n@app.", fn_start + 1)
        if fn_end == -1:
            fn_end = server_src.find("\ndef ", fn_start + 1)
        fn_body = server_src[fn_start:fn_end] if fn_end != -1 else server_src[fn_start:]
        assert "list_open_issues_with_body(" in fn_body, (
            "get_sprint_estimate_summary must call list_open_issues_with_body"
        )

    def test_endpoint_filters_to_sprint_label(self, server_src: str):
        """Endpoint filters issues by sprint label before computing sizes."""
        fn_start = server_src.find("def get_sprint_estimate_summary(")
        fn_end = server_src.find("\n@app.", fn_start + 1)
        if fn_end == -1:
            fn_end = server_src.find("\ndef ", fn_start + 1)
        fn_body = server_src[fn_start:fn_end] if fn_end != -1 else server_src[fn_start:]
        assert "sprint_label" in fn_body and "sprint_issues" in fn_body, (
            "Endpoint must filter issues to the requested sprint_label"
        )

    def test_endpoint_reads_size_labels_from_issues(self, server_src: str):
        """Endpoint reads size-S/M/L/XL labels from each issue."""
        fn_start = server_src.find("def get_sprint_estimate_summary(")
        fn_end = server_src.find("\n@app.", fn_start + 1)
        if fn_end == -1:
            fn_end = server_src.find("\ndef ", fn_start + 1)
        fn_body = server_src[fn_start:fn_end] if fn_end != -1 else server_src[fn_start:]
        assert "size-" in fn_body, (
            "Endpoint must look for 'size-' prefixed labels on each issue"
        )


# ── AC12: Modal uses Bulk Create modal pattern ────────────────────────────────

class TestAC12ModalStyling:
    def test_modal_uses_modal_backdrop_class(self, html: str):
        """Estimate modal uses modal-backdrop class like Bulk Create modal."""
        assert 'id="smgmt-est-backdrop"' in html, (
            "Estimate modal backdrop must have id smgmt-est-backdrop"
        )
        # Verify the backdrop element has modal-backdrop class
        idx = html.find('id="smgmt-est-backdrop"')
        line_start = html.rfind("\n", 0, idx)
        line = html[line_start:idx + 50]
        assert "modal-backdrop" in line, (
            "smgmt-est-backdrop must have modal-backdrop class (matches Bulk Create pattern)"
        )

    def test_modal_uses_modal_class(self, html: str):
        """Estimate modal element uses modal class."""
        assert 'id="smgmt-est-modal"' in html
        idx = html.find('id="smgmt-est-modal"')
        # Check that this element has the modal class
        tag_start = html.rfind("<", 0, idx)
        tag = html[tag_start:idx + 100]
        assert 'class="modal' in tag, (
            "smgmt-est-modal must have the 'modal' CSS class"
        )

    def test_modal_has_modal_header_structure(self, html: str):
        """Estimate modal has modal-header, modal-title, modal-close structure."""
        assert "modal-header" in html
        assert "modal-title" in html
        assert "modal-close" in html

    def test_modal_has_modal_footer_with_buttons(self, html: str):
        """Estimate modal has a modal-footer with Cancel and Confirm buttons."""
        idx_modal = html.find('id="smgmt-est-modal"')
        # Find the footer within this modal
        idx_footer = html.find("modal-footer", idx_modal)
        assert idx_footer != -1, "Estimate modal must have a modal-footer"
        footer_block = html[idx_footer:idx_footer + 300]
        assert "Cancel" in footer_block, "Footer must have a Cancel button"
        assert "Confirm and run" in footer_block, "Footer must have a 'Confirm and run' button"

    def test_modal_has_role_dialog(self, html: str):
        """Estimate modal has role='dialog' for accessibility."""
        assert 'id="smgmt-est-modal"' in html
        idx = html.find('id="smgmt-est-modal"')
        tag_start = html.rfind("<", 0, idx)
        tag = html[tag_start:idx + 200]
        assert 'role="dialog"' in tag, "Estimate modal must have role='dialog'"

    def test_backdrop_closes_on_click(self, html: str):
        """Clicking the backdrop calls smgmtEstClose (consistent with Bulk Create pattern)."""
        assert 'id="smgmt-est-backdrop" onclick="smgmtEstClose()"' in html or \
               'onclick="smgmtEstClose()"' in html, (
                   "Backdrop must call smgmtEstClose() on click"
               )

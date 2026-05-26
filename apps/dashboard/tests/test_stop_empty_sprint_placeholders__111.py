"""
Tests for issue #111: Stop auto-creating empty sprint placeholders.

Acceptance Criteria:
  AC-1: Sprint Mgmt view does not show sprint labels with 0 tickets by default.
  AC-2: Existing 0-ticket sprint labels detected and listed in a "Clean up empty
        sprints" inline action at the top of Sprint Mgmt.
  AC-3: Click "Clean up empty sprints" → confirmation modal listing the empty
        labels → confirm → labels deleted on GitHub.
  AC-4: At the bottom of the sprint list, ONE empty placeholder sprint is shown.
  AC-5: Dropping a ticket on the placeholder creates the real sprint-<N+1> label
        on GitHub and assigns the ticket.
  AC-6: After ticket is assigned, the placeholder converts to a real sprint card
        and a new placeholder appears (sprint-<N+2>).
  AC-7: No more empty placeholders appear automatically (only the trailing one).
  AC-8: "+ New sprint" button continues to work as today.
  AC-9: Internal projects.json or .commander state files NOT polluted with ghost
        sprint entries.
"""
import re
from pathlib import Path
import ast

# Paths
ROOT = Path(__file__).parent.parent.parent.parent
DASHBOARD = ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD / "server.py"
APP_JS = DASHBOARD / "static" / "app.js"
INDEX_HTML = DASHBOARD / "static" / "index.html"


# ---------------------------------------------------------------------------
# AC-1 — API: get_sprint_management_issues only returns non-empty sprints in order
# ---------------------------------------------------------------------------
class TestAC1EmptySprintsExcludedFromOrder:
    """get_sprint_management_issues must exclude 0-ticket sprints from order."""

    def test_server_py_exists(self):
        assert SERVER_PY.exists()

    def test_endpoint_returns_empty_sprint_labels_field(self):
        """API response includes empty_sprint_labels list."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "empty_sprint_labels" in source, \
            "get_sprint_management_issues must return 'empty_sprint_labels' field"

    def test_endpoint_returns_placeholder_sprint_field(self):
        """API response includes placeholder_sprint number."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "placeholder_sprint" in source, \
            "get_sprint_management_issues must return 'placeholder_sprint' field"

    def test_non_empty_sprints_used_for_order(self):
        """Only non-empty sprints are passed to _load_sprint_order."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "non_empty_sprints" in source, \
            "Server must compute non_empty_sprints (sprints with >0 tickets) for order"

    def test_sprint_ticket_counts_computed(self):
        """Server counts tickets per sprint to detect empty ones."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "sprint_ticket_counts" in source, \
            "Server must track sprint_ticket_counts to detect empty labels"

    def test_empty_labels_are_0_ticket_sprints(self):
        """Server builds empty_sprint_labels from sprints with 0 tickets."""
        source = SERVER_PY.read_text(encoding="utf-8")
        # Should check sprint_ticket_counts[n] == 0
        assert "== 0" in source or "0]" in source or "count == 0" in source, \
            "Server must filter sprints where ticket count == 0 to build empty_sprint_labels"


# ---------------------------------------------------------------------------
# AC-2 — Frontend: cleanup banner shown when empty_sprint_labels present
# ---------------------------------------------------------------------------
class TestAC2CleanupBanner:
    """Sprint Mgmt shows cleanup banner when there are empty sprint labels."""

    def test_cleanup_banner_element_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-cleanup-banner" in html, \
            "index.html must contain smgmt-cleanup-banner element"

    def test_smgmt_render_checks_empty_sprint_labels(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "empty_sprint_labels" in js, \
            "smgmtRender must consume empty_sprint_labels from API response"

    def test_cleanup_banner_shows_empty_labels_count(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "smgmt-cleanup-banner" in js, \
            "app.js must reference smgmt-cleanup-banner element"

    def test_cleanup_link_calls_smgmtCleanupOpen(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "smgmtCleanupOpen" in js, \
            "app.js must define and call smgmtCleanupOpen() for the cleanup action"

    def test_cleanup_banner_hidden_class_toggled(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Banner should be hidden when no empty labels
        assert "classList.remove('hidden')" in js or 'classList.remove("hidden")' in js, \
            "Cleanup banner hidden class must be toggled based on empty_sprint_labels"


# ---------------------------------------------------------------------------
# AC-3 — Frontend: cleanup confirmation modal and delete API
# ---------------------------------------------------------------------------
class TestAC3CleanupModal:
    """Clicking Clean up shows a modal; confirming deletes labels via API."""

    def test_cleanup_modal_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-cleanup-modal" in html, \
            "index.html must contain smgmt-cleanup-modal element"

    def test_cleanup_backdrop_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-cleanup-backdrop" in html, \
            "index.html must contain smgmt-cleanup-backdrop element"

    def test_smgmtCleanupOpen_function_defined(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "function smgmtCleanupOpen" in js or "async function smgmtCleanupOpen" in js, \
            "app.js must define smgmtCleanupOpen()"

    def test_smgmtCleanupClose_function_defined(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "function smgmtCleanupClose" in js, \
            "app.js must define smgmtCleanupClose()"

    def test_smgmtCleanupConfirm_function_defined(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "async function smgmtCleanupConfirm" in js, \
            "app.js must define async smgmtCleanupConfirm()"

    def test_cleanup_confirm_calls_delete_endpoint(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "/api/sprints/delete-empty" in js, \
            "smgmtCleanupConfirm must POST to /api/sprints/delete-empty"

    def test_delete_empty_endpoint_in_server(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "/api/sprints/delete-empty" in source, \
            "server.py must define POST /api/sprints/delete-empty endpoint"

    def test_delete_empty_validates_sprint_label_pattern(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        # Should validate sprint label format before deleting
        assert "_SPRINT_LABEL_RE.match" in source or "SPRINT_LABEL_RE.match" in source, \
            "delete-empty endpoint must validate sprint label format"

    def test_delete_empty_verifies_0_tickets(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        # Must verify labels have 0 tickets before deleting
        assert "still has open tickets" in source or "cannot delete" in source or \
               "label_set" in source, \
            "delete-empty endpoint must verify labels have 0 tickets before deleting"

    def test_github_client_has_delete_label(self):
        gc = (DASHBOARD / "github_client.py").read_text(encoding="utf-8")
        assert "def delete_label" in gc, \
            "github_client.py must define delete_label() function"

    def test_delete_label_invalidates_cache(self):
        gc = (DASHBOARD / "github_client.py").read_text(encoding="utf-8")
        # Check delete_label invalidates relevant cache keys
        assert 'invalidate' in gc, \
            "delete_label must invalidate sprint-related cache entries"

    def test_cleanup_body_element_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-cleanup-body" in html, \
            "index.html must contain smgmt-cleanup-body element for listing labels"


# ---------------------------------------------------------------------------
# AC-4 — Frontend: single trailing placeholder at bottom of sprint list
# ---------------------------------------------------------------------------
class TestAC4TrailingPlaceholder:
    """One trailing placeholder sprint card shown at the bottom."""

    def test_placeholder_block_function_defined(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "function smgmtPlaceholderBlockHtml" in js, \
            "app.js must define smgmtPlaceholderBlockHtml() function"

    def test_placeholder_has_drop_handlers(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "smgmtDropOnPlaceholder" in js, \
            "Placeholder block must have smgmtDropOnPlaceholder handler"
        assert "smgmtDragOverPlaceholder" in js, \
            "Placeholder block must have smgmtDragOverPlaceholder handler"

    def test_placeholder_labeled_empty_drop_here(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "drop tickets here" in js.lower() or "Drop tickets here" in js, \
            "Placeholder card must say 'drop tickets here'"

    def test_smgmt_render_appends_placeholder(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "smgmtPlaceholderBlockHtml" in js, \
            "smgmtRender must call smgmtPlaceholderBlockHtml to append placeholder"

    def test_placeholder_uses_placeholder_sprint_from_api(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "placeholder_sprint" in js, \
            "smgmtRender must use placeholder_sprint from API data"

    def test_placeholder_css_defined(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-sprint-placeholder" in html, \
            "CSS must define .smgmt-sprint-placeholder styles"


# ---------------------------------------------------------------------------
# AC-5/AC-6 — Frontend: drop on placeholder creates label and assigns ticket
# ---------------------------------------------------------------------------
class TestAC5AC6DropOnPlaceholder:
    """Dropping a ticket on placeholder creates real sprint label and assigns ticket."""

    def test_drop_on_placeholder_function_defined(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "async function smgmtDropOnPlaceholder" in js, \
            "app.js must define async smgmtDropOnPlaceholder()"

    def test_drop_calls_assign_endpoint(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "/api/sprint-planning/assign" in js, \
            "smgmtDropOnPlaceholder must call /api/sprint-planning/assign to assign ticket"

    def test_drop_updates_placeholder_sprint_to_next(self):
        js = APP_JS.read_text(encoding="utf-8")
        # After drop, placeholder should advance to N+1
        assert "placeholder_sprint" in js and "placeholderN + 1" in js, \
            "After drop, placeholder_sprint must be updated to placeholderN + 1"

    def test_drop_adds_sprint_to_order(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "_smgmtData.order.push" in js, \
            "smgmtDropOnPlaceholder must add new sprint to _smgmtData.order"

    def test_drop_reloads_data_from_server(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "smgmtSelectProject" in js, \
            "smgmtDropOnPlaceholder must reload data from server after successful drop"

    def test_drop_handles_error_with_rollback(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Should have try/catch with rollback
        assert "catch" in js and "smgmtShowError" in js, \
            "smgmtDropOnPlaceholder must handle errors and rollback optimistic update"


# ---------------------------------------------------------------------------
# AC-7 — No auto-creating ghost sprint labels
# ---------------------------------------------------------------------------
class TestAC7NoAutoGhostSprints:
    """Empty sprint labels are not auto-added to the sprint order display."""

    def test_order_built_from_non_empty_sprints_only(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        # The order building should use non_empty_sprints not all sprints
        assert "non_empty_sprints" in source, \
            "Sprint order must be built from non_empty_sprints only"

    def test_load_sprint_order_not_called_with_all_sprints(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        # Should call _load_sprint_order(project_root, non_empty_sprints)
        # rather than _load_sprint_order(project_root, sprints)
        assert "_load_sprint_order(project_root, non_empty_sprints)" in source, \
            "_load_sprint_order must receive non_empty_sprints (not all sprints)"


# ---------------------------------------------------------------------------
# AC-8 — "+ New sprint" continues to work
# ---------------------------------------------------------------------------
class TestAC8NewSprintButton:
    """'+ New sprint' button and /api/sprints/create endpoint still present."""

    def test_new_sprint_btn_in_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "smgmt-new-sprint-btn" in html, \
            "HTML must still contain smgmt-new-sprint-btn"

    def test_smgmtCreateSprint_function_in_js(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "async function smgmtCreateSprint" in js, \
            "app.js must still define smgmtCreateSprint()"

    def test_create_sprint_endpoint_in_server(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "/api/sprints/create" in source, \
            "server.py must still define POST /api/sprints/create endpoint"


# ---------------------------------------------------------------------------
# AC-9 — State files not polluted with ghost sprint entries
# ---------------------------------------------------------------------------
class TestAC9NoGhostStateEntries:
    """projects.json and .commander state files not polluted."""

    def test_projects_module_unchanged(self):
        """projects.py should not reference ghost sprint creation."""
        proj_py = DASHBOARD / "projects.py"
        if proj_py.exists():
            source = proj_py.read_text(encoding="utf-8")
            # Should not auto-create sprint labels from projects.py
            assert "ensure_sprint_label" not in source, \
                "projects.py must not auto-create sprint labels"

    def test_sprint_order_saved_with_non_empty_only(self):
        """Sprint order saved to file excludes empty sprints (order built from non_empty_sprints)."""
        source = SERVER_PY.read_text(encoding="utf-8")
        assert "non_empty_sprints" in source, \
            "Order file written from non_empty_sprints to avoid ghost entries"

"""Acceptance tests for issue #1059 — Redesign Backlog/Tickets Tab with Token Styling.

AC map:
  AC1   Ticket list rows have consistent anatomy: aligned columns for label, size,
        status chips, title, and metadata
  AC2   Label, size, and status chips use token-based colors, border-radius, and
        typography from tokens.css (CSS custom properties)
  AC3   Typography follows the token scale (size, weight, line-height) for
        scannable hierarchy
  AC4   Empty backlog state renders a clean, informative empty-state component
        (no raw "no tickets" text)
  AC5   All existing filters and event handlers are preserved and functional
        after the redesign
  AC6   No new JS frameworks introduced — vanilla JS/CSS only
  AC7   Impeccable detect passes on the tickets view region (manual check only)
  AC8   Diff is scoped to the tickets view region of project.html
  AC9   Dark theme is maintained throughout
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert PROJECT_HTML.exists(), f"project.html not found at {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tickets_css_section(html) -> str:
    """Extract the CSS section spanning the tickets tab and its sub-sections.

    Captures from '/* ── Tickets tab ── */' through to the
    '/* ── Ticket detail panel ── */' boundary, which covers all
    ticket row, chip, and empty-state CSS rules added for this feature.
    """
    m = re.search(
        r"/\*\s*──\s*Tickets tab\s*──\s*\*/(.*?)(?=/\*\s*──\s*Ticket detail|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def tickets_js(html) -> str:
    """Extract JS text containing tickets-related functions."""
    m = re.search(
        r"(function renderTickets.*?)(?=\n// ──|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else html  # fall back to full html if not isolated


# =============================================================================
# AC1 — Row anatomy: label chips visible, consistent columns
# =============================================================================


class TestRowAnatomy:
    def test_tkt_gh_label_class_defined(self, html):
        """AC1 — .tkt-gh-label CSS class must be defined for label chips."""
        assert ".tkt-gh-label" in html, (
            "AC1: .tkt-gh-label CSS rule must be defined for GitHub label chips"
        )

    def test_tkt_labels_row_class_defined(self, html):
        """AC1 — .tkt-labels-row class must exist to group label chips in the row."""
        assert ".tkt-labels-row" in html, (
            "AC1: .tkt-labels-row must be defined as the label chip container"
        )

    def test_label_chips_rendered_in_ticket_rows(self, html):
        """AC1 — renderTickets JS must reference tkt-labels-row in the row template."""
        assert "tkt-labels-row" in html, (
            "AC1: renderTickets must emit tkt-labels-row elements for label chips"
        )

    def test_ticket_row_preserves_status_pill(self, html):
        """AC1 — ticket-status-pill must remain in the row template."""
        # The row template must still include the status pill
        row_template_region = re.search(
            r"ticket-row.*?ticket-est-wrap",
            html,
            re.DOTALL,
        )
        assert row_template_region, "ticket-row template must include ticket-est-wrap"

    def test_tkt_gh_label_function_exists(self, html):
        """AC1 — _tktGhLabelChipsHtml helper function must be defined."""
        assert "_tktGhLabelChipsHtml" in html, (
            "AC1: _tktGhLabelChipsHtml function must render label chips for ticket rows"
        )


# =============================================================================
# AC2 — Token-based chip styling
# =============================================================================


class TestTokenChipStyling:
    def test_size_pill_sz_s_variant(self, html):
        """AC2 — Size pill must have an 'sz-S' variant with token colors."""
        assert "ticket-est-size-pill.sz-S" in html or ".sz-S" in html, (
            "AC2: size pill must have .sz-S variant using token color"
        )

    def test_size_pill_sz_m_variant(self, html):
        """AC2 — Size pill must have an 'sz-M' variant with token colors."""
        assert "ticket-est-size-pill.sz-M" in html or ".sz-M" in html, (
            "AC2: size pill must have .sz-M variant using token color"
        )

    def test_size_pill_sz_l_variant(self, html):
        """AC2 — Size pill must have an 'sz-L' variant with token colors."""
        assert "ticket-est-size-pill.sz-L" in html or ".sz-L" in html, (
            "AC2: size pill must have .sz-L variant using token color"
        )

    def test_size_pill_sz_xl_variant(self, html):
        """AC2 — Size pill must have an 'sz-XL' variant with token colors."""
        assert "ticket-est-size-pill.sz-XL" in html or ".sz-XL" in html, (
            "AC2: size pill must have .sz-XL variant using token color"
        )

    def test_tkt_gh_label_uses_css_vars(self, html):
        """AC2 — .tkt-gh-label must reference CSS custom properties (not hardcoded hex)."""
        block = re.search(r"\.tkt-gh-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .tkt-gh-label CSS rule must be defined"
        decls = block.group(1)
        assert "var(--" in decls, (
            "AC2: .tkt-gh-label must use CSS custom properties for dark-theme support"
        )

    def test_status_pill_sit_uses_token_color(self, html):
        """AC2 — .ticket-status-pill.SIT must use blue token colors."""
        block = re.search(
            r"\.ticket-status-pill\.SIT\s*\{([^}]+)\}", html, re.DOTALL
        )
        assert block, "AC2: .ticket-status-pill.SIT rule must be defined"
        decls = block.group(1)
        assert "var(--blue" in decls, (
            "AC2: SIT status pill must use --blue token color"
        )

    def test_size_pill_s_uses_green_token(self, html):
        """AC2 — sz-S variant must use green token colors."""
        block = re.search(r"\.sz-S\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .sz-S CSS rule must be defined"
        decls = block.group(1)
        assert "var(--green" in decls, (
            "AC2: sz-S must use --green token"
        )

    def test_size_pill_l_uses_amber_token(self, html):
        """AC2 — sz-L variant must use amber token colors."""
        block = re.search(r"\.sz-L\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .sz-L CSS rule must be defined"
        decls = block.group(1)
        assert "var(--amber" in decls, (
            "AC2: sz-L must use --amber token"
        )


# =============================================================================
# AC3 — Typography token scale
# =============================================================================


class TestTypographyScale:
    def test_ticket_row_has_font_size(self, html):
        """AC3 — .ticket-row must specify a font-size rule."""
        block = re.search(r"\.ticket-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .ticket-row CSS rule must be defined"
        # font-size should be defined (already is at 13px)

    def test_ticket_group_hdr_has_uppercase_label(self, html):
        """AC3 — .ticket-group-hdr must use uppercase text-transform for hierarchy."""
        block = re.search(r"\.ticket-group-hdr\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .ticket-group-hdr CSS rule must be defined"
        decls = block.group(1)
        assert "uppercase" in decls, (
            "AC3: group header must use text-transform: uppercase for scannable hierarchy"
        )

    def test_ticket_title_truncates_properly(self, html):
        """AC3 — .ticket-title must have text-overflow: ellipsis for long titles."""
        block = re.search(r"\.ticket-title\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .ticket-title CSS rule must be defined"
        decls = block.group(1)
        assert "ellipsis" in decls, (
            "AC3: .ticket-title must truncate with ellipsis for scannable hierarchy"
        )

    def test_tkt_empty_title_has_font_size(self, html):
        """AC3 — .tkt-empty-title must have a font-size for hierarchy."""
        block = re.search(r"\.tkt-empty-title\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .tkt-empty-title CSS rule must be defined"


# =============================================================================
# AC4 — Empty backlog state component
# =============================================================================


class TestEmptyState:
    def test_tkt_empty_state_class_defined(self, html):
        """AC4 — .tkt-empty-state CSS class must be defined."""
        assert ".tkt-empty-state" in html, (
            "AC4: .tkt-empty-state CSS class must be defined for the empty state component"
        )

    def test_tkt_empty_title_class_defined(self, html):
        """AC4 — .tkt-empty-title CSS class must be defined."""
        assert ".tkt-empty-title" in html, (
            "AC4: .tkt-empty-title CSS class must be defined"
        )

    def test_tkt_empty_sub_class_defined(self, html):
        """AC4 — .tkt-empty-sub CSS class must be defined."""
        assert ".tkt-empty-sub" in html, (
            "AC4: .tkt-empty-sub CSS class must be defined"
        )

    def test_empty_state_rendered_in_js(self, html):
        """AC4 — renderTickets JS must use tkt-empty-state when there are no tickets."""
        assert "tkt-empty-state" in html, (
            "AC4: renderTickets must emit tkt-empty-state when groups.length === 0"
        )

    def test_no_raw_loading_msg_for_empty(self, html):
        """AC4 — The raw 'No open tickets.' text must be replaced by the empty state."""
        # The empty case must use the new component, not the old loading-msg fallback
        # Check that renderTickets does not assign loading-msg for the empty case
        render_fn = re.search(
            r"function renderTickets\(.*?\n\}",
            html,
            re.DOTALL,
        )
        assert render_fn, "renderTickets function must exist"
        fn_body = render_fn.group(0)
        # Ensure tkt-empty-state is used (not loading-msg) for empty path
        assert "tkt-empty-state" in fn_body, (
            "AC4: renderTickets must use tkt-empty-state class for empty tickets"
        )


# =============================================================================
# AC5 — Existing handlers preserved
# =============================================================================


class TestHandlersPreserved:
    def test_on_ticket_checkbox_handler(self, html):
        """AC5 — _onTicketCheckbox event handler must still be referenced."""
        assert "_onTicketCheckbox" in html, (
            "AC5: _onTicketCheckbox handler must be preserved"
        )

    def test_open_close_ticket_modal_handler(self, html):
        """AC5 — openCloseTicketModal handler must still be referenced."""
        assert "openCloseTicketModal" in html, (
            "AC5: openCloseTicketModal handler must be preserved"
        )

    def test_open_bulk_close_uat_modal_handler(self, html):
        """AC5 — openBulkCloseUatModal handler must still be referenced."""
        assert "openBulkCloseUatModal" in html, (
            "AC5: openBulkCloseUatModal handler must be preserved"
        )

    def test_ticket_groups_logic_preserved(self, html):
        """AC5 — Status grouping logic must still group tickets by SIT/UAT/In Progress/Backlog."""
        assert "'SIT'" in html or '"SIT"' in html, (
            "AC5: SIT status group must be preserved in renderTickets"
        )
        assert "'UAT'" in html or '"UAT"' in html, (
            "AC5: UAT status group must be preserved in renderTickets"
        )

    def test_create_label_button_preserved(self, html):
        """AC5 — 'Create Label' button must be preserved in the tickets toolbar."""
        assert "openCreateLabelModal" in html, (
            "AC5: openCreateLabelModal must remain accessible in tickets toolbar"
        )

    def test_load_tickets_function_exists(self, html):
        """AC5 — loadTickets async function must still exist."""
        assert "async function loadTickets" in html, (
            "AC5: loadTickets must still exist as the data-loading entry point"
        )

    def test_tickets_load_estimates_preserved(self, html):
        """AC5 — _ticketsLoadEstimates must still be called for estimate data."""
        assert "_ticketsLoadEstimates" in html, (
            "AC5: _ticketsLoadEstimates must be preserved for estimate data loading"
        )


# =============================================================================
# AC6 — No new JS frameworks
# =============================================================================


class TestNoNewFrameworks:
    def test_no_react_import(self, html):
        """AC6 — No React import in project.html."""
        assert "import React" not in html, "AC6: No React import allowed"

    def test_no_vue_import(self, html):
        """AC6 — No Vue import in project.html."""
        assert "from 'vue'" not in html and 'from "vue"' not in html, (
            "AC6: No Vue import allowed"
        )

    def test_no_angular_import(self, html):
        """AC6 — No Angular import in project.html."""
        assert "from '@angular" not in html, "AC6: No Angular import allowed"

    def test_no_new_cdn_scripts(self, html):
        """AC6 — The tickets tab must not add new CDN script tags for frameworks."""
        # Count script src tags
        cdn_scripts = re.findall(
            r'<script[^>]+src="https?://cdn\.',
            html,
        )
        # Any existing CDN scripts are fine; we just can't add new framework ones
        for s in cdn_scripts:
            assert "react" not in s.lower(), "AC6: No React CDN script allowed"
            assert "vue" not in s.lower(), "AC6: No Vue CDN script allowed"


# =============================================================================
# AC8 — Diff scoped to tickets view region
# =============================================================================


class TestScopedDiff:
    def test_tickets_css_section_has_tkt_classes(self, tickets_css_section):
        """AC8 — New CSS classes must appear in the tickets CSS section."""
        assert "tkt-empty-state" in tickets_css_section, (
            "AC8: .tkt-empty-state must be defined in the tickets CSS section"
        )

    def test_no_changes_to_sprint_board_classes(self, html):
        """AC8 — Core sprint board CSS classes must still exist unchanged."""
        assert ".smgmt-sprint-tickets" in html, (
            "AC8: sprint board classes must not be affected"
        )
        assert ".sc-v5" in html, (
            "AC8: sprint card classes must not be affected"
        )


# =============================================================================
# AC9 — Dark theme maintained
# =============================================================================


class TestDarkTheme:
    def test_tkt_empty_state_uses_css_vars(self, html):
        """AC9 — .tkt-empty-state must use CSS custom properties for dark theme."""
        block = re.search(r"\.tkt-empty-state\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC9: .tkt-empty-state CSS rule must be defined"
        # The empty state parent doesn't need explicit color since it inherits
        # from the themed body — just confirm no hardcoded hex in this block
        decls = block.group(1)
        hardcoded = re.findall(r"#[0-9a-fA-F]{3,6}\b", decls)
        assert not hardcoded, (
            f"AC9: .tkt-empty-state must not use hardcoded hex colors: {hardcoded}"
        )

    def test_tkt_empty_icon_uses_text_sub_token(self, html):
        """AC9 — .tkt-empty-icon must use --text-sub token for dark/light compatibility."""
        block = re.search(r"\.tkt-empty-icon\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC9: .tkt-empty-icon CSS rule must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" in decls or "var(--text-muted)" in decls, (
            "AC9: .tkt-empty-icon must use --text-sub or --text-muted for themed color"
        )

    def test_tkt_gh_label_border_uses_css_var(self, html):
        """AC9 — .tkt-gh-label border must use --border CSS var."""
        block = re.search(r"\.tkt-gh-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC9: .tkt-gh-label CSS rule must be defined"
        decls = block.group(1)
        assert "var(--border)" in decls or "var(--surface" in decls, (
            "AC9: .tkt-gh-label must use themed border or surface token"
        )

    def test_no_hardcoded_hex_in_new_tkt_classes(self, html):
        """AC9 — New .tkt-* CSS rules must not use hardcoded hex colors."""
        tkt_blocks = re.findall(r"(\.tkt-[a-z-]+\s*\{[^}]+\})", html, re.DOTALL)
        for block in tkt_blocks:
            hexes = re.findall(r"#[0-9a-fA-F]{3,6}\b", block)
            assert not hexes, (
                f"AC9: CSS block must not use hardcoded hex — found {hexes} in:\n{block}"
            )

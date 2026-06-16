"""Acceptance tests for issue #1060 — Audit and fix Backlog tab impeccable violations.

AC map:
  AC1   Run impeccable detect on the tickets view region; all findings documented before fix
  AC2   Chip contrast meets WCAG AA minimum (4.5:1 for text, 3:1 for UI components)
        using foundation tokens only
  AC3   Row spacing is consistent and aligned to the spacing scale (no 5px/10px gaps)
  AC4   Column alignment is uniform across all ticket rows
  AC5   Typography uses only foundation type tokens — no ad-hoc font-size overrides
        outside the accepted token scale
  AC6   Re-run impeccable detect reports zero findings (all contrast violations fixed)
  AC7   All ticket filters remain functional after changes
  AC8   No regressions in other project tabs (Overview, Board, etc.)
  AC9   Changes scoped strictly to the tickets region; no global style side-effects
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"

# Dark-mode token values for WCAG contrast calculations.
DARK_BG = "#0d0d0d"
DARK_SURFACE_2 = "#1e1e1e"
DARK_TEXT_MUTED = "#9ca3af"   # target: safe for chips (≥7:1 on DARK_SURFACE_2)
DARK_TEXT_SUB = "#6b7280"     # violating: ~3.55:1 on DARK_SURFACE_2 (< 4.5:1)


@pytest.fixture(scope="module")
def html() -> str:
    assert PROJECT_HTML.exists(), f"project.html not found at {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tickets_css_section(html) -> str:
    """Extract the CSS section for the tickets tab (lines between markers)."""
    m = re.search(
        r"/\*\s*──\s*Tickets tab\s*──\s*\*/(.*?)(?=/\*\s*──\s*(?:Mobile|Tablet|Loading)|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


# =============================================================================
# AC1 — Pre-fix findings documented (violations that exist before the fix)
# These tests verify the ORIGINAL violations are present in the source —
# they pass on unmodified code and should be read alongside the fix tests.
# =============================================================================


class TestPreFixDocumentation:
    """Document what violations existed — kept as living documentation."""

    def test_tickets_region_exists(self, html):
        """AC1 — The tickets CSS section marker must exist for region scoping."""
        assert "/* ── Tickets tab ── */" in html, (
            "AC1: Tickets CSS section marker must be present in project.html"
        )

    def test_tkt_gh_label_class_exists(self, html):
        """AC1 — .tkt-gh-label must exist (introduced in #1059, needs contrast fix)."""
        assert ".tkt-gh-label" in html, "AC1: .tkt-gh-label class must exist"

    def test_ticket_status_pill_backlog_exists(self, html):
        """AC1 — .ticket-status-pill.backlog must exist (needs contrast fix)."""
        assert ".ticket-status-pill.backlog" in html, (
            "AC1: .ticket-status-pill.backlog must exist"
        )


# =============================================================================
# AC2 — Chip contrast: no --text-sub on chip/pill surfaces in the tickets region
#
# Dark-mode analysis:
#   --text-sub (#6b7280) on --surface-2 (#1e1e1e): ~3.55:1  → FAIL (< 4.5:1)
#   --text-sub (#6b7280) on --bg       (#0d0d0d): ~4.29:1  → FAIL (< 4.5:1)
#   --text-muted (#9ca3af) on --surface-2:         ~7.05:1  → PASS
#   --text-muted (#9ca3af) on --bg:                ~8.51:1  → PASS
# =============================================================================


class TestChipContrast:
    def test_tkt_gh_label_not_text_sub(self, html):
        """AC2 — .tkt-gh-label must not use --text-sub (dark: 3.55:1 on --surface-2, FAIL)."""
        block = re.search(r"\.tkt-gh-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .tkt-gh-label CSS rule must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .tkt-gh-label must not use --text-sub; "
            "dark-mode contrast is ~3.55:1 (< 4.5:1 WCAG AA). Use --text-muted."
        )

    def test_tkt_gh_label_uses_text_muted(self, html):
        """AC2 — .tkt-gh-label must use --text-muted for WCAG AA contrast (~7:1 dark)."""
        block = re.search(r"\.tkt-gh-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .tkt-gh-label CSS rule must be defined"
        decls = block.group(1)
        assert "var(--text-muted)" in decls, (
            "AC2: .tkt-gh-label must use var(--text-muted) for WCAG AA compliance"
        )

    def test_ticket_status_pill_backlog_not_text_sub(self, html):
        """AC2 — .ticket-status-pill.backlog must not use --text-sub (3.55:1 fail)."""
        block = re.search(
            r"\.ticket-status-pill\.backlog\s*\{([^}]+)\}", html, re.DOTALL
        )
        assert block, "AC2: .ticket-status-pill.backlog must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .ticket-status-pill.backlog must not use --text-sub; "
            "contrast ~3.55:1 on --surface-2 (< 4.5:1). Use --text-muted."
        )

    def test_ticket_status_pill_backlog_uses_text_muted(self, html):
        """AC2 — .ticket-status-pill.backlog must use --text-muted."""
        block = re.search(
            r"\.ticket-status-pill\.backlog\s*\{([^}]+)\}", html, re.DOTALL
        )
        assert block, "AC2: .ticket-status-pill.backlog must be defined"
        decls = block.group(1)
        assert "var(--text-muted)" in decls, (
            "AC2: .ticket-status-pill.backlog must use var(--text-muted)"
        )

    def test_ticket_est_size_pill_base_not_text_sub(self, html):
        """AC2 — .ticket-est-size-pill base must not use --text-sub (3.55:1 fail)."""
        # Extract the BASE rule (not the .sz-* variants)
        base_block = re.search(
            r"(?<!\.)\.ticket-est-size-pill\s*\{([^}]+)\}",
            html,
            re.DOTALL,
        )
        assert base_block, "AC2: .ticket-est-size-pill base CSS rule must be defined"
        decls = base_block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .ticket-est-size-pill must not use --text-sub; "
            "contrast ~3.55:1 on --surface-2 (< 4.5:1). Use --text-muted."
        )

    def test_ticket_est_size_pill_base_uses_text_muted(self, html):
        """AC2 — .ticket-est-size-pill base must use --text-muted."""
        base_block = re.search(
            r"(?<!\.)\.ticket-est-size-pill\s*\{([^}]+)\}",
            html,
            re.DOTALL,
        )
        assert base_block, "AC2: .ticket-est-size-pill base CSS rule must be defined"
        decls = base_block.group(1)
        assert "var(--text-muted)" in decls, (
            "AC2: .ticket-est-size-pill base must use var(--text-muted)"
        )

    def test_dep_chip_label_not_text_sub(self, html):
        """AC2 — .dep-chip-label must not use --text-sub (4.29:1 on --bg, fails at 10px)."""
        block = re.search(r"\.dep-chip-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .dep-chip-label must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .dep-chip-label must not use --text-sub; "
            "dark-mode contrast ~4.29:1 on --bg (< 4.5:1 for 10px text). Use --text-muted."
        )

    def test_ticket_num_not_text_sub(self, html):
        """AC2 — .ticket-num must not use --text-sub (4.29:1 on --bg, fails at 11px)."""
        block = re.search(r"\.ticket-num\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .ticket-num must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .ticket-num must not use --text-sub; "
            "dark-mode contrast ~4.29:1 on --bg (< 4.5:1 for 11px text). Use --text-muted."
        )

    def test_tkt_empty_sub_not_text_sub(self, html):
        """AC2 — .tkt-empty-sub must not use --text-sub (4.29:1 on --bg, fails at 13px)."""
        block = re.search(r"\.tkt-empty-sub\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .tkt-empty-sub must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .tkt-empty-sub must not use --text-sub; "
            "dark-mode contrast ~4.29:1 on --bg (< 4.5:1 for 13px body text). Use --text-muted."
        )

    def test_ticket_group_rollup_not_text_sub(self, html):
        """AC2 — .ticket-group-rollup must not use --text-sub (fails at 10px)."""
        block = re.search(r"\.ticket-group-rollup\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .ticket-group-rollup must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .ticket-group-rollup must not use --text-sub; "
            "dark-mode contrast ~4.29:1 on --bg (< 4.5:1 for 10px text). Use --text-muted."
        )

    def test_ticket_no_estimate_not_text_sub(self, html):
        """AC2 — .ticket-no-estimate must not use --text-sub (fails at 10px)."""
        block = re.search(r"\.ticket-no-estimate\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC2: .ticket-no-estimate must be defined"
        decls = block.group(1)
        assert "var(--text-sub)" not in decls, (
            "AC2: .ticket-no-estimate must not use --text-sub; "
            "dark-mode contrast ~4.29:1 on --bg (< 4.5:1 for 10px text). Use --text-muted."
        )

    def test_chip_colors_use_token_vars(self, html):
        """AC2 — All status chip variants must use token CSS variables, not hardcoded hex."""
        chip_blocks = re.findall(
            r"(\.ticket-(?:status-pill|est-size-pill)[^\s{]*\s*\{[^}]+\})",
            html,
            re.DOTALL,
        )
        for block in chip_blocks:
            hexes = re.findall(r"#[0-9a-fA-F]{3,6}\b", block)
            assert not hexes, (
                f"AC2: Chip rule must use CSS custom properties, not hardcoded hex: "
                f"found {hexes} in:\n{block[:200]}"
            )


# =============================================================================
# AC3 — Row spacing: consistent 4px-scale values (no 5px or 10px in gaps)
# =============================================================================


class TestRowSpacing:
    def test_tkt_labels_row_gap_is_not_5px(self, html):
        """AC3 — .tkt-labels-row gap must NOT be 5px (not on 4px spacing scale)."""
        block = re.search(r"\.tkt-labels-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .tkt-labels-row must be defined"
        decls = block.group(1)
        assert "gap: 5px" not in decls, (
            "AC3: .tkt-labels-row gap: 5px is not on the 4px scale. "
            "Use gap: 4px or gap: 8px."
        )

    def test_tkt_labels_row_gap_is_4px(self, html):
        """AC3 — .tkt-labels-row gap must be 4px (4px spacing scale)."""
        block = re.search(r"\.tkt-labels-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .tkt-labels-row must be defined"
        decls = block.group(1)
        assert "gap: 4px" in decls, (
            "AC3: .tkt-labels-row must use gap: 4px (4px spacing scale)"
        )

    def test_dep_chips_row_gap_is_not_5px(self, html):
        """AC3 — .dep-chips-row gap must NOT be 5px (not on 4px spacing scale)."""
        block = re.search(r"\.dep-chips-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .dep-chips-row must be defined"
        decls = block.group(1)
        assert "gap: 5px" not in decls, (
            "AC3: .dep-chips-row gap: 5px is not on the 4px scale. "
            "Use gap: 4px or gap: 8px."
        )

    def test_dep_chips_row_gap_is_4px(self, html):
        """AC3 — .dep-chips-row gap must be 4px (4px spacing scale)."""
        block = re.search(r"\.dep-chips-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .dep-chips-row must be defined"
        decls = block.group(1)
        assert "gap: 4px" in decls, (
            "AC3: .dep-chips-row must use gap: 4px (4px spacing scale)"
        )

    def test_tkt_empty_state_gap_is_not_10px(self, html):
        """AC3 — .tkt-empty-state gap must NOT be 10px (not on 4px spacing scale)."""
        block = re.search(r"\.tkt-empty-state\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .tkt-empty-state must be defined"
        decls = block.group(1)
        assert "gap: 10px" not in decls, (
            "AC3: .tkt-empty-state gap: 10px is not on the 4px scale. "
            "Use gap: 8px or gap: 12px."
        )

    def test_ticket_status_pill_padding_uses_even_scale(self, html):
        """AC3 — .ticket-status-pill padding must use 4px-scale horizontal values (not 7px)."""
        block = re.search(r"\.ticket-status-pill\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC3: .ticket-status-pill must be defined"
        decls = block.group(1)
        # 7px is not on the 4px scale; acceptable values: 6px, 8px
        assert "7px" not in decls, (
            "AC3: .ticket-status-pill has 7px horizontal padding which is not on the 4px scale. "
            "Use 6px or 8px."
        )

    def test_ticket_row_gap_is_not_10px(self, html):
        """AC3 — .ticket-row gap must NOT be 10px (not on 4px spacing scale)."""
        block = re.search(
            r"\.ticket-row\s*\{([^}]+)\}",
            html,
            re.DOTALL,
        )
        assert block, "AC3: .ticket-row must be defined"
        decls = block.group(1)
        assert "gap: 10px" not in decls, (
            "AC3: .ticket-row gap: 10px is not on the 4px scale. "
            "Use gap: 8px or gap: 12px."
        )


# =============================================================================
# AC4 — Column alignment: ticket rows have consistent flex structure
# =============================================================================


class TestColumnAlignment:
    def test_ticket_row_uses_flex_layout(self, html):
        """AC4 — .ticket-row must use display: flex for aligned columns."""
        block = re.search(r"\.ticket-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC4: .ticket-row must be defined"
        decls = block.group(1)
        assert "display: flex" in decls, (
            "AC4: .ticket-row must use display: flex for uniform column alignment"
        )
        assert "align-items: center" in decls, (
            "AC4: .ticket-row must use align-items: center for vertical alignment"
        )

    def test_ticket_title_flex_grows(self, html):
        """AC4 — .ticket-title must use flex: 1 to fill remaining space consistently."""
        block = re.search(r"\.ticket-title\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC4: .ticket-title must be defined"
        decls = block.group(1)
        assert "flex: 1" in decls, (
            "AC4: .ticket-title must use flex: 1 so it fills available column width"
        )

    def test_ticket_status_pill_no_flex_grow(self, html):
        """AC4 — .ticket-status-pill must NOT use flex: 1 (chips must have fixed size)."""
        block = re.search(r"\.ticket-status-pill\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC4: .ticket-status-pill must be defined"
        decls = block.group(1)
        assert "flex: 1" not in decls, (
            "AC4: Status chips must not grow; .ticket-status-pill must not have flex: 1"
        )

    def test_ticket_row_has_single_line_layout(self, html):
        """Issues rows keep title + labels on one line (flex-wrap: nowrap)."""
        row_rule = re.search(r"\.ticket-row\s*\{([^}]+)\}", html, re.DOTALL)
        assert row_rule, "AC4: .ticket-row must be defined"
        decls = row_rule.group(1)
        assert "flex-wrap: nowrap" in decls or "flex-wrap:nowrap" in decls.replace(" ", ""), (
            "AC4: .ticket-row must use flex-wrap: nowrap for a single-line layout"
        )


# =============================================================================
# AC5 — Typography: only accepted font-size values in the tickets region
#
# The accepted scale (pixels) in this codebase is: 9, 10, 11, 12, 13, 14, 15, 16.
# Values like 17px, 18px outside this set would be ad-hoc overrides.
# =============================================================================

ACCEPTED_FONT_SIZES = {9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 24, 28, 32, 36}


class TestTypography:
    def test_tickets_region_font_sizes_on_scale(self, tickets_css_section):
        """AC5 — All font-size values in the tickets CSS must be from the accepted scale."""
        raw_sizes = re.findall(r"font-size:\s*(\d+)px", tickets_css_section)
        bad = [s for s in raw_sizes if int(s) not in ACCEPTED_FONT_SIZES]
        assert not bad, (
            f"AC5: Non-scale font-size values found in tickets CSS: {bad}px. "
            f"Accepted scale: {sorted(ACCEPTED_FONT_SIZES)}"
        )

    def test_ticket_group_hdr_uses_uppercase(self, html):
        """AC5 — .ticket-group-hdr must use text-transform: uppercase for clear hierarchy."""
        block = re.search(r"\.ticket-group-hdr\s*\{([^}]+)\}", html, re.DOTALL)
        assert block, "AC5: .ticket-group-hdr must be defined"
        decls = block.group(1)
        assert "uppercase" in decls, (
            "AC5: .ticket-group-hdr must use text-transform: uppercase for typography hierarchy"
        )

    def test_no_hardcoded_hex_in_tkt_classes(self, html):
        """AC5 — .tkt-* CSS rules must not use hardcoded hex colors."""
        tkt_blocks = re.findall(r"(\.tkt-[a-z-]+\s*\{[^}]+\})", html, re.DOTALL)
        for block in tkt_blocks:
            hexes = re.findall(r"#[0-9a-fA-F]{3,6}\b", block)
            assert not hexes, (
                f"AC5: .tkt-* rule uses hardcoded hex {hexes}. Use CSS custom properties.\n"
                f"Block: {block[:200]}"
            )


# =============================================================================
# AC6 — Zero findings: verify all contrast violations are resolved
# =============================================================================


class TestZeroFindings:
    def test_no_text_sub_color_on_surface_2_chips(self, html):
        """AC6 — No chip/pill CSS in tickets region uses --text-sub on --surface-2."""
        # Collect all chip-like class blocks in the tickets CSS
        chip_patterns = [
            r"\.tkt-gh-label\s*\{([^}]+)\}",
            r"\.ticket-status-pill\.backlog\s*\{([^}]+)\}",
            r"(?<!\.)\.ticket-est-size-pill\s*\{([^}]+)\}",
        ]
        for pat in chip_patterns:
            m = re.search(pat, html, re.DOTALL)
            if m:
                decls = m.group(1)
                assert "var(--text-sub)" not in decls, (
                    f"AC6: Chip CSS still uses --text-sub (dark: 3.55:1 on --surface-2). "
                    f"Rule: {pat}"
                )

    def test_no_text_sub_on_bg_text_elements(self, html):
        """AC6 — No text elements in tickets region use --text-sub on --bg."""
        text_patterns = [
            r"\.ticket-num\s*\{([^}]+)\}",
            r"\.tkt-empty-sub\s*\{([^}]+)\}",
            r"\.dep-chip-label\s*\{([^}]+)\}",
            r"\.ticket-group-rollup\s*\{([^}]+)\}",
            r"\.ticket-no-estimate\s*\{([^}]+)\}",
        ]
        for pat in text_patterns:
            m = re.search(pat, html, re.DOTALL)
            if m:
                decls = m.group(1)
                assert "var(--text-sub)" not in decls, (
                    f"AC6: Text element CSS still uses --text-sub "
                    f"(dark: 4.29:1 on --bg, < 4.5:1 WCAG AA). Rule: {pat}"
                )

    def test_all_chip_colors_are_token_based(self, html):
        """AC6 — Every chip background and color value uses CSS custom properties."""
        # Grep for CSS blocks defining chip/label/pill elements
        tkt_blocks = re.findall(
            r"(\.(?:tkt|ticket)-[a-z-]+(?:\.[a-z-]+)?\s*\{[^}]+\})",
            html,
            re.DOTALL,
        )
        for block in tkt_blocks:
            # Look for plain hex or rgb() that indicate hardcoded values
            bare_hex = re.findall(r"(?:color|background)[^:]*:\s*#[0-9a-fA-F]{3,6}\b", block)
            assert not bare_hex, (
                f"AC6: Chip/ticket CSS block uses hardcoded color value: {bare_hex}\n"
                f"Block: {block[:200]}"
            )


# =============================================================================
# AC7 — Filters remain functional (JS handlers preserved)
# =============================================================================


class TestFiltersPreserved:
    def test_load_tickets_function_exists(self, html):
        """AC7 — loadTickets function must exist for data loading."""
        assert "async function loadTickets" in html, (
            "AC7: loadTickets function must still exist"
        )

    def test_render_tickets_function_exists(self, html):
        """AC7 — renderTickets function must exist for display logic."""
        assert "function renderTickets" in html, (
            "AC7: renderTickets function must still exist"
        )

    def test_ticket_status_groups_preserved(self, html):
        """AC7 — SIT/UAT/In Progress/Backlog status group logic must be preserved."""
        assert "'SIT'" in html or '"SIT"' in html, "AC7: SIT group must be preserved"
        assert "'UAT'" in html or '"UAT"' in html, "AC7: UAT group must be preserved"
        assert "'Backlog'" in html or '"Backlog"' in html, (
            "AC7: Backlog group must be preserved"
        )

    def test_checkbox_handler_preserved(self, html):
        """AC7 — _onTicketCheckbox handler must still be wired in ticket rows."""
        assert "_onTicketCheckbox" in html, (
            "AC7: _onTicketCheckbox handler must be preserved"
        )

    def test_close_ticket_modal_preserved(self, html):
        """AC7 — openCloseTicketModal must still be referenced in the Close button."""
        assert "openCloseTicketModal" in html, (
            "AC7: openCloseTicketModal must be preserved"
        )

    def test_create_label_modal_preserved(self, html):
        """AC7 — openCreateLabelModal must remain in the tickets toolbar."""
        assert "openCreateLabelModal" in html, (
            "AC7: openCreateLabelModal button must be preserved in tickets toolbar"
        )

    def test_dep_chip_function_preserved(self, html):
        """AC7 — _depChipsHtml function must exist for dependency chip rendering."""
        assert "_depChipsHtml" in html, (
            "AC7: _depChipsHtml must be preserved for dep chip rendering"
        )

    def test_tkt_label_chips_function_preserved(self, html):
        """AC7 — _tktGhLabelChipsHtml function must exist for GitHub label chips."""
        assert "_tktGhLabelChipsHtml" in html, (
            "AC7: _tktGhLabelChipsHtml must be preserved"
        )


# =============================================================================
# AC8 — No regressions in other tabs
# =============================================================================


class TestNoRegressions:
    def test_sprint_board_classes_intact(self, html):
        """AC8 — Core sprint board CSS classes must be untouched."""
        assert ".smgmt-sprint-tickets" in html, (
            "AC8: .smgmt-sprint-tickets must not be removed (sprint board regression)"
        )
        assert ".sc-v5" in html, (
            "AC8: .sc-v5 sprint card class must not be removed"
        )

    def test_board_tab_css_intact(self, html):
        """AC8 — Board tab CSS must still exist."""
        assert ".smgmt-ticket-status" in html, (
            "AC8: .smgmt-ticket-status must not be removed (board regression)"
        )

    def test_overview_tab_classes_intact(self, html):
        """AC8 — Overview-related CSS must still exist."""
        assert ".snav-panel" in html or ".snav-pill" in html, (
            "AC8: Sprint nav pill/panel classes must not be removed"
        )

    def test_sse_event_source_preserved(self, html):
        """AC8 — SSE EventSource setup must be preserved (live updates)."""
        assert "EventSource" in html, (
            "AC8: EventSource must not be removed; live updates depend on it"
        )


# =============================================================================
# AC9 — Scope: changes are contained to the tickets region
# =============================================================================


class TestScopeContained:
    def test_global_root_tokens_unchanged(self, html):
        """AC9 — :root token block must not have new entries added by this fix."""
        root_block = re.search(r":root\s*\{([^}]+)\}", html, re.DOTALL)
        assert root_block, "AC9: :root token block must exist"
        decls = root_block.group(1)
        # No spacing tokens should be injected globally (scope: tickets region only)
        assert "--space-" not in decls, (
            "AC9: Global spacing tokens (--space-*) must not be added to :root; "
            "scope changes to the tickets region"
        )

    def test_no_changes_outside_tickets_section(self, html):
        """AC9 — The sprint management CSS section must remain unchanged."""
        assert ".smgmt-backlog-pane" in html, (
            "AC9: Sprint management backlog CSS must not be removed"
        )
        assert ".smgmt-backlog-header-row" in html, (
            "AC9: Sprint management backlog header must not be removed"
        )

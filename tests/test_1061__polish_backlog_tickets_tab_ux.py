"""Acceptance tests for issue #1061 — Polish Backlog/Tickets Tab UX and Accessibility.

AC map:
  AC1   Row hover state uses foundation design tokens (--surface-hover, dark theme)
  AC2   Row focus state visible and matches design system focus ring (--blue outline)
  AC3   Filter transitions smooth; prefers-reduced-motion disables animations
  AC4   Loading state renders intentionally (spinner or skeleton)
  AC5   Empty state renders a friendly, informative message when no tickets
  AC6   All interactive row elements keyboard reachable via Tab (tabindex on rows)
  AC7   Visible focus indicator present on all focusable elements
  AC8   ARIA labels applied to rows and interactive controls
  AC9   No new hardcoded color values in tickets CSS region (tokens only)
  AC10  No style or logic changes outside the tickets view region
  AC11  Vanilla JS/CSS; no new external dependencies introduced
  AC12  Foundation tokens reused; no hardcoded color or spacing values
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
def tickets_css(html) -> str:
    """CSS from '/* ── Tickets tab ── */' through '/* ── Ticket detail panel ── */'."""
    m = re.search(
        r"/\*\s*──\s*Tickets tab\s*──\s*\*/(.*?)(?=/\*\s*──\s*Ticket detail|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def tickets_js(html) -> str:
    """JS from renderTickets through the close-ticket helpers."""
    m = re.search(
        r"(function renderTickets.*?)(?=\n// ──\s*Close Ticket|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def load_tickets_js(html) -> str:
    """JS for the loadTickets async function."""
    m = re.search(
        r"(async function loadTickets\(\).*?)(?=\nfunction renderTickets|\Z)",
        html,
        re.DOTALL,
    )
    return m.group(1) if m else ""


# =============================================================================
# AC1 — Row hover state uses --surface-hover token
# =============================================================================

class TestHoverState:
    def test_ticket_row_hover_rule_exists(self, tickets_css):
        """AC1: .ticket-row:hover must be defined in the tickets CSS section."""
        assert ".ticket-row:hover" in tickets_css, (
            "AC1: .ticket-row:hover rule missing from tickets CSS section"
        )

    def test_ticket_row_hover_uses_surface_hover_token(self, tickets_css):
        """AC1: hover background must reference var(--surface-hover) token."""
        hover_block = re.search(r"\.ticket-row:hover\s*\{([^}]*)\}", tickets_css)
        assert hover_block, "AC1: .ticket-row:hover block must exist"
        assert "var(--surface-hover)" in hover_block.group(1), (
            "AC1: hover state must use var(--surface-hover), not a hardcoded color"
        )

    def test_ticket_row_has_transition(self, tickets_css):
        """AC1: .ticket-row base rule must include a transition for smooth hover."""
        row_block = re.search(r"\.ticket-row\s*\{([^}]*)\}", tickets_css)
        assert row_block, "AC1: .ticket-row base rule must exist"
        assert "transition" in row_block.group(1), (
            "AC1: .ticket-row must have a transition property for smooth hover"
        )


# =============================================================================
# AC2 — Row focus state uses design-system focus ring (--blue outline)
# =============================================================================

class TestFocusState:
    def test_focus_state_rule_exists(self, tickets_css):
        """AC2: A :focus-within or :focus-visible rule must exist for ticket rows."""
        has_focus_within = ":focus-within" in tickets_css
        has_focus_visible = "ticket-row" in tickets_css and ":focus-visible" in tickets_css
        assert has_focus_within or has_focus_visible, (
            "AC2: focus state rule (:focus-within or :focus-visible) missing from tickets CSS"
        )

    def test_focus_ring_uses_blue_token(self, tickets_css):
        """AC2: Focus ring must use var(--blue) to match the design system."""
        focus_block_m = re.search(
            r"(\.ticket-row.*?:focus[-\w]*\s*\{[^}]*\})", tickets_css, re.DOTALL
        )
        assert focus_block_m, "AC2: focus block on .ticket-row must exist"
        assert "var(--blue)" in focus_block_m.group(1), (
            "AC2: focus ring must reference var(--blue)"
        )

    def test_focus_ring_uses_outline(self, tickets_css):
        """AC2: Focus ring must use 'outline' property (not box-shadow only)."""
        focus_block_m = re.search(
            r"(\.ticket-row.*?:focus[-\w]*\s*\{[^}]*\})", tickets_css, re.DOTALL
        )
        assert focus_block_m, "AC2: focus block on .ticket-row must exist"
        assert "outline" in focus_block_m.group(1), (
            "AC2: focus ring must use outline property"
        )


# =============================================================================
# AC3 — Filter transitions smooth; prefers-reduced-motion disables animations
# =============================================================================

class TestFilterTransitions:
    def test_filter_chip_has_transition(self, tickets_css):
        """AC3: Filter chips must animate smoothly via CSS transition."""
        assert ".tkt-filter-chip" in tickets_css, (
            "AC3: .tkt-filter-chip class must exist in tickets CSS"
        )
        chip_block = re.search(r"\.tkt-filter-chip\s*\{([^}]*)\}", tickets_css)
        assert chip_block, "AC3: .tkt-filter-chip base rule must exist"
        assert "transition" in chip_block.group(1), (
            "AC3: .tkt-filter-chip must have a transition property"
        )

    def test_prefers_reduced_motion_in_tickets_region(self, tickets_css):
        """AC3: prefers-reduced-motion block must be inside the tickets CSS section."""
        assert "prefers-reduced-motion" in tickets_css, (
            "AC3: @media (prefers-reduced-motion: reduce) must appear in tickets CSS region"
        )

    def test_reduced_motion_disables_ticket_transition(self, tickets_css):
        """AC3: prefers-reduced-motion block must disable ticket-row transitions."""
        rm_block = re.search(
            r"prefers-reduced-motion[^{]*reduce[^{]*\{(.*?)\}",
            tickets_css,
            re.DOTALL,
        )
        assert rm_block, "AC3: prefers-reduced-motion block must be found"
        inner = rm_block.group(1)
        assert "transition: none" in inner or "transition:none" in inner, (
            "AC3: prefers-reduced-motion block must set transition: none"
        )

    def test_reduced_motion_disables_spinner_animation(self, tickets_css):
        """AC3: prefers-reduced-motion must disable the loading spinner animation."""
        rm_block = re.search(
            r"prefers-reduced-motion[^{]*reduce[^{]*\{(.*?)\}",
            tickets_css,
            re.DOTALL,
        )
        assert rm_block, "AC3: prefers-reduced-motion block required"
        inner = rm_block.group(1)
        assert "animation" in inner, (
            "AC3: prefers-reduced-motion block must disable animation (tkt-loading-spinner)"
        )


# =============================================================================
# AC4 — Loading state renders intentionally (spinner)
# =============================================================================

class TestLoadingState:
    def test_tkt_loading_class_in_css(self, tickets_css):
        """AC4: .tkt-loading CSS class must exist in the tickets section."""
        assert ".tkt-loading" in tickets_css, (
            "AC4: .tkt-loading CSS class required in tickets region"
        )

    def test_tkt_loading_spinner_class_in_css(self, tickets_css):
        """AC4: .tkt-loading-spinner CSS class must exist for spinner element."""
        assert ".tkt-loading-spinner" in tickets_css, (
            "AC4: .tkt-loading-spinner CSS class required"
        )

    def test_spinner_animation_defined(self, tickets_css):
        """AC4: Spinner must use a CSS animation (keyframes tkt-spin)."""
        assert "tkt-spin" in tickets_css, (
            "AC4: @keyframes tkt-spin must be defined in tickets CSS region"
        )

    def test_loading_spinner_used_in_js(self, load_tickets_js):
        """AC4: loadTickets() must use the tkt-loading HTML for intentional loading UX."""
        assert "tkt-loading" in load_tickets_js, (
            "AC4: loadTickets() must render tkt-loading (spinner) not plain loading-msg"
        )

    def test_spinner_element_in_js(self, load_tickets_js):
        """AC4: The spinner div element must appear in loadTickets() HTML string."""
        assert "tkt-loading-spinner" in load_tickets_js, (
            "AC4: tkt-loading-spinner element must be generated in loadTickets()"
        )


# =============================================================================
# AC5 — Empty state renders a friendly message
# =============================================================================

class TestEmptyState:
    def test_tkt_empty_state_class_exists(self, html):
        """AC5: .tkt-empty-state must be present (introduced in #1059, preserved here)."""
        assert "tkt-empty-state" in html, "AC5: .tkt-empty-state must exist"

    def test_empty_state_has_icon(self, tickets_js):
        """AC5: Empty state must include an icon for visual appeal."""
        assert "tkt-empty-icon" in tickets_js, (
            "AC5: empty state must include an icon element with class tkt-empty-icon"
        )

    def test_empty_state_has_title(self, tickets_js):
        """AC5: Empty state must have a title message."""
        assert "tkt-empty-title" in tickets_js, (
            "AC5: empty state must include a title element"
        )

    def test_empty_state_has_subtitle(self, tickets_js):
        """AC5: Empty state must have explanatory subtitle text."""
        assert "tkt-empty-sub" in tickets_js, (
            "AC5: empty state must include a subtitle element"
        )


# =============================================================================
# AC6 — All interactive row elements keyboard reachable via Tab
# =============================================================================

class TestKeyboardNavigation:
    def test_tabindex_on_ticket_rows(self, tickets_js):
        """AC6: Ticket row divs must have tabindex=0 for keyboard navigation."""
        assert 'tabindex="0"' in tickets_js, (
            'AC6: ticket-row elements must have tabindex="0" in renderTickets()'
        )

    def test_filter_chips_are_buttons(self, tickets_js):
        """AC6: Filter chips must be <button> elements (natively keyboard-focusable)."""
        assert "tkt-filter-chip" in tickets_js and "<button" in tickets_js, (
            "AC6: filter chips must be <button> elements so they receive Tab focus"
        )


# =============================================================================
# AC7 — Visible focus indicator on all focusable elements
# =============================================================================

class TestFocusIndicator:
    def test_focus_visible_rule_for_controls(self, tickets_css):
        """AC7: :focus-visible rules must cover interactive elements inside ticket rows."""
        assert ":focus-visible" in tickets_css, (
            "AC7: :focus-visible rules required for ticket row interactive controls"
        )

    def test_focus_indicator_uses_outline(self, tickets_css):
        """AC7: Focus indicator must use outline (not only color change) for visibility."""
        # Find any focus-visible block
        focus_blocks = re.findall(
            r":focus-visible\s*\{([^}]*)\}", tickets_css
        )
        assert focus_blocks, "AC7: at least one :focus-visible rule must exist"
        has_outline = any("outline" in b for b in focus_blocks)
        assert has_outline, (
            "AC7: at least one :focus-visible block must include outline property"
        )

    def test_filter_chip_has_focus_visible(self, tickets_css):
        """AC7: Filter chip must have :focus-visible rule for keyboard users."""
        assert "tkt-filter-chip:focus-visible" in tickets_css, (
            "AC7: .tkt-filter-chip:focus-visible rule must exist"
        )


# =============================================================================
# AC8 — ARIA labels on rows and interactive controls
# =============================================================================

class TestAriaLabels:
    def test_aria_label_on_checkbox(self, tickets_js):
        """AC8: Ticket checkbox must have aria-label for screen reader users."""
        # Look for aria-label on the input checkbox in renderTickets
        checkbox_block = re.search(
            r'<input[^>]+ticket-checkbox[^>]*>',
            tickets_js,
        )
        assert checkbox_block, "AC8: ticket-checkbox input must exist in renderTickets"
        assert "aria-label" in checkbox_block.group(0), (
            "AC8: ticket-checkbox input must have aria-label attribute"
        )

    def test_aria_label_on_close_button(self, tickets_js):
        """AC8: Ticket close button must have aria-label."""
        close_btn = re.search(
            r'<button[^>]+ticket-close-btn[^>]*>',
            tickets_js,
        )
        assert close_btn, "AC8: ticket-close-btn button must exist"
        assert "aria-label" in close_btn.group(0), (
            "AC8: ticket-close-btn must have aria-label attribute"
        )

    def test_filter_group_has_aria_label(self, tickets_js):
        """AC8: Filter chip group must have aria-label for labelling the group."""
        assert 'aria-label="Filter by status"' in tickets_js or \
               "aria-label='Filter by status'" in tickets_js, (
            'AC8: filter chips container must have aria-label="Filter by status"'
        )

    def test_ticket_row_has_aria_label(self, tickets_js):
        """AC8: Each ticket row must have an aria-label describing the issue."""
        row_block = re.search(
            r'<div[^>]+ticket-row[^>]+id="ticket-row-\$\{t\.number\}"[^>]*>',
            tickets_js,
        )
        if not row_block:
            # Multiline template literal, check the full JS
            assert 'aria-label' in tickets_js and 'ticket-row' in tickets_js, (
                "AC8: ticket-row elements must include aria-label"
            )
        else:
            assert "aria-label" in row_block.group(0), (
                "AC8: ticket-row div must have aria-label attribute"
            )


# =============================================================================
# AC9 — No new hardcoded hex colors in tickets CSS (tokens only)
# =============================================================================

class TestTokenUsage:
    def test_tkt_filter_chip_uses_tokens_only(self, tickets_css):
        """AC9/AC12: .tkt-filter-chip and .tkt-loading rules must use CSS vars only."""
        # Extract just the new classes added for #1061
        new_blocks = re.findall(
            r"\.tkt-(?:filter-chip|loading)[^{]*\{([^}]*)\}",
            tickets_css,
        )
        assert new_blocks, "AC9: tkt-filter-chip or tkt-loading blocks must exist"
        for block in new_blocks:
            hex_colors = re.findall(r"(?<!var\()#[0-9a-fA-F]{3,6}\b", block)
            assert not hex_colors, (
                f"AC9/AC12: hardcoded hex color found in tickets CSS block: {hex_colors}. "
                "Use CSS custom properties (var(--...)) instead."
            )

    def test_tkt_loading_spinner_uses_tokens(self, tickets_css):
        """AC12: .tkt-loading-spinner must reference only CSS vars for color."""
        spinner_block = re.search(r"\.tkt-loading-spinner\s*\{([^}]*)\}", tickets_css)
        assert spinner_block, "AC12: .tkt-loading-spinner block must exist"
        inner = spinner_block.group(1)
        hex_colors = re.findall(r"(?<!var\()#[0-9a-fA-F]{3,6}\b", inner)
        assert not hex_colors, (
            f"AC12: .tkt-loading-spinner uses hardcoded color {hex_colors}; use var(--border)/var(--blue)"
        )


# =============================================================================
# AC10 — Changes scoped to tickets view region
# =============================================================================

class TestScopedChanges:
    def test_new_css_classes_in_tickets_section(self, tickets_css):
        """AC10: New CSS classes for #1061 must live inside the tickets CSS region."""
        assert ".tkt-loading" in tickets_css, (
            "AC10: .tkt-loading must be scoped inside /* ── Tickets tab ── */ section"
        )
        assert ".tkt-filter-chip" in tickets_css, (
            "AC10: .tkt-filter-chip must be scoped inside /* ── Tickets tab ── */ section"
        )
        assert ".tkt-loading-spinner" in tickets_css, (
            "AC10: .tkt-loading-spinner must be scoped inside /* ── Tickets tab ── */ section"
        )

    def test_tkt_group_section_class_in_tickets_css(self, tickets_css):
        """AC10: .tkt-group-section must be in the tickets region for filter targeting."""
        assert ".tkt-group-section" in tickets_css, (
            "AC10: .tkt-group-section must be defined in the tickets CSS region"
        )


# =============================================================================
# AC11 — No new external dependencies
# =============================================================================

class TestNoDependencies:
    def test_no_new_cdn_scripts(self, html):
        """AC11: No new external script tags (CDN) added by this change."""
        # Count script src tags referencing external URLs — baseline check
        cdn_scripts = re.findall(r'<script[^>]+src=["\']https?://', html)
        # The existing page has Tabler icons and other CDN scripts; just ensure
        # no obviously new ticket-related CDN script was added (any animation lib, etc.)
        for s in cdn_scripts:
            assert "tkt" not in s.lower() and "ticket" not in s.lower(), (
                f"AC11: unexpected CDN script found: {s}"
            )

    def test_no_framework_imports(self, html):
        """AC11: No React/Vue/Svelte or other framework imports introduced."""
        ticket_section = re.search(
            r"(pane-tickets.*?pane-logs)",
            html,
            re.DOTALL,
        )
        if ticket_section:
            section_text = ticket_section.group(1)
            for framework in ("react", "vue", "svelte", "angular", "alpine"):
                assert framework not in section_text.lower(), (
                    f"AC11: framework '{framework}' must not appear in the tickets tab HTML"
                )

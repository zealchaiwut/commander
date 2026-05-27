"""Tests for issue #144: Move New Ticket Button Next to New Sprint.

Verifies all 6 acceptance criteria:
  AC1 - + New Ticket button appears in .smgmt-header, left of + New Sprint
  AC2 - Button is inside pview-sprint-mgmt (visible only when tab is active)
  AC3 - Clicking + New Ticket calls openDraftTicketModal() — no behaviour change
  AC4 - + New Ticket is removed from .header-right in the global page header
  AC5 - Both buttons in .smgmt-header are visually consistent (padding, font-size, border-radius)
  AC6 - No regression on other header buttons (Filters, New Project, Refresh, Theme Toggle)
"""
import re
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text()


# ---------------------------------------------------------------------------
# AC-1: + New Ticket appears in .smgmt-header, immediately left of + New Sprint
# ---------------------------------------------------------------------------

class TestAC1NewTicketInSmgmtHeader:
    def test_new_ticket_button_exists_in_smgmt_header(self, html):
        """AC-1: + New Ticket button is present inside .smgmt-header."""
        smgmt_start = html.find('class="smgmt-header"')
        smgmt_end = html.find("</div>", smgmt_start + len('class="smgmt-header"'))
        # Find the outer closing </div> of smgmt-header (accounts for nested divs)
        smgmt_block = html[smgmt_start:smgmt_start + 600]
        assert "+ New Ticket" in smgmt_block, \
            "+ New Ticket button must be inside .smgmt-header"

    def test_new_ticket_appears_before_new_sprint(self, html):
        """AC-1: + New Ticket button appears immediately before + New Sprint in the HTML."""
        ticket_idx = html.find("+ New Ticket")
        sprint_idx = html.find("+ New sprint")
        assert ticket_idx != -1, "+ New Ticket button not found in HTML"
        assert sprint_idx != -1, "+ New sprint button not found in HTML"
        assert ticket_idx < sprint_idx, \
            "+ New Ticket must appear before + New Sprint in smgmt-header"

    def test_both_buttons_in_smgmt_header_block(self, html):
        """AC-1: Both + New Ticket and + New Sprint are inside the same smgmt-header div."""
        smgmt_start = html.find('class="smgmt-header"')
        assert smgmt_start != -1, ".smgmt-header div not found"
        # Find the smgmt-header block (up to its matching close)
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        assert "+ New Ticket" in smgmt_block, \
            "+ New Ticket not found within .smgmt-header block"
        assert "+ New sprint" in smgmt_block, \
            "+ New sprint not found within .smgmt-header block"


# ---------------------------------------------------------------------------
# AC-2: Button visible only when Sprint Management tab is active
# ---------------------------------------------------------------------------

class TestAC2VisibilityScoped:
    def test_new_ticket_inside_pview_sprint_mgmt(self, html):
        """AC-2: + New Ticket button is inside #pview-sprint-mgmt (hidden when tab is inactive)."""
        pview_start = html.find('id="pview-sprint-mgmt"')
        assert pview_start != -1, "#pview-sprint-mgmt element not found"
        # The smgmt-header must be after this element
        smgmt_start = html.find('class="smgmt-header"', pview_start)
        assert smgmt_start != -1, ".smgmt-header not found inside #pview-sprint-mgmt"
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        assert "+ New Ticket" in smgmt_block, \
            "+ New Ticket must be scoped inside #pview-sprint-mgmt via smgmt-header"

    def test_pview_sprint_mgmt_has_hidden_class(self, html):
        """AC-2: #pview-sprint-mgmt starts hidden (so button is only visible on tab activation)."""
        assert 'id="pview-sprint-mgmt" class="hidden"' in html or \
               'class="hidden"' in html[html.find('id="pview-sprint-mgmt"'):html.find('id="pview-sprint-mgmt"') + 60], \
            "#pview-sprint-mgmt must have 'hidden' class by default"

    def test_new_sprint_button_also_in_pview(self, html):
        """AC-2: + New Sprint button is inside the same #pview-sprint-mgmt scope."""
        pview_start = html.find('id="pview-sprint-mgmt"')
        new_sprint_idx = html.find("+ New sprint", pview_start)
        assert new_sprint_idx != -1, \
            "+ New sprint button not found inside #pview-sprint-mgmt"


# ---------------------------------------------------------------------------
# AC-3: Clicking + New Ticket calls openDraftTicketModal()
# ---------------------------------------------------------------------------

class TestAC3OpenDraftTicketModal:
    def test_new_ticket_button_calls_open_draft_ticket_modal(self, html):
        """AC-3: + New Ticket button in smgmt-header has onclick='openDraftTicketModal()'."""
        smgmt_start = html.find('class="smgmt-header"')
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        assert "openDraftTicketModal()" in smgmt_block, \
            "+ New Ticket button in smgmt-header must call openDraftTicketModal()"

    def test_open_draft_ticket_modal_function_not_redefined(self, html):
        """AC-3: openDraftTicketModal function still exists (not removed or renamed)."""
        assert "openDraftTicketModal" in html, \
            "openDraftTicketModal function reference must still exist in index.html"

    def test_new_ticket_in_smgmt_uses_inline_onclick(self, html):
        """AC-3: + New Ticket button uses onclick attribute (same pattern as before)."""
        smgmt_start = html.find('class="smgmt-header"')
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        assert 'onclick="openDraftTicketModal()"' in smgmt_block, \
            "+ New Ticket button must use onclick attribute in smgmt-header"


# ---------------------------------------------------------------------------
# AC-4: + New Ticket removed from .header-right
# ---------------------------------------------------------------------------

class TestAC4RemovedFromHeaderRight:
    def test_new_ticket_not_in_header_right(self, html):
        """AC-4: + New Ticket button is absent from .header-right."""
        header_right_start = html.find('class="header-right"')
        assert header_right_start != -1, ".header-right section not found"
        # Find the end of header-right div
        header_right_block = html[header_right_start:header_right_start + 600]
        assert "+ New Ticket" not in header_right_block, \
            "+ New Ticket must be removed from .header-right"

    def test_open_draft_ticket_modal_not_called_from_header_right(self, html):
        """AC-4: openDraftTicketModal() is not called from within .header-right."""
        header_right_start = html.find('class="header-right"')
        header_right_block = html[header_right_start:header_right_start + 600]
        assert "openDraftTicketModal" not in header_right_block, \
            "openDraftTicketModal must not be called from .header-right"


# ---------------------------------------------------------------------------
# AC-5: Both buttons are visually consistent (padding, font-size, border-radius)
# ---------------------------------------------------------------------------

class TestAC5VisualConsistency:
    def test_new_ticket_uses_btn_secondary(self, html):
        """AC-5: + New Ticket in smgmt-header uses btn-secondary class."""
        smgmt_start = html.find('class="smgmt-header"')
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        # Find the new ticket button
        ticket_btn_match = re.search(
            r'<button[^>]*class="[^"]*btn-secondary[^"]*"[^>]*>\s*\+\s*New Ticket',
            smgmt_block,
        )
        assert ticket_btn_match is not None, \
            "+ New Ticket button in smgmt-header must use btn-secondary class"

    def test_new_sprint_uses_btn_primary(self, html):
        """AC-5: + New Sprint in smgmt-header uses btn-primary class."""
        smgmt_start = html.find('class="smgmt-header"')
        smgmt_block = html[smgmt_start:smgmt_start + 800]
        assert "btn-primary" in smgmt_block and "smgmt-new-sprint-btn" in smgmt_block, \
            "+ New Sprint button must use btn-primary class in smgmt-header"

    def test_btn_secondary_matching_padding_and_font_size(self, html):
        """AC-5: .btn-secondary CSS has same padding (6px 14px), font-size (12px), border-radius (6px) as .btn-primary."""
        btn_secondary_match = re.search(
            r"\.btn-secondary\s*\{([^}]+)\}",
            html,
            re.DOTALL,
        )
        assert btn_secondary_match is not None, ".btn-secondary CSS rule not found"
        secondary_css = btn_secondary_match.group(1)
        assert "padding: 6px 14px" in secondary_css, \
            ".btn-secondary padding must be 6px 14px (matching .btn-primary)"
        assert "font-size: 12px" in secondary_css, \
            ".btn-secondary font-size must be 12px (matching .btn-primary)"
        assert "border-radius: 6px" in secondary_css, \
            ".btn-secondary border-radius must be 6px (matching .btn-primary)"

    def test_btn_primary_matching_padding_and_font_size(self, html):
        """AC-5: .btn-primary CSS has padding 6px 14px, font-size 12px, border-radius 6px."""
        btn_primary_match = re.search(
            r"\.btn-primary\s*\{([^}]+)\}",
            html,
            re.DOTALL,
        )
        assert btn_primary_match is not None, ".btn-primary CSS rule not found"
        primary_css = btn_primary_match.group(1)
        assert "padding: 6px 14px" in primary_css, \
            ".btn-primary padding must be 6px 14px"
        assert "font-size: 12px" in primary_css, \
            ".btn-primary font-size must be 12px"
        assert "border-radius: 6px" in primary_css, \
            ".btn-primary border-radius must be 6px"


# ---------------------------------------------------------------------------
# AC-6: No regression on other header buttons
# ---------------------------------------------------------------------------

class TestAC6NoRegressionOnHeaderButtons:
    def test_filters_button_still_present(self, html):
        """AC-6: Filters button is still present in .header-right."""
        header_right_start = html.find('class="header-right"')
        header_right_block = html[header_right_start:header_right_start + 600]
        assert "Filters" in header_right_block, \
            "Filters button must still be present in .header-right"

    def test_new_project_button_still_present(self, html):
        """AC-6: + New Project button is still present in .header-right."""
        header_right_start = html.find('class="header-right"')
        header_right_block = html[header_right_start:header_right_start + 600]
        assert "+ New Project" in header_right_block, \
            "+ New Project button must still be present in .header-right"

    def test_refresh_button_still_present(self, html):
        """AC-6: Refresh button (btn-refresh) is still present in .header-right."""
        header_right_start = html.find('class="header-right"')
        header_right_block = html[header_right_start:header_right_start + 600]
        assert 'id="btn-refresh"' in header_right_block, \
            "Refresh button (#btn-refresh) must still be present in .header-right"

    def test_theme_toggle_still_present(self, html):
        """AC-6: Theme toggle button (theme-btn) is still present in .header-right."""
        header_right_start = html.find('class="header-right"')
        header_end = html.find("</header>", header_right_start)
        header_right_block = html[header_right_start:header_end]
        assert 'id="theme-btn"' in header_right_block, \
            "Theme toggle button (#theme-btn) must still be present in .header-right"

    def test_open_new_project_modal_still_called(self, html):
        """AC-6: + New Project button still calls openNewProjectModal()."""
        header_right_start = html.find('class="header-right"')
        header_right_block = html[header_right_start:header_right_start + 600]
        assert "openNewProjectModal()" in header_right_block, \
            "+ New Project button must still call openNewProjectModal() in .header-right"

    def test_header_right_button_count(self, html):
        """AC-6: .header-right contains exactly 4 interactive elements (Filters, New Project, Refresh, Theme)."""
        header_right_start = html.find('class="header-right"')
        # Find end of header element
        header_end = html.find("</header>", header_right_start)
        header_right_block = html[header_right_start:header_end]
        buttons = re.findall(r"<button\b", header_right_block)
        assert len(buttons) == 4, \
            f".header-right must have exactly 4 buttons (Filters, New Project, Refresh, Theme), found {len(buttons)}"

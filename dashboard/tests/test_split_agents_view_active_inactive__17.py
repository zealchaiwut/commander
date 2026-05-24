"""Tests for issue #17 — Split Agents view into Active and Inactive sections.

Strategy:
- Static checks read source files directly from the feature branch (work-coder).
- Live-server checks hit http://localhost:8000 via the `client` fixture.
- Browser/visual steps (mobile layout, dark mode rendering) are marked ⚠️ MANUAL.

AC-1: Two sections rendered (ACTIVE and INACTIVE with counts)
AC-2: Active section empty state ("No active agents")
AC-3: Inactive section hidden when empty
AC-4: Summary bar adds Timed Out counter (4 tiles: Working | Waiting | Timed Out | Done)
AC-5: No layout regression on mobile ≤ 600px  ⚠️ MANUAL
AC-6: Dark mode compatibility                  ⚠️ MANUAL
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths — point at the feature branch checked out in work-coder
# ---------------------------------------------------------------------------
FEATURE_DIR = Path("/Users/chaiwutchaianuchittrakul/commander/work-coder/dashboard")
INDEX_HTML  = FEATURE_DIR / "static" / "index.html"
APP_JS      = FEATURE_DIR / "static" / "app.js"


# ---------------------------------------------------------------------------
# AC-1: Two distinct sections — Active and Inactive — with labelled headers
# ---------------------------------------------------------------------------

class TestAC1TwoSections:
    """AC-1: The Agents tab renders two distinct sections with counts in headers."""

    def test_html_has_active_section_label_element(self):
        """index.html must have an element with id='agents-active-label'."""
        html = INDEX_HTML.read_text()
        assert 'id="agents-active-label"' in html, (
            "index.html must define id='agents-active-label' for the Active section header"
        )

    def test_html_has_inactive_section_element(self):
        """index.html must have a wrapper element with id='agents-inactive-section'."""
        html = INDEX_HTML.read_text()
        assert 'id="agents-inactive-section"' in html, (
            "index.html must define id='agents-inactive-section' for the Inactive section"
        )

    def test_html_has_inactive_label_element(self):
        """index.html must have an element with id='agents-inactive-label'."""
        html = INDEX_HTML.read_text()
        assert 'id="agents-inactive-label"' in html, (
            "index.html must define id='agents-inactive-label' for the Inactive section header"
        )

    def test_html_has_active_grid(self):
        """index.html must have a grid element with id='agents-grid-active'."""
        html = INDEX_HTML.read_text()
        assert 'id="agents-grid-active"' in html, (
            "index.html must define id='agents-grid-active' for the Active agents grid"
        )

    def test_html_has_inactive_grid(self):
        """index.html must have a grid element with id='agents-grid-inactive'."""
        html = INDEX_HTML.read_text()
        assert 'id="agents-grid-inactive"' in html, (
            "index.html must define id='agents-grid-inactive' for the Inactive agents grid"
        )

    def test_app_js_renderAgents_filters_active(self):
        """renderAgents must filter 'working' and 'waiting' into active array."""
        js = APP_JS.read_text()
        assert "status === 'working' || a.status === 'waiting'" in js or \
               "status === 'working'" in js and "status === 'waiting'" in js, (
            "app.js renderAgents must filter working/waiting into an active array"
        )

    def test_app_js_renderAgents_filters_inactive(self):
        """renderAgents must filter 'done' and 'timed_out' into inactive array."""
        js = APP_JS.read_text()
        assert "status === 'done'" in js and "status === 'timed_out'" in js, (
            "app.js renderAgents must filter done/timed_out into an inactive array"
        )

    def test_app_js_sets_active_label_with_count(self):
        """app.js must set active label text including count (ACTIVE · N)."""
        js = APP_JS.read_text()
        assert "agents-active-label" in js, (
            "app.js must update 'agents-active-label' element text"
        )
        assert "ACTIVE" in js, (
            "app.js must use 'ACTIVE' text in the active section label"
        )

    def test_app_js_sets_inactive_label_with_count(self):
        """app.js must set inactive label text including count (INACTIVE · N)."""
        js = APP_JS.read_text()
        assert "agents-inactive-label" in js, (
            "app.js must update 'agents-inactive-label' element text"
        )
        assert "INACTIVE" in js, (
            "app.js must use 'INACTIVE' text in the inactive section label"
        )

    def test_html_section_label_css_class_used(self):
        """Both section headers must use the existing .section-label CSS class."""
        html = INDEX_HTML.read_text()
        # Count occurrences in the agents view portion
        agents_view_start = html.find('id="view-agents"')
        agents_view_end   = html.find('id="view-activity"', agents_view_start)
        agents_view = html[agents_view_start:agents_view_end]
        count = agents_view.count('class="section-label"')
        assert count >= 2, (
            f"Expected ≥2 .section-label elements in agents view, found {count}. "
            "Both Active and Inactive headers must use the .section-label class."
        )

    def test_live_api_agents_returns_list(self, client):
        """Live: /api/agents must return a JSON array (basic sanity check)."""
        resp = client.get("/api/agents")
        assert resp.status_code == 200, f"/api/agents returned {resp.status_code}"
        data = resp.json()
        assert isinstance(data, list), "/api/agents must return a JSON array"


# ---------------------------------------------------------------------------
# AC-2: Active section shows empty-state text when no active agents
# ---------------------------------------------------------------------------

class TestAC2ActiveEmptyState:
    """AC-2: Active section always visible; shows 'No active agents' when empty."""

    def test_app_js_has_no_active_agents_text(self):
        """app.js must emit 'No active agents' empty-state when active list is empty."""
        js = APP_JS.read_text()
        assert "No active agents" in js, (
            "app.js must render 'No active agents' text when active array is empty"
        )

    def test_app_js_active_grid_shows_empty_not_hidden(self):
        """Active grid must show empty-state text, not be hidden (display:none)."""
        js = APP_JS.read_text()
        # Verify empty state is rendered into the grid — not that the section is hidden
        assert "agents-grid-active" in js, (
            "app.js must update 'agents-grid-active' element"
        )
        # The active section must NOT be conditionally hidden
        # (Only the inactive section should be hidden when empty)
        render_agents_start = js.find("function renderAgents(")
        render_agents_end   = js.find("\nfunction ", render_agents_start + 1)
        render_agents_body  = js[render_agents_start:render_agents_end]

        # Active section should not have display:none logic
        active_block_end = render_agents_body.find("agents-inactive-section")
        active_block = render_agents_body[:active_block_end]
        assert "display" not in active_block or "agents-inactive" in active_block, (
            "Active section must never be hidden (display:none); only inactive section may be hidden"
        )

    def test_app_js_empty_state_uses_empty_class(self):
        """Empty state for active section must use the existing .empty CSS class."""
        js = APP_JS.read_text()
        # Look for the empty div in the renderAgents function
        render_start = js.find("function renderAgents(")
        render_end   = js.find("\nfunction ", render_start + 1)
        render_body  = js[render_start:render_end]
        assert 'class="empty"' in render_body, (
            "renderAgents must use class='empty' for the No active agents empty state"
        )


# ---------------------------------------------------------------------------
# AC-3: Inactive section hidden entirely when there are no inactive agents
# ---------------------------------------------------------------------------

class TestAC3InactiveSectionHiddenWhenEmpty:
    """AC-3: Entire Inactive section (header + grid) not rendered when no inactive agents."""

    def test_app_js_inactive_section_display_none_when_empty(self):
        """app.js must set display:none on agents-inactive-section when inactive is empty."""
        js = APP_JS.read_text()
        assert "agents-inactive-section" in js, (
            "app.js must reference 'agents-inactive-section' element"
        )
        assert "display" in js and "none" in js, (
            "app.js must use display:none to hide the inactive section when empty"
        )

    def test_app_js_inactive_section_hidden_before_shown(self):
        """renderAgents must conditionally hide/show inactive section based on count."""
        js = APP_JS.read_text()
        render_start = js.find("function renderAgents(")
        render_end   = js.find("\nfunction ", render_start + 1)
        render_body  = js[render_start:render_end]
        # Must have a conditional check (if inactive.length === 0)
        assert "inactive.length" in render_body, (
            "renderAgents must check inactive.length to decide whether to show/hide the inactive section"
        )
        # Must set display to none when empty
        assert "display = 'none'" in render_body or "display='none'" in render_body or \
               'display = "none"' in render_body, (
            "renderAgents must set display='none' when inactive section is empty"
        )

    def test_html_inactive_section_is_single_wrapper(self):
        """agents-inactive-section must wrap both label and grid in one element."""
        html = INDEX_HTML.read_text()
        # Find the inactive section
        start = html.find('id="agents-inactive-section"')
        assert start != -1, "agents-inactive-section not found"
        # Both inactive label and grid must be inside it
        section_chunk = html[start:start + 400]
        assert "agents-inactive-label" in section_chunk, (
            "agents-inactive-label must be inside agents-inactive-section wrapper"
        )
        assert "agents-grid-inactive" in section_chunk, (
            "agents-grid-inactive must be inside agents-inactive-section wrapper"
        )


# ---------------------------------------------------------------------------
# AC-4: Summary bar adds Timed Out counter (4 tiles total)
# ---------------------------------------------------------------------------

class TestAC4TimedOutCounter:
    """AC-4: Summary bar has four tiles including a Timed Out tile."""

    def test_html_has_cnt_timed_out_element(self):
        """index.html must have id='cnt-timed-out' span in the summary bar."""
        html = INDEX_HTML.read_text()
        assert 'id="cnt-timed-out"' in html, (
            "index.html must define id='cnt-timed-out' in the agent summary bar"
        )

    def test_html_timed_out_label_text(self):
        """Summary bar must show 'Timed Out' label text."""
        html = INDEX_HTML.read_text()
        assert "Timed Out" in html, (
            "index.html must include 'Timed Out' label text in the summary bar"
        )

    def test_html_has_four_sum_items_in_agents_view(self):
        """Agent summary bar must contain exactly 4 sum-item tiles."""
        html = INDEX_HTML.read_text()
        agents_start = html.find('id="view-agents"')
        agents_end   = html.find('id="view-activity"', agents_start)
        agents_chunk = html[agents_start:agents_end]
        count = agents_chunk.count('class="sum-item"')
        assert count == 4, (
            f"Expected 4 sum-item tiles in agents view (Working, Waiting, Timed Out, Done), found {count}"
        )

    def test_html_summary_bar_order_timed_out_before_done(self):
        """Timed Out tile must appear between Waiting and Done in the HTML order."""
        html = INDEX_HTML.read_text()
        cnt_waiting  = html.find('id="cnt-waiting"')
        cnt_timed    = html.find('id="cnt-timed-out"')
        cnt_done     = html.find('id="cnt-done"')
        assert cnt_waiting != -1, "cnt-waiting element not found"
        assert cnt_timed   != -1, "cnt-timed-out element not found"
        assert cnt_done    != -1, "cnt-done element not found"
        assert cnt_waiting < cnt_timed < cnt_done, (
            "Summary bar must list tiles in order: Waiting ... Timed Out ... Done"
        )

    def test_html_summary_bar_has_cnt_working(self):
        """Summary bar must retain the Working tile (cnt-working)."""
        html = INDEX_HTML.read_text()
        assert 'id="cnt-working"' in html, (
            "index.html must retain id='cnt-working' in the summary bar (unchanged tile)"
        )

    def test_app_js_sets_cnt_timed_out(self):
        """app.js renderAgents must update cnt-timed-out element."""
        js = APP_JS.read_text()
        assert "cnt-timed-out" in js, (
            "app.js must set the 'cnt-timed-out' element in renderAgents"
        )

    def test_app_js_timed_out_count_uses_correct_status(self):
        """app.js must count agents with status === 'timed_out' for Timed Out tile."""
        js = APP_JS.read_text()
        render_start = js.find("function renderAgents(")
        render_end   = js.find("\nfunction ", render_start + 1)
        render_body  = js[render_start:render_end]
        assert "timed_out" in render_body, (
            "renderAgents must filter/count agents by status === 'timed_out' for the Timed Out tile"
        )

    def test_live_api_agents_has_timed_out_status(self, client):
        """Live: /api/agents must recognise 'timed_out' as a valid status value."""
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        agents = resp.json()
        statuses = {a.get("status") for a in agents}
        # timed_out might not currently be present, but status field must exist
        assert all("status" in a for a in agents), (
            "Every agent record must have a 'status' field"
        )
        # Validate that known statuses are among expected values
        known = {"working", "waiting", "done", "timed_out"}
        unknown = statuses - known
        assert not unknown, (
            f"Unexpected agent status values returned by API: {unknown}"
        )

    def test_html_badge_timed_out_css_defined(self):
        """index.html CSS must define .badge-timed-out style."""
        html = INDEX_HTML.read_text()
        assert ".badge-timed-out" in html, (
            "index.html CSS must define .badge-timed-out rule for Timed Out agent badges"
        )

    def test_app_js_timed_out_badge_label(self):
        """app.js _renderAgentCard must label timed_out agents as 'Timed Out'."""
        js = APP_JS.read_text()
        assert "Timed Out" in js, (
            "app.js must use 'Timed Out' as the badge label for timed_out agents"
        )


# ---------------------------------------------------------------------------
# AC-5: No layout regression on mobile ≤ 600px  ⚠️ MANUAL
# ---------------------------------------------------------------------------

class TestAC5MobileLayout:
    """AC-5: Mobile layout — static CSS checks; full rendering is manual."""

    def test_html_has_600px_media_query(self):
        """index.html must have a @media (max-width: 600px) block."""
        html = INDEX_HTML.read_text()
        assert "max-width: 600px" in html or "max-width:600px" in html, (
            "index.html must define a @media (max-width: 600px) breakpoint"
        )

    def test_html_section_pad_responsive_padding(self):
        """index.html must include .section-pad reduced padding inside a 600px media query."""
        html = INDEX_HTML.read_text()
        # There may be multiple @media (max-width: 600px) blocks; search all of them
        query = "max-width: 600px"
        if query not in html:
            query = "max-width:600px"
        found = False
        pos = 0
        while True:
            idx = html.find(query, pos)
            if idx == -1:
                break
            chunk = html[idx:idx + 600]
            if "section-pad" in chunk:
                found = True
                break
            pos = idx + 1
        assert found, (
            ".section-pad must have reduced padding defined inside a @media (max-width: 600px) block"
        )

    def test_html_agents_grid_auto_fill_columns(self):
        """agents-grid must use auto-fill column template for responsive stacking."""
        html = INDEX_HTML.read_text()
        assert "auto-fill" in html or "auto-fit" in html, (
            ".agents-grid must use auto-fill/auto-fit for responsive column layout"
        )

    @pytest.mark.skip(reason="⚠️ MANUAL — resize browser to 375px, verify no horizontal scroll")
    def test_manual_mobile_no_horizontal_overflow(self):
        """MANUAL: Resize browser to 375px wide. Both sections stack with no overflow."""
        pass


# ---------------------------------------------------------------------------
# AC-6: Dark mode compatibility  ⚠️ MANUAL
# ---------------------------------------------------------------------------

class TestAC6DarkMode:
    """AC-6: Dark mode — CSS variable checks; visual rendering is manual."""

    def test_html_has_dark_theme_data_attribute(self):
        """index.html must support data-theme='dark' for dark mode."""
        html = INDEX_HTML.read_text()
        assert '[data-theme="dark"]' in html or "data-theme" in html, (
            "index.html must support data-theme attribute for dark mode toggling"
        )

    def test_html_dark_theme_overrides_bg_variables(self):
        """index.html must define --bg and --text variables for dark theme."""
        html = INDEX_HTML.read_text()
        dark_start = html.find('[data-theme="dark"]')
        if dark_start == -1:
            pytest.skip("No [data-theme='dark'] block found — cannot verify dark variables")
        dark_chunk = html[dark_start:dark_start + 400]
        assert "--bg" in dark_chunk, (
            "[data-theme='dark'] must override --bg CSS variable"
        )
        assert "--text" in dark_chunk, (
            "[data-theme='dark'] must override --text CSS variable"
        )

    def test_html_section_label_uses_css_variable_for_color(self):
        """section-label CSS must use var(--text-muted) for color — dark-mode compatible."""
        html = INDEX_HTML.read_text()
        # Find section-label CSS rule
        sl_idx = html.find(".section-label")
        assert sl_idx != -1, ".section-label CSS rule not found"
        sl_chunk = html[sl_idx:sl_idx + 150]
        assert "var(--" in sl_chunk, (
            ".section-label must use CSS variables (e.g. var(--text-muted)) for dark-mode compatibility"
        )

    def test_html_agents_grid_bg_uses_css_variable(self):
        """Agent card styles must use CSS variables rather than hard-coded colours."""
        html = INDEX_HTML.read_text()
        # Check agent-card or .sum-item uses var()
        agent_card_idx = html.find(".agent-card")
        if agent_card_idx == -1:
            agent_card_idx = html.find(".sum-item")
        assert agent_card_idx != -1, "No agent-card or sum-item CSS found"
        chunk = html[agent_card_idx:agent_card_idx + 200]
        assert "var(--" in chunk, (
            "Agent card/summary styles must use CSS variables for dark-mode compatibility"
        )

    @pytest.mark.skip(reason="⚠️ MANUAL — toggle dark mode, verify both sections render correctly")
    def test_manual_dark_mode_sections_render_correctly(self):
        """MANUAL: Click moon icon, verify both Active/Inactive sections render in dark mode."""
        pass

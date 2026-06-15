"""Tests for issue #798 — Rename Sprint tab and introduce Board/Running/History sub-nav.

Acceptance criteria tested (each test class maps to one AC):

  AC1 — Tab label reads exactly "Sprint" (was "Sprint Mgmt")
  AC2 — Sub-nav renders three tabs in order: Board, Running, History, using the
        mock tokens `.crumb`, `.subnav`, `.subtab`
  AC3 — Running tab shows a pulsing live dot ONLY while >=1 sprint is running;
        absent otherwise
  AC4 — History tab label includes the total sprint count badge (e.g. "History 12")
  AC5 — Tag chips, search field, and size legend are absent from the Sprint pane
  AC6 — "New Sprint" and "Clean up empty" controls live in the sub-nav right slot
  AC7 — Deep links (?tab=sprint) continue to resolve to the Sprint pane
  AC8 — `npx impeccable detect` passes with zero violations (runs in CI/tester env)
  AC9 — No regressions in the Tickets pane (its own toolbar render is intact)

The dashboard frontend is no-build vanilla HTML/JS served from disk, so these
tests assert against the static source of `project.html` (the same approach as
test_716__sprint_history_subview.py).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _slice(html: str, start_marker: str, end_marker: str) -> str:
    idx = html.find(start_marker)
    assert idx >= 0, f"marker not found: {start_marker}"
    end = html.find(end_marker, idx)
    assert end > idx, f"end marker {end_marker} must come after {start_marker}"
    return html[idx:end]


def _pane_sprint_mgmt(html: str) -> str:
    """Inner HTML of the Sprint pane (up to the Tickets tab)."""
    return _slice(html, 'id="pane-sprint-mgmt"', "<!-- Tickets tab -->")


def _subnav(html: str) -> str:
    """Inner HTML of the new sub-nav strip."""
    return _slice(html, 'class="subnav"', "<!-- Board sub-view")


# ---------------------------------------------------------------------------
# AC1 — Tab label reads exactly "Sprint" (was "Sprint Mgmt")
# ---------------------------------------------------------------------------

class TestTabLabel:
    def test_top_nav_label_is_sprint(self, html):
        # The top-nav button still uses id stab-sprint-mgmt (internal id unchanged
        # so deep-links keep resolving) but its visible label must read "Sprint".
        m = re.search(r'<button[^>]*id="stab-sprint-mgmt".*?</button>', html, re.S)
        assert m, "stab-sprint-mgmt nav button must exist"
        btn = m.group(0)
        # Visible text is whatever follows the icon tag.
        label = re.sub(r"<[^>]+>", "", btn).strip()
        assert label == "Sprint", f'Tab label must read exactly "Sprint", got {label!r}'

    def test_no_sprint_mgmt_label_in_nav(self, html):
        m = re.search(r'<button[^>]*id="stab-sprint-mgmt".*?</button>', html, re.S)
        assert "Sprint Mgmt" not in m.group(0), \
            'Top-nav label must not read "Sprint Mgmt" anymore'


# ---------------------------------------------------------------------------
# AC2 — Sub-nav: Board, Running, History using .crumb / .subnav / .subtab
# ---------------------------------------------------------------------------

class TestSubNav:
    def test_subnav_present_in_pane(self, html):
        pane = _pane_sprint_mgmt(html)
        assert 'class="subnav"' in pane, \
            "Sprint pane must contain a .subnav strip (mock token)"

    def test_crumb_token_present(self, html):
        pane = _pane_sprint_mgmt(html)
        assert "crumb" in pane, "Sub-nav must use the .crumb mock token"
        assert re.search(r'class="[^"]*\bcrumb\b', pane), \
            "A .crumb element must be present in the sub-nav"

    def test_three_subtabs_present(self, html):
        for v in ("board", "running", "history"):
            assert f'id="smgmt-subtab-{v}"' in html, \
                f"Sub-tab smgmt-subtab-{v} must be present"

    def test_subtabs_use_subtab_class(self, html):
        for v in ("board", "running", "history"):
            m = re.search(rf'class="([^"]*)" id="smgmt-subtab-{v}"', html)
            assert m, f"smgmt-subtab-{v} must carry a class attribute"
            assert "subtab" in m.group(1).split(), \
                f"smgmt-subtab-{v} must use the .subtab mock token (got {m.group(1)!r})"

    def test_subtab_order_board_running_history(self, html):
        nav = _subnav(html)
        order = re.findall(r'id="smgmt-subtab-(\w+)"', nav)
        assert order == ["board", "running", "history"], \
            f"Sub-tabs must be Board, Running, History in order; got {order}"

    def test_subtab_labels_text(self, html):
        nav = _subnav(html)
        for label in ("Board", "Running", "History"):
            assert label in nav, f"Sub-nav must contain a '{label}' tab"

    def test_subtabs_call_show_subview(self, html):
        nav = _subnav(html)
        for v in ("board", "running", "history"):
            assert f"_smgmtShowSubView('{v}')" in nav, \
                f"{v} sub-tab must call _smgmtShowSubView('{v}')"

    def test_running_subview_container_exists(self, html):
        assert 'id="smgmt-subview-running"' in html, \
            "A Running sub-view container must exist so the tab is not blank"

    def test_show_subview_handles_running(self, html):
        idx = html.find("function _smgmtShowSubView")
        assert idx >= 0, "_smgmtShowSubView must be defined"
        body = html[idx:idx + 900]
        assert "'running'" in body, \
            "_smgmtShowSubView must include 'running' in its view list"


# ---------------------------------------------------------------------------
# AC3 — Running tab pulsing live dot only while a sprint is running
# ---------------------------------------------------------------------------

class TestRunningLiveDot:
    def test_running_dot_element_exists(self, html):
        assert 'id="smgmt-running-dot"' in html, \
            "Running sub-tab must contain a live-dot element (smgmt-running-dot)"

    def test_dot_lives_inside_running_subtab(self, html):
        # The dot markup must sit inside the Running sub-tab button.
        m = re.search(r'id="smgmt-subtab-running".*?</button>', html, re.S)
        assert m, "Running sub-tab button must exist"
        assert "smgmt-running-dot" in m.group(0), \
            "Live dot must be inside the Running sub-tab button"

    def test_dot_hidden_by_default(self, html):
        m = re.search(r'<span[^>]*id="smgmt-running-dot"[^>]*>', html)
        assert m, "smgmt-running-dot must be a span"
        assert "hidden" in m.group(0), \
            "Live dot must be hidden in static markup (absent until a sprint runs)"

    def test_dot_visibility_driven_by_running_state(self, html):
        # JS must toggle the dot based on _smgmtAnySprintRunning.
        assert "smgmt-running-dot" in html
        # Find the toggle logic referencing the running flag near the dot id.
        idx = html.find("function _smgmtUpdateSubnav")
        assert idx >= 0, "_smgmtUpdateSubnav must exist to drive the sub-nav state"
        body = html[idx:idx + 1200]
        assert "smgmt-running-dot" in body, \
            "_smgmtUpdateSubnav must reference the live dot"
        assert "_smgmtAnySprintRunning" in body, \
            "Live-dot visibility must be driven by _smgmtAnySprintRunning"

    def test_dot_has_pulse_animation(self, html):
        # A pulsing animation must be defined for the live dot.
        css = _slice(html, ".subtab-live-dot", "</style>")
        assert "animation" in css and "infinite" in css, \
            "Live dot must have an infinite pulse animation"


# ---------------------------------------------------------------------------
# AC4 — History tab badge is a NEEDS-ACTION count (notification semantics),
# not an inventory of every sprint: it counts only sprints awaiting the
# operator (ready_to_merge / needs_rework / failed) and is hidden at zero, so a
# history of all-completed sprints shows no badge.
# ---------------------------------------------------------------------------

class TestHistoryBadge:
    def test_history_badge_element_exists(self, html):
        assert 'id="smgmt-history-count"' in html, \
            "History sub-tab must contain a needs-action badge (smgmt-history-count)"

    def test_badge_lives_inside_history_subtab(self, html):
        m = re.search(r'id="smgmt-subtab-history".*?</button>', html, re.S)
        assert m, "History sub-tab button must exist"
        assert "smgmt-history-count" in m.group(0), \
            "Count badge must be inside the History sub-tab button"

    def test_badge_hidden_by_default(self, html):
        m = re.search(r'id="smgmt-history-count"[^>]*>', html)
        assert m and "hidden" in m.group(0), \
            "Badge must start hidden — nothing needs action until states load"

    def test_badge_populated_from_needs_action(self, html):
        idx = html.find("function _smgmtUpdateSubnav")
        assert idx >= 0, "_smgmtUpdateSubnav must exist"
        body = html[idx:idx + 1200]
        assert "smgmt-history-count" in body, \
            "_smgmtUpdateSubnav must populate the History count badge"
        assert "_histNeedsActionCount" in body, \
            "the badge count must come from the needs-action helper"
        assert "hidden" in body, "the badge must hide when the count is zero"

    def test_action_states_are_human_gated_only(self, html):
        idx = html.find("_HIST_ACTION_STATES")
        assert idx >= 0, "the needs-action state set must exist"
        decl = html[idx:idx + 200]
        for state in ("ready_to_merge", "needs_rework", "failed"):
            assert state in decl, f"{state} requires the operator and must count"
        # Passive / live states must NOT be in the action set.
        for state in ("completed", "deleted", "cancelled", "running"):
            assert f"'{state}'" not in decl, \
                f"{state} needs no action and must not inflate the badge"


# ---------------------------------------------------------------------------
# AC5 — Chips, search field, and size legend are absent from the Sprint pane
# ---------------------------------------------------------------------------

class TestControlsRemovedFromSprintPane:
    def test_label_filter_chips_absent(self, html):
        pane = _pane_sprint_mgmt(html)
        assert 'id="smgmt-label-filter-row"' not in pane, \
            "Tag/label filter chips must be removed from the Sprint pane"

    def test_search_field_absent(self, html):
        pane = _pane_sprint_mgmt(html)
        assert 'id="smgmt-filter-input"' not in pane, \
            "Search/filter field must be removed from the Sprint pane"
        assert 'id="smgmt-filter-bar"' not in pane, \
            "Search/filter bar must be removed from the Sprint pane"

    def test_size_legend_absent(self, html):
        pane = _pane_sprint_mgmt(html)
        assert 'id="smgmt-size-legend"' not in pane, \
            "Size legend must be removed from the Sprint pane"


# ---------------------------------------------------------------------------
# AC6 — New Sprint + Clean up empty relocated to the sub-nav right slot
# ---------------------------------------------------------------------------

class TestActionsRelocated:
    def test_new_sprint_in_subnav(self, html):
        nav = _subnav(html)
        assert 'id="smgmt-new-sprint-btn"' in nav, \
            "New Sprint button must live in the sub-nav"

    def test_cleanup_empty_in_subnav(self, html):
        nav = _subnav(html)
        assert 'id="smgmt-cleanup-empty-btn"' in nav, \
            "Clean up empty button must live in the sub-nav"

    def test_actions_in_right_slot(self, html):
        nav = _subnav(html)
        assert "subnav-actions" in nav, \
            "Relocated controls must sit in the .subnav-actions right slot"

    def test_buttons_keep_their_handlers(self, html):
        nav = _subnav(html)
        assert "smgmtOpenNewSprint()" in nav, \
            "New Sprint button must keep its smgmtOpenNewSprint() handler"
        assert "_smgmtCleanupEmpty()" in nav, \
            "Clean up empty button must keep its _smgmtCleanupEmpty() handler"


# ---------------------------------------------------------------------------
# AC7 — Deep link ?tab=sprint resolves to the Sprint pane
# ---------------------------------------------------------------------------

class TestDeepLink:
    def test_parseurl_resolves_sprint_alias(self, html):
        idx = html.find("function parseUrl")
        assert idx >= 0, "parseUrl must be defined"
        end = html.find("\n}", idx)
        body = html[idx:end]
        assert "'sprint'" in body, \
            "parseUrl must recognise the 'sprint' deep-link token"
        # It must resolve to the existing sprint-mgmt pane id.
        assert "sprint-mgmt" in body, \
            "parseUrl must map the 'sprint' token to the sprint-mgmt pane"

    def test_sprint_pane_id_unchanged(self, html):
        # The pane id stays pane-sprint-mgmt so existing links keep resolving.
        assert 'id="pane-sprint-mgmt"' in html, \
            "Sprint pane id must remain pane-sprint-mgmt for link compatibility"


# ---------------------------------------------------------------------------
# AC8 — impeccable detect passes (runs for real where node/npx is installed)
# ---------------------------------------------------------------------------

class TestImpeccable:
    def test_impeccable_detect_clean(self):
        npx = shutil.which("npx")
        if not npx:
            pytest.skip("npx/node not installed in this environment; "
                        "impeccable detect runs in the CI/tester environment")
        proc = subprocess.run(
            [npx, "impeccable", "detect", str(_PROJECT_HTML)],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "impeccable detect must pass with zero violations:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# AC9 — No regressions in the Tickets pane
# ---------------------------------------------------------------------------

class TestTicketsPaneIntact:
    def test_tickets_pane_exists(self, html):
        assert 'id="pane-tickets"' in html, "Tickets pane must remain"

    def test_tickets_toolbar_render_intact(self, html):
        # renderTickets still builds its own toolbar (count + Create Label).
        assert "tickets-toolbar" in html, \
            "Tickets pane toolbar render must be intact"
        assert "openCreateLabelModal()" in html, \
            "Tickets pane Create Label control must be intact"

    def test_load_tickets_defined(self, html):
        assert "async function loadTickets" in html, \
            "loadTickets loader must remain unchanged"

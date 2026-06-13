"""Tests for issue #800 — Filtered multi-select backlog panel with what-if delta.

Each test class maps to one acceptance criterion from the issue:

  AC1 — Backlog panel renders as a full-width bottom panel on the Board page;
        single-column, mobile-safe at <=720px with no horizontal scroll.
  AC2 — Filter pills for size, age, and unestimated operate client-side over
        existing backlog data; the active pill is visually distinct.
  AC3 — Each backlog row has a checkbox; selecting >=1 row reveals an
        "Add N selected to Sprint X" picker populated with valid target sprints.
  AC4 — Confirming the picker adds all selected tickets via the existing
        add-to-sprint / batch plumbing (no new engine).
  AC5 — What-if line updates live on selection: +minutes delta, new sprint
        total, and over-budget warning when sprint_budget is set; degrades to
        "no budget set" when the setting is absent.
  AC6 — If a preview-level endpoint exists, the what-if line also shows a level
        delta; if absent, that segment is omitted silently (no error state).
  AC7 — Drag-and-drop across the panel still works after multi-select UI added.
  AC8 — DOM targets follow the mock v5 contract: .panel, .bl-filter, .bl-grid,
        .bl-row, .bl-whatif; BA Path-A literal tokens honored.
  AC9 — impeccable detect gate passes (runs for real where npx is installed).

The dashboard frontend is no-build vanilla HTML/JS served from disk, so these
tests assert against the static source of project.html — the same approach as
test_798__sprint_tab_rename_and_subnav.py.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
_SERVER_PY = _REPO_ROOT / "apps" / "dashboard" / "server.py"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def server_py() -> str:
    assert _SERVER_PY.exists(), "server.py must exist"
    return _SERVER_PY.read_text(encoding="utf-8")


def _slice(html: str, start_marker: str, end_marker: str) -> str:
    idx = html.find(start_marker)
    assert idx >= 0, f"marker not found: {start_marker}"
    end = html.find(end_marker, idx)
    assert end > idx, f"end marker {end_marker} must come after {start_marker}"
    return html[idx:end]


def _board(html: str) -> str:
    """Inner HTML of the Board sub-view."""
    return _slice(html, 'id="smgmt-subview-board"', 'id="smgmt-subview-running"')


def _panel(html: str) -> str:
    """Inner HTML of the backlog panel (kept id smgmt-backlog-pane)."""
    return _slice(html, 'id="smgmt-backlog-pane"', "</div><!-- /smgmt-subview-board")


def _fn(html: str, name: str, span: int = 1600) -> str:
    """Return the source slice starting at a function definition."""
    idx = html.find(f"function {name}")
    assert idx >= 0, f"function {name} must be defined"
    return html[idx:idx + span]


# ---------------------------------------------------------------------------
# AC1 — Full-width bottom panel, single-column, mobile-safe <=720px
# ---------------------------------------------------------------------------

class TestPanelLayout:
    def test_panel_lives_at_bottom_of_board(self, html):
        board = _board(html)
        assert 'id="smgmt-backlog-pane"' in board, \
            "Backlog panel must live in the Board sub-view"
        # It must come after the sprint list (bottom of the board).
        assert board.index('id="smgmt-sprint-list"') < board.index('id="smgmt-backlog-pane"'), \
            "Backlog panel must render below the sprint list (bottom panel)"

    def test_panel_carries_panel_token(self, html):
        m = re.search(r'<div class="([^"]*)" id="smgmt-backlog-pane"', html)
        assert m, "smgmt-backlog-pane must be a div with a class attribute"
        classes = m.group(1).split()
        assert "panel" in classes, \
            "Backlog panel must carry the .panel mock token (got %r)" % m.group(1)

    def test_mobile_breakpoint_present(self, html):
        # A 720px breakpoint must exist and target backlog-panel tokens.
        assert "max-width: 720px" in html or "max-width:720px" in html, \
            "A <=720px mobile breakpoint must exist for the backlog panel"
        # The breakpoint block must reference a bl- token (single-column / wrap).
        blocks = re.findall(r'@media[^{]*max-width:\s*720px[^{]*\{(.*?)\n\s*\}', html, re.S)
        joined = "\n".join(blocks)
        assert "bl-" in joined, \
            "The 720px breakpoint must adapt the .bl-* backlog panel tokens"

    def test_grid_is_single_column(self, html):
        css = _slice(html, ".bl-grid", "}")
        # Single column: a flex column or an explicit single grid column.
        assert ("flex-direction: column" in css
                or "grid-template-columns: 1fr" in css
                or "grid-template-columns:1fr" in css), \
            ".bl-grid must stack rows in a single column"

    def test_filter_pills_wrap_on_mobile(self, html):
        css = _slice(html, ".bl-filter", "</style>")
        assert "flex-wrap: wrap" in css or "flex-wrap:wrap" in css, \
            ".bl-filter pills must wrap (no horizontal overflow on mobile)"

    def test_row_can_shrink_without_overflow(self, html):
        # Rows must allow their content to shrink (min-width:0) so long titles
        # truncate instead of forcing a horizontal scrollbar.
        css = _slice(html, ".bl-row", "</style>")
        assert "min-width: 0" in css or "min-width:0" in css, \
            ".bl-row must set min-width:0 so titles truncate (no horizontal scroll)"


# ---------------------------------------------------------------------------
# AC2 — Filter pills: size, age, unestimated; client-side; active distinct
# ---------------------------------------------------------------------------

class TestFilterPills:
    def test_filter_row_present(self, html):
        panel = _panel(html)
        assert 'class="bl-filter"' in panel or "bl-filter" in panel, \
            "Backlog panel must contain a .bl-filter pill row"

    def test_size_pills_present(self, html):
        panel = _panel(html)
        for size in ("S", "M", "L", "XL"):
            assert f'data-filter-val="{size}"' in panel, \
                f"A size filter pill for {size} must exist"
        assert 'data-filter-type="size"' in panel, \
            "Size pills must declare data-filter-type=\"size\""

    def test_age_filter_present(self, html):
        panel = _panel(html)
        assert 'data-filter-type="age"' in panel, \
            "An age filter pill group must exist"

    def test_unestimated_filter_present(self, html):
        panel = _panel(html)
        assert 'data-filter-type="unestimated"' in panel, \
            "An unestimated filter pill must exist"

    def test_filter_toggle_handler_exists(self, html):
        assert "function _blToggleFilter" in html, \
            "_blToggleFilter must drive client-side filter pill toggling"

    def test_filtering_is_client_side(self, html):
        # Filtering reads the already-loaded backlog tickets and re-renders;
        # it must NOT issue a network fetch for filtered data.
        fn = _fn(html, "_blApplyFilters", 1800)
        assert "fetch(" not in fn, \
            "_blApplyFilters must filter client-side (no server fetch)"
        # It uses the size resolution + age helpers over existing data.
        assert "_blTicketSize" in fn or "_sizeMinutes" in fn or "size" in fn, \
            "_blApplyFilters must filter on ticket size"

    def test_active_pill_visually_distinct(self, html):
        # An is-active modifier must restyle the pill with the accent token.
        css = _slice(html, ".bl-pill.is-active", "}")
        assert "var(--blue" in css, \
            "Active filter pill must be visually distinct using the blue accent token"

    def test_toggle_marks_active_state(self, html):
        fn = _fn(html, "_blToggleFilter", 1200)
        assert "is-active" in fn, \
            "_blToggleFilter must toggle the is-active state on the pill"


# ---------------------------------------------------------------------------
# AC3 — Checkbox per row; >=1 selected reveals the "Add N selected" picker
# ---------------------------------------------------------------------------

class TestMultiSelectPicker:
    def test_row_has_checkbox(self, html):
        fn = _fn(html, "_smgmtBacklogTicketHtml", 1600)
        assert 'type="checkbox"' in fn, "Each backlog row must carry a checkbox"
        assert "_smgmtToggleSelect" in fn, \
            "Row checkbox must drive the shared multi-select state"

    def test_picker_markup_present(self, html):
        panel = _panel(html)
        assert 'id="bl-actions"' in panel, \
            "Panel must contain the bl-actions bar housing the picker"
        assert 'id="bl-target"' in panel, \
            "Panel must contain the target-sprint picker (bl-target)"
        assert 'id="bl-add-btn"' in panel, \
            "Panel must contain the Add-selected confirm button (bl-add-btn)"

    def test_picker_literal_label(self, html):
        # BA Path-A literal: "Add N selected to Sprint X"
        assert re.search(r"Add\s+.*selected\s+to\s+Sprint", html), \
            'Picker must use the literal "Add N selected to Sprint X" label'

    def test_picker_hidden_until_selection(self, html):
        m = re.search(r'<div[^>]*id="bl-actions"[^>]*>', html)
        assert m, "bl-actions must be a div"
        assert "hidden" in m.group(0), \
            "bl-actions picker must be hidden until >=1 row is selected"

    def test_actions_revealed_on_selection(self, html):
        fn = _fn(html, "_blUpdateActions", 1600)
        assert "bl-actions" in fn and "hidden" in fn, \
            "_blUpdateActions must reveal/hide the picker based on selection count"

    def test_target_populated_with_valid_sprints(self, html):
        fn = _fn(html, "_blPopulateTarget", 1400)
        assert "_smgmtData" in fn and "sprints" in fn, \
            "_blPopulateTarget must populate the picker from the valid sprint list"

    def test_selection_drives_panel_update(self, html):
        # The shared selection UI must also refresh the panel actions/what-if.
        fn = _fn(html, "_smgmtUpdateSelectionUI", 1400)
        assert "_blUpdateActions" in fn, \
            "_smgmtUpdateSelectionUI must refresh the backlog panel actions"


# ---------------------------------------------------------------------------
# AC4 — Confirm adds via existing plumbing (no new engine)
# ---------------------------------------------------------------------------

class TestConfirmReusesPlumbing:
    def test_confirm_handler_exists(self, html):
        assert "function _blConfirmAdd" in html, \
            "_blConfirmAdd must handle the picker confirmation"

    def test_confirm_reuses_batch_move(self, html):
        fn = _fn(html, "_blConfirmAdd", 1400)
        # Reuse the existing batch move helper — no new fetch/endpoint here.
        assert "_smgmtMoveSelectedTo" in fn, \
            "_blConfirmAdd must reuse _smgmtMoveSelectedTo (existing plumbing)"
        assert "/api/" not in fn, \
            "_blConfirmAdd must not introduce a new add-to-sprint engine/endpoint"

    def test_existing_batch_endpoint_unchanged(self, html):
        # The reused engine still posts to the existing batch-labels endpoint.
        assert "/api/sprints/batch-labels" in html, \
            "Existing batch-labels endpoint must remain the add-to-sprint engine"


# ---------------------------------------------------------------------------
# AC5 — What-if line: delta, new total, over-budget / "no budget set"
# ---------------------------------------------------------------------------

class TestWhatIf:
    def test_whatif_element_present(self, html):
        panel = _panel(html)
        assert 'class="bl-whatif"' in panel or "bl-whatif" in panel, \
            "Panel must contain a .bl-whatif line"

    def test_whatif_updater_exists(self, html):
        assert "function _blUpdateWhatIf" in html, \
            "_blUpdateWhatIf must compute the what-if line"

    def test_whatif_uses_minutes_delta(self, html):
        fn = _fn(html, "_blUpdateWhatIf", 2400)
        assert "_sizeMinutes" in fn, \
            "What-if delta must be computed from per-ticket size minutes"

    def test_whatif_shows_new_total(self, html):
        fn = _fn(html, "_blUpdateWhatIf", 2400)
        # Adds the target sprint's current total to the selection delta.
        assert "total" in fn.lower(), \
            "What-if must surface the new sprint total"

    def test_whatif_reads_sprint_budget_setting(self, html):
        fn = _fn(html, "_blSprintBudgetMins", 800)
        assert "sprintBudget" in fn or "sprint_budget" in fn, \
            "sprint_budget must be read from the project setting"

    def test_whatif_over_budget_warning(self, html):
        fn = _fn(html, "_blUpdateWhatIf", 2400)
        assert "over budget" in fn.lower(), \
            'What-if must show an over-budget warning literal'

    def test_whatif_degrades_no_budget(self, html):
        fn = _fn(html, "_blUpdateWhatIf", 2400)
        assert "no budget set" in fn, \
            'What-if must degrade to "no budget set" when sprint_budget is absent'

    def test_whatif_updates_live_on_selection(self, html):
        # _blUpdateActions (run on every selection change) must refresh what-if.
        fn = _fn(html, "_blUpdateActions", 1600)
        assert "_blUpdateWhatIf" in fn, \
            "What-if must update live whenever the selection changes"


# ---------------------------------------------------------------------------
# AC6 — Preview-level delta: shown if endpoint exists, omitted silently if not
# ---------------------------------------------------------------------------

class TestPreviewLevel:
    def test_level_probe_exists(self, html):
        assert "function _blMaybeLevelDelta" in html, \
            "_blMaybeLevelDelta must feature-detect the preview-level endpoint"

    def test_level_probe_is_silent_on_absence(self, html):
        fn = _fn(html, "_blMaybeLevelDelta", 1600)
        # Must swallow failure (catch) and must not surface any error UI/state.
        assert "catch" in fn, \
            "_blMaybeLevelDelta must catch failures (silent on absent endpoint)"
        assert "error" not in fn.lower() or "no error" in fn.lower(), \
            "Absent preview-level endpoint must not produce an error state"

    def test_level_probe_targets_preview_endpoint(self, html):
        fn = _fn(html, "_blMaybeLevelDelta", 1600)
        assert "preview" in fn and "level" in fn, \
            "_blMaybeLevelDelta must probe a preview-level endpoint"


# ---------------------------------------------------------------------------
# AC7 — Drag-and-drop across the panel still works
# ---------------------------------------------------------------------------

# Drag-and-drop was removed from the board (operator decision): tickets move
# via the row ⋯ menu → "Move to" and the multi-select bar. The ticket builders
# now live in static/src/sprint-board/board-render.js (issue #797 extraction).
_BOARD_RENDER = (
    Path(__file__).resolve().parent.parent
    / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")


class TestDragRemoved:
    def test_rows_not_draggable(self):
        assert 'draggable="true"' not in _BOARD_RENDER, \
            "Drag-and-drop was removed — no ticket row may be draggable"
        assert "_smgmtBacklogTicketDragStart" not in _BOARD_RENDER, \
            "Backlog dragstart handler must be gone"

    def test_rows_carry_bl_row_token(self):
        assert "bl-row" in _BOARD_RENDER, \
            "Backlog rows must still carry the .bl-row mock token"

    def test_sprint_card_has_no_drop_handler(self):
        assert "_smgmtDropOnSprint(event" not in _BOARD_RENDER, \
            "Sprint cards must no longer wire the drop handler"

    def test_move_via_row_menu_available(self):
        assert "_smgmtRowMenuOpen" in _BOARD_RENDER, \
            "Tickets must keep the ⋯ row menu (Move to) as the move affordance"


# ---------------------------------------------------------------------------
# AC8 — Mock v5 DOM contract + BA Path-A literal tokens
# ---------------------------------------------------------------------------

class TestDomContract:
    def test_all_contract_tokens_present(self, html):
        for token in (".panel", ".bl-filter", ".bl-grid", ".bl-row", ".bl-whatif"):
            cls = token.lstrip(".")
            assert cls in html, f"Mock v5 contract token {token} must be present"

    def test_grid_token_on_container(self, html):
        m = re.search(r'<div id="smgmt-backlog-tickets"[^>]*class="([^"]*)"', html) \
            or re.search(r'<div class="([^"]*)" id="smgmt-backlog-tickets"', html)
        assert m, "smgmt-backlog-tickets container must carry a class attribute"
        assert "bl-grid" in m.group(1).split(), \
            "The backlog tickets container must carry the .bl-grid token"

    def test_created_at_exposed_for_age_filter(self, server_py):
        # Age filtering needs a creation timestamp on each issue. It must be
        # exposed by the sprint-management issues serialization.
        block = server_py[server_py.find('"/api/sprint-management/issues"'):]
        block = block[:block.find("def ", 200)]
        assert "created_at" in block, \
            "Issue serialization must expose created_at for the age filter"


# ---------------------------------------------------------------------------
# AC9 — impeccable detect passes (real run where npx is installed)
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

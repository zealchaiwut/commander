"""Tester AC coverage for issue #800 — Filtered multi-select backlog panel with what-if delta.

Independent tester verification, one (or two) checks per acceptance criterion.

The dashboard frontend is a no-build vanilla HTML/JS page served from disk, so —
consistent with the repo convention (see test_798__*) — these tests assert
against the static source of project.html and the server.py serialization that
backs the new age filter. The live UAT server at $UAT_BASE_URL currently serves
an unrelated branch, so static-source verification of the merged-to-be code is
the faithful gate here; AC9 (impeccable detect) requires node/npx and is skipped
where that toolchain is absent (it runs in the CI/tester environment).
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


def _panel(html: str) -> str:
    start = html.find('id="smgmt-backlog-pane"')
    assert start >= 0, "backlog panel must exist"
    end = html.find("</div><!-- /smgmt-subview-board", start)
    assert end > start, "backlog panel end marker must exist"
    return html[start:end]


def _fn(html: str, name: str, span: int = 2400) -> str:
    idx = html.find(f"function {name}")
    assert idx >= 0, f"function {name} must be defined"
    return html[idx:idx + span]


# AC1 — full-width bottom panel, single-column, mobile-safe <=720px
def test_backlog_multiselect_whatif__panel_is_full_width_bottom_panel(html):
    panel_tag = re.search(r'<div class="([^"]*)" id="smgmt-backlog-pane"', html)
    assert panel_tag and "panel" in panel_tag.group(1).split(), \
        "Backlog panel must carry the .panel token"
    # It must sit below the sprint list (bottom of the Board view).
    assert html.index('id="smgmt-sprint-list"') < html.index('id="smgmt-backlog-pane"'), \
        "Panel must render below the sprint list"


def test_backlog_multiselect_whatif__mobile_safe_720(html):
    assert "max-width: 720px" in html, "720px mobile breakpoint must exist"
    grid_css = html[html.find(".bl-grid"):html.find(".bl-grid") + 400]
    assert "1fr" in grid_css or "flex-direction: column" in grid_css, \
        ".bl-grid must be single-column"


# AC2 — filter pills (size/age/unestimated), client-side, active distinct
def test_backlog_multiselect_whatif__filter_pills_present(html):
    panel = _panel(html)
    for ftype in ("size", "age", "unestimated"):
        assert f'data-filter-type="{ftype}"' in panel, f"{ftype} filter pill must exist"


def test_backlog_multiselect_whatif__filtering_client_side_and_active_state(html):
    fn = _fn(html, "_blApplyFilters")
    assert "fetch(" not in fn, "filtering must be client-side (no server fetch)"
    toggle = _fn(html, "_blToggleFilter")
    assert "is-active" in toggle, "active pill must be marked is-active"
    css = html[html.find(".bl-pill.is-active"):html.find(".bl-pill.is-active") + 200]
    assert "var(--blue" in css, "active pill must be visually distinct"


# AC3 — checkbox per row; >=1 selected reveals "Add N selected to Sprint X" picker
def test_backlog_multiselect_whatif__row_checkbox_and_picker(html):
    row = _fn(html, "_smgmtBacklogTicketHtml")
    assert 'type="checkbox"' in row, "each backlog row must have a checkbox"
    panel = _panel(html)
    for el in ('id="bl-actions"', 'id="bl-target"', 'id="bl-add-btn"'):
        assert el in panel, f"picker element {el} must exist"
    assert re.search(r"Add\s+.*selected\s+to\s+Sprint", html), \
        'picker must use the "Add N selected to Sprint X" literal'


def test_backlog_multiselect_whatif__picker_hidden_until_selection(html):
    tag = re.search(r'<div[^>]*id="bl-actions"[^>]*>', html)
    assert tag and "hidden" in tag.group(0), "picker must be hidden until selection"
    upd = _fn(html, "_blUpdateActions")
    assert "bl-actions" in upd and "hidden" in upd, \
        "_blUpdateActions must toggle picker visibility by selection count"


# AC4 — confirm reuses existing add-to-sprint plumbing (no new engine)
def test_backlog_multiselect_whatif__confirm_reuses_existing_plumbing(html):
    fn = _fn(html, "_blConfirmAdd")
    assert "_smgmtMoveSelectedTo" in fn, "_blConfirmAdd must reuse existing batch move"
    assert "/api/" not in fn, "_blConfirmAdd must not introduce a new endpoint"
    assert "/api/sprints/batch-labels" in html, "existing batch endpoint must remain"


# AC5 — what-if: +minutes delta, new total, over-budget / "no budget set"
def test_backlog_multiselect_whatif__whatif_delta_total_budget(html):
    panel = _panel(html)
    assert "bl-whatif" in panel, "panel must contain a .bl-whatif line"
    fn = _fn(html, "_blUpdateWhatIf")
    assert "_sizeMinutes" in fn, "delta must be computed from per-ticket minutes"
    assert "total" in fn.lower(), "what-if must show new sprint total"
    assert "over budget" in fn.lower(), "over-budget warning literal must exist"
    assert "no budget set" in fn, 'must degrade to "no budget set" when absent'


def test_backlog_multiselect_whatif__whatif_updates_live_on_selection(html):
    fn = _fn(html, "_blUpdateActions")
    assert "_blUpdateWhatIf" in fn, "what-if must refresh on every selection change"


# AC6 — preview-level delta: shown if endpoint exists, silently omitted if absent
def test_backlog_multiselect_whatif__preview_level_silent_when_absent(html):
    fn = _fn(html, "_blMaybeLevelDelta")
    assert "preview" in fn and "level" in fn, "must probe a preview-level endpoint"
    assert "catch" in fn, "must catch failures and stay silent when endpoint absent"


# AC7 — drag-and-drop still works after multi-select UI
def test_backlog_multiselect_whatif__drag_and_drop_preserved(html):
    row = _fn(html, "_smgmtBacklogTicketHtml")
    assert 'draggable="true"' in row, "rows must remain draggable"
    assert "_smgmtBacklogTicketDragStart" in row, "dragstart handler must remain"
    assert "function _smgmtDropOnSprint" in html, "sprint-column drop must remain"
    panel_open = html[html.find('id="smgmt-backlog-pane"'):html.find('id="smgmt-backlog-pane"') + 400]
    assert "_smgmtDropOnBacklog" in panel_open, "panel must remain a drop target"


# AC8 — mock v5 DOM contract + age serialization that backs the age filter
def test_backlog_multiselect_whatif__dom_contract_tokens(html):
    for token in ("panel", "bl-filter", "bl-grid", "bl-row", "bl-whatif"):
        assert token in html, f"mock v5 contract token .{token} must be present"


def test_backlog_multiselect_whatif__created_at_exposed_for_age_filter(server_py):
    block = server_py[server_py.find('"/api/sprint-management/issues"'):]
    block = block[:block.find("def ", 200)]
    assert "created_at" in block, "issue serialization must expose created_at for age filter"


# AC9 — impeccable detect gate (runs where node/npx is installed)
def test_backlog_multiselect_whatif__impeccable_detect_clean():
    npx = shutil.which("npx")
    if not npx:
        pytest.skip("manual — node/npx not installed; impeccable detect runs in CI/tester env")
    proc = subprocess.run(
        [npx, "impeccable", "detect", str(_PROJECT_HTML)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"impeccable detect must pass:\n{proc.stdout}\n{proc.stderr}"

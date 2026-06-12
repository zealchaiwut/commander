"""Tester AC coverage for issue #798 — Rename Sprint tab and introduce
Board/Running/History sub-nav.

The dashboard frontend is no-build vanilla HTML/JS served verbatim from disk
(`apps/dashboard/static/project.html`). The UAT server serves that exact file,
so — following the established precedent of test_716__sprint_history_subview.py
— these tests assert against the static source on the feature branch. After
finish_feature merges, the same bytes are what UAT serves.

One test function per acceptance criterion (Risk: MEDIUM → up to 2 tests/criterion).
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


def _slice(html: str, start: str, end: str) -> str:
    i = html.find(start)
    assert i >= 0, f"marker not found: {start}"
    j = html.find(end, i)
    assert j > i, f"end marker {end} must follow {start}"
    return html[i:j]


def _sprint_pane(html: str) -> str:
    return _slice(html, 'id="pane-sprint-mgmt"', "<!-- Tickets tab -->")


def _tickets_pane(html: str) -> str:
    # Tickets pane begins at its marker and runs to the next top-level pane.
    return _slice(html, "<!-- Tickets tab -->", 'id="pane-logs"')


def _subnav(html: str) -> str:
    return _slice(html, 'class="subnav"', "<!-- Board sub-view")


# AC1 — Tab label reads exactly "Sprint" (was "Sprint Mgmt")
def test_sprint_tab_rename_and_subnav__tab_label_is_sprint(html):
    m = re.search(r'<button[^>]*id="stab-sprint-mgmt".*?</button>', html, re.S)
    assert m, "stab-sprint-mgmt nav button must exist"
    label = re.sub(r"<[^>]+>", "", m.group(0)).strip()
    assert label == "Sprint", f'Tab label must read exactly "Sprint", got {label!r}'
    assert "Sprint Mgmt" not in m.group(0), 'Nav label must not read "Sprint Mgmt"'


# AC2 — Sub-nav: Board, Running, History in order using .crumb/.subnav/.subtab
def test_sprint_tab_rename_and_subnav__three_subtabs_in_order(html):
    pane = _sprint_pane(html)
    assert 'class="subnav"' in pane, "Sprint pane must contain a .subnav strip"
    assert re.search(r'class="[^"]*\bcrumb\b', pane), "Sub-nav must use the .crumb token"
    nav = _subnav(html)
    order = re.findall(r'id="smgmt-subtab-(\w+)"', nav)
    assert order == ["board", "running", "history"], \
        f"Sub-tabs must be Board, Running, History in order; got {order}"


def test_sprint_tab_rename_and_subnav__subtabs_use_subtab_token(html):
    for v in ("board", "running", "history"):
        m = re.search(rf'class="([^"]*)" id="smgmt-subtab-{v}"', html)
        assert m and "subtab" in m.group(1).split(), \
            f"smgmt-subtab-{v} must use the .subtab mock token"


# AC3 — Running tab pulsing live dot only while a sprint is running
def test_sprint_tab_rename_and_subnav__live_dot_conditional(html):
    m = re.search(r'id="smgmt-subtab-running".*?</button>', html, re.S)
    assert m and "smgmt-running-dot" in m.group(0), \
        "Live dot must live inside the Running sub-tab"
    dot = re.search(r'<span[^>]*id="smgmt-running-dot"[^>]*>', html)
    assert dot and "hidden" in dot.group(0), \
        "Live dot must be hidden in static markup (absent until a sprint runs)"
    upd = _slice(html, "function _smgmtUpdateSubnav", "async function _smgmtLoadSummaries")
    assert "smgmt-running-dot" in upd and "_smgmtAnySprintRunning" in upd, \
        "Dot visibility must be driven by _smgmtAnySprintRunning in _smgmtUpdateSubnav"


def test_sprint_tab_rename_and_subnav__dot_has_pulse_animation(html):
    css = _slice(html, ".subtab-live-dot", "</style>")
    assert "animation" in css and "infinite" in css, \
        "Live dot must have an infinite pulse animation"


# AC4 — History tab badge shows the total sprint count
def test_sprint_tab_rename_and_subnav__history_count_badge(html):
    m = re.search(r'id="smgmt-subtab-history".*?</button>', html, re.S)
    assert m and "smgmt-history-count" in m.group(0), \
        "Count badge must live inside the History sub-tab"
    upd = _slice(html, "function _smgmtUpdateSubnav", "async function _smgmtLoadSummaries")
    assert "smgmt-history-count" in upd and "textContent" in upd, \
        "_smgmtUpdateSubnav must populate the History count badge from the sprint total"


# AC5 — Chips/search/legend absent from Sprint pane, still present in Tickets pane
def test_sprint_tab_rename_and_subnav__controls_absent_from_sprint_pane(html):
    pane = _sprint_pane(html)
    assert 'id="smgmt-label-filter-row"' not in pane, "Label chips must be gone from Sprint pane"
    assert 'id="smgmt-filter-input"' not in pane, "Search field must be gone from Sprint pane"
    assert 'id="smgmt-filter-bar"' not in pane, "Filter bar must be gone from Sprint pane"
    assert 'id="smgmt-size-legend"' not in pane, "Size legend must be gone from Sprint pane"


def test_sprint_tab_rename_and_subnav__tickets_pane_retains_filters(html):
    # The Tickets pane is lazy-rendered by renderTickets() into #tickets-content,
    # so its filtering lives in JS, not the static shell. AC5's "still present in
    # the Tickets pane" is a no-regression guarantee: this change only removed the
    # Sprint-pane controls. Verify the Tickets render path is intact and that the
    # relocated Sprint-pane control ids did not bleed into the Tickets renderer.
    assert 'id="pane-tickets"' in html, "Tickets pane shell must remain"
    render = _slice(html, "function renderTickets", "\nfunction ")
    assert "tickets-content" in render, "renderTickets must build the Tickets pane"
    assert "t.status" in render and "filter(" in render, \
        "Tickets pane must retain its status grouping/filtering logic"
    for stray in ("smgmt-label-filter-row", "smgmt-filter-input", "smgmt-size-legend"):
        assert stray not in render, \
            f"Relocated Sprint-pane control {stray} must not appear in renderTickets"


# AC6 — New Sprint + Clean up empty relocated to sub-nav right slot
def test_sprint_tab_rename_and_subnav__actions_relocated_to_subnav(html):
    nav = _subnav(html)
    assert "subnav-actions" in nav, "Relocated controls must sit in the .subnav-actions slot"
    assert 'id="smgmt-new-sprint-btn"' in nav and "smgmtOpenNewSprint()" in nav, \
        "New Sprint button (with handler) must live in the sub-nav"
    assert 'id="smgmt-cleanup-empty-btn"' in nav and "_smgmtCleanupEmpty()" in nav, \
        "Clean up empty button (with handler) must live in the sub-nav"


# AC7 — Deep link ?tab=sprint resolves to the Sprint pane
def test_sprint_tab_rename_and_subnav__deep_link_sprint_alias(html):
    body = _slice(html, "function parseUrl", "\n}")
    assert "'sprint'" in body and "sprint-mgmt" in body, \
        "parseUrl must map the 'sprint' deep-link token to the sprint-mgmt pane"
    assert 'id="pane-sprint-mgmt"' in html, \
        "Sprint pane id must remain pane-sprint-mgmt for link compatibility"


# AC8 — impeccable detect passes (runs where node/npx is installed)
def test_sprint_tab_rename_and_subnav__impeccable_detect_clean():
    npx = shutil.which("npx")
    if not npx:
        pytest.skip("npx/node not installed in this environment — impeccable detect "
                    "runs where node is available")
    proc = subprocess.run(
        [npx, "impeccable", "detect", str(_PROJECT_HTML)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert proc.returncode == 0, \
        f"impeccable detect must pass:\n{proc.stdout}\n{proc.stderr}"


# AC9 — No regressions in the Tickets pane
def test_sprint_tab_rename_and_subnav__tickets_pane_intact(html):
    assert 'id="pane-tickets"' in html, "Tickets pane must remain"
    assert "async function loadTickets" in html, "loadTickets loader must remain"
    assert "openCreateLabelModal()" in html, "Tickets pane Create Label control must remain"

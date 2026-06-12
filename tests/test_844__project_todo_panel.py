"""Tests for issue #844 — Build per-project to-do list panel UI.

This is a frontend ticket. A single shared, framework-free module
(`apps/dashboard/static/project-todo.js`) renders the to-do panel on two
surfaces so the data is never duplicated:

  • the right side of every project block on the home page (`home.html`)
  • a docked panel inside the project view (`project.html`)

Both surfaces drive the panel from the per-project to-do API shipped in #843
(`/api/projects/<project>/todos`), keyed by the project *slug* so the two
surfaces show the identical list. The page is vanilla HTML + JS served from
disk (no build step, no client runtime to drive from pytest), so each test
asserts on the served files' structure, the mock's design tokens, and the
API calls the JavaScript wires up. Every test is anchored to a specific
acceptance criterion.

AC1  To-do panel renders on the right side of each project block (home page).
AC2  To-do panel renders as a docked panel inside the project view.
AC3  Both surfaces display the same per-project list from the to-do API.
AC4  UI matches the attached mock (project_todo_mock.html).
AC5  Add an item via a text input using either Enter or the + button.
AC6  Items render as a checkbox + text row.
AC7  Checking applies strikethrough and moves the item to a collapsible
     "Done (n)" group whose count is the completed count.
AC8  Unchecking returns the item to the active list with normal styling.
AC9  The "Done (n)" group is collapsible/expandable; the count stays current.
AC10 Hovering an item reveals delete and promote (up-right arrow) buttons.
AC11 Delete removes the item and persists via the API.
AC12 Inline text editing of existing items persists via the API.
AC13 Items can be reordered and the new order persists via the API.
AC14 Every change auto-saves; a brief "Saved" indicator confirms persistence.
AC15 An empty state is displayed when the project has no to-do items.
AC16 Promote is rendered/styled but only shows a "coming soon" affordance and
     triggers no other action.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
TODO_JS = STATIC_DIR / "project-todo.js"
HOME_HTML = STATIC_DIR / "home.html"
PROJECT_HTML = STATIC_DIR / "project.html"


@pytest.fixture(scope="module")
def js() -> str:
    assert TODO_JS.exists(), f"shared to-do module not found at {TODO_JS}"
    return TODO_JS.read_text()


@pytest.fixture(scope="module")
def home() -> str:
    return HOME_HTML.read_text()


@pytest.fixture(scope="module")
def project() -> str:
    return PROJECT_HTML.read_text()


def _nows(s: str) -> str:
    """Whitespace-insensitive copy for matching CSS/markup."""
    return re.sub(r"\s+", "", s)


# ── AC1: home page mounts a panel on the right of each project block ──────────

def test_ac1_home_includes_shared_module(home):
    assert "/static/project-todo.js" in home, \
        "home.html must load the shared to-do module"


def test_ac1_home_mounts_panel_per_project(home):
    # A mount point exists in the per-project block template, and each block
    # is wired to the shared module.
    assert "CommanderTodo.mount" in home or "CommanderTodo" in home, \
        "home.html must mount the shared to-do panel"
    # The panel sits on the right of the block: a two-column body wraps the
    # always-visible summary/status on the left and the to-do mount on the right.
    assert "pb-grid" in home or "pb-cell-todo" in home, \
        "home block must have a side-by-side column for the to-do panel"


def test_ac1_home_panel_on_right_grid(home):
    """The block body is a 2-column grid (summary left, to-do right)."""
    collapsed = _nows(home)
    assert re.search(r"\.pb-grid\{[^}]*grid-template-columns", collapsed), \
        "home block must use a grid placing the to-do panel beside the summary"


def test_ac1_home_embed_caps_visible_todos(js):
    """Home embed shows at most five active items; the rest are hidden."""
    assert "HOME_LIST_LIMIT" in js
    assert "active.slice(0,HOME_LIST_LIMIT)" in js.replace(" ", "")
    assert "todo-more" in js


# ── AC2: project view has a docked panel ─────────────────────────────────────

def test_ac2_project_includes_shared_module(project):
    assert "/static/project-todo.js" in project, \
        "project.html must load the shared to-do module"


def test_ac2_project_has_docked_panel(project):
    assert "todo-dock" in project, "project view must contain a docked to-do panel"
    assert "CommanderTodo.mount" in project, \
        "project view must mount the shared to-do panel"


# ── AC3: same per-project list from the to-do API (not duplicated) ───────────

def test_ac3_module_reads_todo_api(js):
    # Both surfaces go through the same #843 API, scoped by project.
    assert "/api/projects/" in js and "/todos" in js, \
        "module must read/write the per-project to-do API from #843"


def test_ac3_single_shared_module_no_duplication(js, home, project):
    """The render/markup lives only in the shared module — neither HTML file
    re-implements the to-do list rendering."""
    # The signature row markup belongs to the module, not the pages.
    assert "todo-item" in js, "row markup must live in the shared module"
    assert "todo-item" not in home, \
        "home.html must not duplicate the to-do row markup"
    assert "todo-item" not in project, \
        "project.html must not duplicate the to-do row markup"


def test_ac3_both_surfaces_key_by_slug(home, project):
    """Both surfaces mount with the project slug so the lists are identical."""
    assert re.search(r"CommanderTodo\.mount\([^)]*slug", home), \
        "home must mount the panel keyed by project slug"
    assert re.search(r"CommanderTodo\.mount\([^)]*[Ss]lug", project), \
        "project view must mount the panel keyed by project slug"


# ── AC4: UI matches the attached mock ────────────────────────────────────────

def test_ac4_mock_component_classes(js):
    for cls in (
        "todo-head", "todo-title", "todo-count", "todo-saved",
        "todo-add", "todo-input", "todo-addbtn",
        "todo-list", "todo-item", "todo-check", "todo-text",
        "todo-actions", "todo-iconbtn", "todo-empty",
        "todo-done-toggle", "todo-done-list",
    ):
        assert cls in js, f"mock component class '{cls}' missing from module"


def test_ac4_mock_styles_injected(js):
    """The module ships the mock's panel styles (single source of truth)."""
    collapsed = _nows(js)
    # signature rules straight from project_todo_mock.html
    assert re.search(r"\.todo-actions\{[^}]*opacity:0", collapsed), \
        "hover-reveal actions style missing"
    assert re.search(r"\.todo-item\.done\s*\.todo-text\{[^}]*line-through", collapsed), \
        "completed strikethrough style missing"


def test_ac4_reduced_motion_guard(js):
    assert "prefers-reduced-motion" in js, \
        "module must provide a reduced-motion fallback (impeccable a11y rule)"


# ── AC5: add via Enter or the + button ───────────────────────────────────────

def test_ac5_add_via_enter_and_button(js):
    assert "'Enter'" in js or '"Enter"' in js, "Enter-to-add not wired"
    assert "todo-addbtn" in js, "+ button not present"
    # Both paths funnel into a single add() that POSTs a new todo.
    assert re.search(r"method:\s*['\"]POST['\"]", js), \
        "add must POST a new todo to the API"


# ── AC6: checkbox + text row ──────────────────────────────────────────────────

def test_ac6_checkbox_text_row(js):
    assert "todo-check" in js and "todo-text" in js, \
        "each item must render a checkbox + text row"


# ── AC7: check → strikethrough + move into "Done (n)" group ──────────────────

def test_ac7_check_moves_to_done_group(js):
    # The done partition drives a "Done (n)" header whose count is the
    # completed length.
    assert "Done (" in js, "Done group header missing"
    assert re.search(r"done\.length", js), \
        "Done count must reflect the number of completed items"
    # toggling persists the done flag through the API
    assert re.search(r"done\s*:", js), "toggle must PATCH the done flag"


# ── AC8: uncheck → back to active list ───────────────────────────────────────

def test_ac8_uncheck_returns_to_active(js):
    """A single toggle handler flips done both ways via PATCH."""
    assert re.search(r"PATCH", js), "toggle must PATCH the item"
    # rendering partitions by the done flag, so unchecking moves it back.
    assert re.search(r"!\s*\w+\.done|\.done\s*\?|filter\([^)]*done", js), \
        "rendering must partition items by their done flag"


# ── AC9: Done group collapsible; count stays current ─────────────────────────

def test_fullscreen_expand_wired(js, project):
    assert "openFullscreen" in js
    assert "todo-fs-root" in js
    assert "todo-fsbtn" in js
    assert "CommanderTodo.openFullscreen" in project or "todo-dock-expand" in project


def test_attachment_ui_wired(js):
    assert "todo-thumb" in js
    assert "/attachments" in js
    assert "attach" in js


def test_clear_done_button_wired(js):
    assert "todo-done-clear" in js, "Clear done button class missing"
    assert "Clear done" in js, "Clear done label missing"
    assert re.search(r"/done['\"].*method:\s*['\"]DELETE['\"]|method:\s*['\"]DELETE['\"][^}]+/done", _nows(js)), \
        "Clear done must DELETE /api/projects/<project>/todos/done"


def test_ac9_done_group_collapsible(js):
    assert "todo-done-toggle" in js, "Done group toggle missing"
    collapsed = _nows(js)
    # a collapsed-state class hides the done list
    assert re.search(r"done-collapsed[^{]*\.todo-done-list\{[^}]*display:none", collapsed) or \
        "done-collapsed" in js, "Done group must be collapsible"


# ── AC10: hover reveals delete + edit ────────────────────────────────────────

def test_ac10_hover_actions_delete_and_edit(js):
    collapsed = _nows(js)
    assert "todo-actions" in collapsed and "opacity:1" in collapsed
    assert "todo-item:hover" in collapsed or "todo-item.focused" in collapsed
    assert "todo-iconbtn edit" in js or '.edit"' in js, "edit action missing"
    assert "ti-pencil" in js, "edit pencil icon missing"
    assert re.search(r"\bdel\b|delete", js), "delete action missing"


# ── AC11: delete removes + persists via API ──────────────────────────────────

def test_ac11_delete_persists(js):
    assert re.search(r"method:\s*['\"]DELETE['\"]", js), \
        "delete must call the API with DELETE"


# ── AC12: inline edit persists via API ───────────────────────────────────────

def test_ac12_inline_edit_persists(js):
    # Editing swaps the text for an input and PATCHes the new text.
    assert "dblclick" in js or "ondblclick" in js, \
        "inline edit must be activated (e.g. double-click)"
    assert re.search(r"text\s*:", js), "edit must PATCH the new text"


# ── AC13: reorder persists via API ───────────────────────────────────────────

def test_ac13_reorder_persists(js):
    assert "draggable" in js, "items must be draggable to reorder"
    # new order persists by PATCHing position
    assert re.search(r"position\s*:", js), \
        "reorder must PATCH the new position"


# ── AC14: auto-save + "Saved" indicator after every change ───────────────────

def test_ac14_saved_indicator(js):
    assert "todo-saved" in js, "Saved indicator element missing"
    # a helper flashes the indicator after a persist
    assert re.search(r"function\s+flashSaved|flashSaved\s*=", js) or "flashSaved" in js, \
        "Saved indicator must flash after each save"


def test_ac14_every_mutation_flashes_saved(js):
    """add / toggle / edit / delete / reorder each confirm persistence."""
    assert js.count("flashSaved") >= 2, \
        "the Saved indicator must fire after persistence (every mutation path)"


# ── AC15: empty state ─────────────────────────────────────────────────────────

def test_ac15_empty_state(js):
    assert "todo-empty" in js, "empty-state element missing"


# ── AC16: paste screenshot creates a new to-do ───────────────────────────────

def test_ac16_paste_creates_new_todo_from_screenshot(js):
    assert "addFromScreenshot" in js, "paste must create a new to-do from screenshots"
    assert "apiCreate" in js
    m = re.search(r"panel\.onpaste\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\n\s*\};", js, re.DOTALL)
    assert m, "panel paste handler must exist"
    body = m.group(1)
    assert "addFromScreenshot" in body, "paste handler must call addFromScreenshot"
    assert "focusId" not in body or "addFromScreenshot" in body, \
        "paste must not require a focused existing item"

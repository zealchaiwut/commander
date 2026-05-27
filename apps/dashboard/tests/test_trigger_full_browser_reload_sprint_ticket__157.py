"""Tests for issue #157: Trigger full browser reload when ticket added to sprint.

AC1 — smgmtMoveTicketTo calls window.location.reload() after successful API call.
AC2 — smgmtDropOnSprint calls window.location.reload() after successful API call.
       Updated by issue #179: now calls smgmtRefreshBoard() instead.
AC3 — smgmtDropOnPlaceholder calls window.location.reload() (replaces smgmtSelectProject).
       Updated by issue #179: now calls smgmtRefreshBoard() instead.
AC4 — On API failure, no reload; rollback + smgmtShowError are preserved in each function.
AC5 — No regressions: drag-and-drop functions exist, smgmtRender still called optimistically.
"""
import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "static" / "app.js"


@pytest.fixture(scope="module")
def src():
    return APP_JS.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    """Extract the full body of an async function by matching braces."""
    pattern = rf"async function {re.escape(name)}\s*\("
    m = re.search(pattern, src)
    assert m, f"Function {name} not found in app.js"
    start = m.start()
    depth = 0
    i = start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError(f"Could not extract body of {name}")


# ── AC1: smgmtMoveTicketTo reloads on success ─────────────────────────────────

def test_move_ticket_to_reload_present(src):
    """AC1: smgmtMoveTicketTo calls window.location.reload() in the try block."""
    body = _extract_function(src, "smgmtMoveTicketTo")
    assert "window.location.reload()" in body, (
        "smgmtMoveTicketTo must call window.location.reload() after success"
    )


def test_move_ticket_to_reload_after_ok_check(src):
    """AC1: reload comes after the res.ok guard (success path only)."""
    body = _extract_function(src, "smgmtMoveTicketTo")
    ok_pos = body.find("if (!res.ok) throw new Error")
    reload_pos = body.find("window.location.reload()")
    catch_pos = body.find("} catch (e) {")
    assert ok_pos != -1, "res.ok guard missing from smgmtMoveTicketTo"
    assert reload_pos != -1, "window.location.reload() missing from smgmtMoveTicketTo"
    assert ok_pos < reload_pos < catch_pos, (
        "reload() must appear after res.ok guard and before catch block"
    )


def test_move_ticket_to_no_reload_in_catch(src):
    """AC4: smgmtMoveTicketTo catch block does NOT call window.location.reload()."""
    body = _extract_function(src, "smgmtMoveTicketTo")
    catch_pos = body.find("} catch (e) {")
    assert catch_pos != -1, "catch block missing from smgmtMoveTicketTo"
    catch_body = body[catch_pos:]
    assert "window.location.reload()" not in catch_body, (
        "smgmtMoveTicketTo catch block must not reload on failure"
    )


def test_move_ticket_to_rollback_preserved(src):
    """AC4: smgmtMoveTicketTo catch block preserves rollback and error display."""
    body = _extract_function(src, "smgmtMoveTicketTo")
    catch_pos = body.find("} catch (e) {")
    catch_body = body[catch_pos:]
    assert "smgmtRender()" in catch_body, (
        "smgmtMoveTicketTo catch must call smgmtRender() to rollback optimistic update"
    )
    assert "smgmtShowError(" in catch_body, (
        "smgmtMoveTicketTo catch must call smgmtShowError() on failure"
    )


# ── AC2: smgmtDropOnSprint reloads on success ─────────────────────────────────

def test_drop_on_sprint_reload_present(src):
    """AC2 (updated by #179): smgmtDropOnSprint calls smgmtRefreshBoard() in the try block."""
    body = _extract_function(src, "smgmtDropOnSprint")
    assert "smgmtRefreshBoard()" in body, (
        "smgmtDropOnSprint must call smgmtRefreshBoard() after success (issue #179)"
    )


def test_drop_on_sprint_reload_after_ok_check(src):
    """AC2 (updated by #179): smgmtRefreshBoard call comes after the res.ok guard."""
    body = _extract_function(src, "smgmtDropOnSprint")
    ok_pos = body.find("if (!res.ok) throw new Error")
    reload_pos = body.find("smgmtRefreshBoard()")
    catch_pos = body.find("} catch (e) {")
    assert ok_pos != -1, "res.ok guard missing from smgmtDropOnSprint"
    assert reload_pos != -1, "smgmtRefreshBoard() missing from smgmtDropOnSprint"
    assert ok_pos < reload_pos < catch_pos, (
        "smgmtRefreshBoard() must appear after res.ok guard and before catch block in smgmtDropOnSprint"
    )


def test_drop_on_sprint_no_reload_in_catch(src):
    """AC4: smgmtDropOnSprint catch block does NOT call window.location.reload() or smgmtRefreshBoard()."""
    body = _extract_function(src, "smgmtDropOnSprint")
    catch_pos = body.find("} catch (e) {")
    assert catch_pos != -1, "catch block missing from smgmtDropOnSprint"
    catch_body = body[catch_pos:]
    assert "window.location.reload()" not in catch_body, (
        "smgmtDropOnSprint catch block must not reload on failure"
    )
    assert "smgmtRefreshBoard()" not in catch_body, (
        "smgmtDropOnSprint catch block must not call smgmtRefreshBoard() on failure"
    )


def test_drop_on_sprint_rollback_preserved(src):
    """AC4/AC5: smgmtDropOnSprint catch preserves rollback and error display."""
    body = _extract_function(src, "smgmtDropOnSprint")
    catch_pos = body.find("} catch (e) {")
    catch_body = body[catch_pos:]
    assert "smgmtRender()" in catch_body, (
        "smgmtDropOnSprint catch must call smgmtRender() to rollback"
    )
    assert "smgmtShowError(" in catch_body, (
        "smgmtDropOnSprint catch must call smgmtShowError() on failure"
    )


# ── AC3: smgmtDropOnPlaceholder reloads on success ────────────────────────────

def test_drop_on_placeholder_reload_present(src):
    """AC3 (updated by #179): smgmtDropOnPlaceholder calls smgmtRefreshBoard() in the try block."""
    body = _extract_function(src, "smgmtDropOnPlaceholder")
    assert "smgmtRefreshBoard()" in body, (
        "smgmtDropOnPlaceholder must call smgmtRefreshBoard() after success (issue #179)"
    )


def test_drop_on_placeholder_no_select_project(src):
    """AC3: smgmtDropOnPlaceholder no longer calls smgmtSelectProject directly in the success path."""
    body = _extract_function(src, "smgmtDropOnPlaceholder")
    try_start = body.find("try {")
    catch_start = body.find("} catch (e) {")
    assert try_start != -1 and catch_start != -1, (
        "try/catch structure missing from smgmtDropOnPlaceholder"
    )
    try_body = body[try_start:catch_start]
    assert "smgmtSelectProject(" not in try_body, (
        "smgmtDropOnPlaceholder success path must NOT call smgmtSelectProject() directly; use smgmtRefreshBoard() instead"
    )


def test_drop_on_placeholder_reload_after_ok_check(src):
    """AC3 (updated by #179): smgmtRefreshBoard call comes after the res.ok guard."""
    body = _extract_function(src, "smgmtDropOnPlaceholder")
    ok_pos = body.find("if (!res.ok) throw new Error")
    reload_pos = body.find("smgmtRefreshBoard()")
    catch_pos = body.find("} catch (e) {")
    assert ok_pos != -1, "res.ok guard missing from smgmtDropOnPlaceholder"
    assert reload_pos != -1, "smgmtRefreshBoard() missing from smgmtDropOnPlaceholder"
    assert ok_pos < reload_pos < catch_pos, (
        "smgmtRefreshBoard() must appear after res.ok guard and before catch block in smgmtDropOnPlaceholder"
    )


def test_drop_on_placeholder_no_reload_in_catch(src):
    """AC4: smgmtDropOnPlaceholder catch block does NOT reload or call smgmtRefreshBoard()."""
    body = _extract_function(src, "smgmtDropOnPlaceholder")
    catch_pos = body.find("} catch (e) {")
    assert catch_pos != -1, "catch block missing from smgmtDropOnPlaceholder"
    catch_body = body[catch_pos:]
    assert "window.location.reload()" not in catch_body, (
        "smgmtDropOnPlaceholder catch block must not reload on failure"
    )
    assert "smgmtRefreshBoard()" not in catch_body, (
        "smgmtDropOnPlaceholder catch block must not call smgmtRefreshBoard() on failure"
    )


def test_drop_on_placeholder_rollback_preserved(src):
    """AC4: smgmtDropOnPlaceholder catch preserves rollback and re-render."""
    body = _extract_function(src, "smgmtDropOnPlaceholder")
    catch_pos = body.find("} catch (e) {")
    catch_body = body[catch_pos:]
    assert "smgmtRender()" in catch_body, (
        "smgmtDropOnPlaceholder catch must call smgmtRender() to rollback"
    )
    assert "_smgmtData.order" in catch_body and "_smgmtData.sprints" in catch_body, (
        "smgmtDropOnPlaceholder catch must restore _smgmtData.order and .sprints"
    )


# ── AC5: no regressions — drag functions still exist and use optimistic renders ──

def test_smgmt_functions_exist(src):
    """AC5: All three sprint-assign functions are still present in app.js."""
    for fn in ("smgmtMoveTicketTo", "smgmtDropOnSprint", "smgmtDropOnPlaceholder"):
        assert f"async function {fn}" in src, f"{fn} is missing from app.js"


def test_optimistic_render_preserved_in_move_ticket(src):
    """AC5: smgmtMoveTicketTo still does optimistic smgmtRender() before try block."""
    body = _extract_function(src, "smgmtMoveTicketTo")
    try_pos = body.find("try {")
    render_pos = body.find("smgmtRender()")
    assert render_pos != -1 and render_pos < try_pos, (
        "smgmtMoveTicketTo must call smgmtRender() optimistically before the try block"
    )


def test_optimistic_render_preserved_in_drop_on_sprint(src):
    """AC5: smgmtDropOnSprint still calls smgmtRender() optimistically before try."""
    body = _extract_function(src, "smgmtDropOnSprint")
    try_pos = body.find("try {")
    render_pos = body.find("smgmtRender()")
    assert render_pos != -1 and render_pos < try_pos, (
        "smgmtDropOnSprint must call smgmtRender() optimistically before the try block"
    )


def test_api_endpoint_unchanged(src):
    """AC5: All three functions still POST to /api/sprint-planning/assign."""
    for fn in ("smgmtMoveTicketTo", "smgmtDropOnSprint", "smgmtDropOnPlaceholder"):
        body = _extract_function(src, fn)
        assert "/api/sprint-planning/assign" in body, (
            f"{fn} must still POST to /api/sprint-planning/assign"
        )

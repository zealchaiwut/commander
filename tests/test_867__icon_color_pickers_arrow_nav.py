"""Tests for issue #867 — Icon/color pickers: add Arrow-key keyboard navigation.

AC coverage:
  AC1 — Icon picker handles ArrowRight/ArrowDown to move to next option, wrapping.
  AC2 — Icon picker handles ArrowLeft/ArrowUp to move to previous option, wrapping.
  AC3 — Color picker handles ArrowRight/ArrowDown to move to next option, wrapping.
  AC4 — Color picker handles ArrowLeft/ArrowUp to move to previous option, wrapping.
  AC5 — Roving tabindex is maintained after Arrow-key navigation.
  AC6 — Arrow-key navigation calls the appropriate selection handler.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "apps" / "dashboard" / "static"


def _html():
    return (STATIC_DIR / "project.html").read_text()


def _fn_body(html, signature, span=1500):
    idx = html.index(signature)
    return html[idx:idx + span]


# ── AC1: Icon picker — ArrowRight / ArrowDown moves to next, wraps ─────────────

def test_icon_picker_has_keydown_handler_wired():
    """The icon radiogroup must have a keydown handler attached."""
    html = _html()
    assert "_psIconGridKeydown" in html, (
        "An _psIconGridKeydown handler must exist for the icon picker"
    )
    assert 'id="ps-icon-grid"' in html, "icon grid container must exist"
    region = html[html.index('id="ps-icon-grid"'):html.index('id="ps-icon-grid"') + 300]
    assert "_psIconGridKeydown" in region, (
        "The icon grid element must wire up _psIconGridKeydown (onkeydown or addEventListener)"
    )


def test_icon_picker_arrow_right_moves_to_next():
    """_psIconGridKeydown must advance selection on ArrowRight."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "ArrowRight" in body, (
        "_psIconGridKeydown must handle ArrowRight to move to the next icon"
    )


def test_icon_picker_arrow_down_moves_to_next():
    """_psIconGridKeydown must advance selection on ArrowDown."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "ArrowDown" in body, (
        "_psIconGridKeydown must handle ArrowDown (identical to ArrowRight)"
    )


def test_icon_picker_arrow_right_wraps_at_last():
    """After the last icon, ArrowRight/Down wraps back to the first."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert ("% " in body or "length" in body), (
        "_psIconGridKeydown must wrap from last to first (use modulo or length check)"
    )


# ── AC2: Icon picker — ArrowLeft / ArrowUp moves to previous, wraps ────────────

def test_icon_picker_arrow_left_moves_to_prev():
    """_psIconGridKeydown must retreat selection on ArrowLeft."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "ArrowLeft" in body, (
        "_psIconGridKeydown must handle ArrowLeft to move to the previous icon"
    )


def test_icon_picker_arrow_up_moves_to_prev():
    """_psIconGridKeydown must retreat selection on ArrowUp."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "ArrowUp" in body, (
        "_psIconGridKeydown must handle ArrowUp (identical to ArrowLeft)"
    )


# ── AC3: Color picker — ArrowRight / ArrowDown moves to next, wraps ────────────

def test_color_picker_has_keydown_handler_wired():
    """The color radiogroup must have a keydown handler attached."""
    html = _html()
    assert "_psColorGridKeydown" in html, (
        "An _psColorGridKeydown handler must exist for the color picker"
    )
    region = html[html.index('id="ps-color-grid"'):html.index('id="ps-color-grid"') + 300]
    assert "_psColorGridKeydown" in region, (
        "The color grid element must wire up _psColorGridKeydown"
    )


def test_color_picker_arrow_right_moves_to_next():
    """_psColorGridKeydown must advance selection on ArrowRight."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "ArrowRight" in body, (
        "_psColorGridKeydown must handle ArrowRight to move to the next color"
    )


def test_color_picker_arrow_down_moves_to_next():
    """_psColorGridKeydown must advance selection on ArrowDown."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "ArrowDown" in body, (
        "_psColorGridKeydown must handle ArrowDown (identical to ArrowRight)"
    )


def test_color_picker_arrow_right_wraps_at_last():
    """After the last color, ArrowRight/Down wraps back to the first."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert ("% " in body or "length" in body), (
        "_psColorGridKeydown must wrap from last to first"
    )


# ── AC4: Color picker — ArrowLeft / ArrowUp moves to previous, wraps ───────────

def test_color_picker_arrow_left_moves_to_prev():
    """_psColorGridKeydown must retreat selection on ArrowLeft."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "ArrowLeft" in body, (
        "_psColorGridKeydown must handle ArrowLeft to move to the previous color"
    )


def test_color_picker_arrow_up_moves_to_prev():
    """_psColorGridKeydown must retreat selection on ArrowUp."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "ArrowUp" in body, (
        "_psColorGridKeydown must handle ArrowUp (identical to ArrowLeft)"
    )


# ── AC5: Roving tabindex maintained after navigation ───────────────────────────

def test_icon_picker_keydown_updates_tabindex():
    """The icon keydown handler must update tabindex on each button (roving pattern)."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "tabindex" in body, (
        "_psIconGridKeydown must update tabindex (roving tabindex pattern)"
    )


def test_color_picker_keydown_updates_tabindex():
    """The color keydown handler must update tabindex on each button."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "tabindex" in body, (
        "_psColorGridKeydown must update tabindex (roving tabindex pattern)"
    )


def test_icon_picker_arrow_prevents_default():
    """Arrow keys inside the picker must not scroll the page (preventDefault)."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "preventDefault" in body, (
        "_psIconGridKeydown must call preventDefault() to block page scroll"
    )


def test_color_picker_arrow_prevents_default():
    """Arrow keys inside the color picker must not scroll the page."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "preventDefault" in body, (
        "_psColorGridKeydown must call preventDefault() to block page scroll"
    )


# ── AC6: Arrow-key navigation calls the selection handler ──────────────────────

def test_icon_picker_keydown_calls_select_handler():
    """_psIconGridKeydown must call _psSelectIcon after moving focus."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert "_psSelectIcon" in body, (
        "_psIconGridKeydown must call _psSelectIcon so state updates like a mouse click"
    )


def test_color_picker_keydown_calls_select_handler():
    """_psColorGridKeydown must call _psSelectColor after moving focus."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert "_psSelectColor" in body, (
        "_psColorGridKeydown must call _psSelectColor so state updates like a mouse click"
    )


def test_icon_picker_keydown_focuses_new_option():
    """The new icon button must receive focus after navigation."""
    html = _html()
    body = _fn_body(html, "function _psIconGridKeydown", span=1500)
    assert ".focus()" in body, (
        "_psIconGridKeydown must call .focus() on the newly selected button"
    )


def test_color_picker_keydown_focuses_new_option():
    """The new color button must receive focus after navigation."""
    html = _html()
    body = _fn_body(html, "function _psColorGridKeydown", span=1500)
    assert ".focus()" in body, (
        "_psColorGridKeydown must call .focus() on the newly selected button"
    )

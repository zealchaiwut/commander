"""Tests for issue #1197 — Fix Sprint History mobile reflow and Gantt overflow.

Static CSS analysis of project.html and JS analysis of history.js — no server needed.

AC coverage:
  AC1 — At 375px: .iss-row wraps (flex-wrap:wrap inside @media (max-width:600px))
  AC2 — At 600px: same stacking behavior (same media block covers both)
  AC3 — .iss-title takes full row width (flex:1 1 100%) with text-overflow:ellipsis in mobile
  AC4 — .iss-time sits below title (flex:0 0 100%, margin-left:0, margin-top:4px) on mobile
  AC5 — .hist-gantt has overflow-x:auto (gantt scrolls horizontally; page does not)
  AC6 — Desktop layout (>600px) unchanged — base .iss-row stays single-line, gantt renders inline
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HTML_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
HISTORY_JS_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "history.js"

PROJECT_HTML = HTML_PATH.read_text(encoding="utf-8")
HISTORY_JS = HISTORY_JS_PATH.read_text(encoding="utf-8")


def _extract_media_max_width(source: str, px: int) -> str:
    """Return all @media (max-width:<px>px) block bodies concatenated."""
    pattern = re.compile(
        r"@media\s*\(\s*max-width\s*:\s*" + str(px) + r"px\s*\)",
        re.IGNORECASE,
    )
    blocks = []
    pos = 0
    while True:
        m = pattern.search(source, pos)
        if not m:
            break
        start = source.find("{", m.end())
        if start == -1:
            break
        depth = 1
        i = start + 1
        while i < len(source) and depth > 0:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        blocks.append(source[start + 1: i - 1])
        pos = i
    return "\n".join(blocks)


def _strip_media_blocks(source: str) -> str:
    """Return source with all @media blocks removed (base styles only)."""
    return re.sub(
        r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}",
        "",
        source,
        flags=re.DOTALL,
    )


def _get_rule_body(block: str, selector: str) -> str | None:
    """Return the CSS rule body for selector inside block, or None."""
    escaped = re.escape(selector)
    pattern = re.compile(
        r"(?:^|[{};,\s])" + escaped + r"\s*\{",
        re.MULTILINE,
    )
    m = pattern.search(block)
    if not m:
        return None
    brace_start = block.find("{", m.start())
    depth = 1
    j = brace_start + 1
    while j < len(block) and depth > 0:
        if block[j] == "{":
            depth += 1
        elif block[j] == "}":
            depth -= 1
        j += 1
    return block[brace_start + 1: j - 1]


MW600 = _extract_media_max_width(PROJECT_HTML, 600)
BASE_CSS = _strip_media_blocks(PROJECT_HTML)


# ── AC1 & AC2 ─────────────────────────────────────────────────────────────────

def test_media_600px_block_exists():
    """AC1/AC2: A @media (max-width:600px) block must exist in project.html."""
    assert MW600, "No @media (max-width:600px) block found in project.html"


def test_iss_row_flex_wrap_in_600px_block():
    """AC1/AC2: .iss-row must have flex-wrap:wrap inside @media (max-width:600px)."""
    body = _get_rule_body(MW600, ".iss-row")
    assert body is not None, (
        ".iss-row rule not found inside @media (max-width:600px) — "
        "issue rows will not stack on narrow viewports"
    )
    assert re.search(r"flex-wrap\s*:\s*wrap", body), (
        ".iss-row must have flex-wrap:wrap inside @media (max-width:600px) — "
        "rows will overflow horizontally at 375px/600px instead of stacking"
    )


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_iss_title_full_width_in_600px_block():
    """AC3: .iss-title must have flex:1 1 100% inside @media (max-width:600px)."""
    body = _get_rule_body(MW600, ".iss-title")
    assert body is not None, (
        ".iss-title rule not found inside @media (max-width:600px)"
    )
    assert re.search(r"flex\s*:\s*1\s+1\s+100%", body), (
        ".iss-title must have flex:1 1 100% inside @media (max-width:600px) — "
        "title must take the full row width on mobile"
    )


def test_iss_title_text_overflow_ellipsis():
    """AC3: .iss-title must have text-overflow:ellipsis somewhere in project.html."""
    # Search directly in HTML — the property may live in base CSS or mobile block.
    matches = re.findall(r"\.iss-title\s*\{([^}]+)\}", PROJECT_HTML)
    assert any(re.search(r"text-overflow\s*:\s*ellipsis", m) for m in matches), (
        ".iss-title must have text-overflow:ellipsis — long titles must truncate, not push time off-screen"
    )


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_iss_time_flex_full_width_in_600px_block():
    """AC4: .iss-time must have flex:0 0 100% inside @media (max-width:600px)."""
    body = _get_rule_body(MW600, ".iss-time")
    assert body is not None, (
        ".iss-time rule not found inside @media (max-width:600px)"
    )
    assert re.search(r"flex\s*:\s*0\s+0\s+100%", body), (
        ".iss-time must have flex:0 0 100% inside @media (max-width:600px) — "
        "time must sit on its own line below the title"
    )


def test_iss_time_margin_left_zero_in_600px_block():
    """AC4: .iss-time must have margin-left:0 inside @media (max-width:600px)."""
    body = _get_rule_body(MW600, ".iss-time")
    assert body is not None
    assert re.search(r"margin-left\s*:\s*0", body), (
        ".iss-time must have margin-left:0 inside @media (max-width:600px) — "
        "time must not push to the right when it wraps"
    )


def test_iss_time_margin_top_in_600px_block():
    """AC4: .iss-time must have margin-top:4px inside @media (max-width:600px)."""
    body = _get_rule_body(MW600, ".iss-time")
    assert body is not None
    assert re.search(r"margin-top\s*:\s*4px", body), (
        ".iss-time must have margin-top:4px inside @media (max-width:600px) — "
        "time needs spacing below the title when stacked"
    )


# ── AC5 ───────────────────────────────────────────────────────────────────────

def test_hist_gantt_overflow_x_auto_in_base_css():
    """AC5: .hist-gantt must have overflow-x:auto in base styles (not mobile-only)."""
    body = _get_rule_body(BASE_CSS, ".hist-gantt")
    assert body is not None, (
        ".hist-gantt rule not found in project.html base styles — "
        "the gantt container will not scroll horizontally when content exceeds viewport"
    )
    assert re.search(r"overflow-x\s*:\s*auto", body), (
        ".hist-gantt must have overflow-x:auto — "
        "page will scroll horizontally instead of the gantt container when chart exceeds viewport"
    )


def test_hist_gantt_class_in_history_js():
    """AC5: _histGanttHtml() must produce a .hist-gantt wrapper in history.js."""
    assert "hist-gantt" in HISTORY_JS, (
        "hist-gantt class not found in history.js — "
        "_histGanttHtml() must wrap .gantt in a .hist-gantt container"
    )


# ── AC6 ───────────────────────────────────────────────────────────────────────

def test_iss_row_base_no_flex_wrap():
    """AC6: Base .iss-row must NOT have flex-wrap:wrap (desktop layout unchanged)."""
    body = _get_rule_body(BASE_CSS, ".iss-row")
    assert body is not None, "Base .iss-row rule not found"
    assert not re.search(r"flex-wrap\s*:\s*wrap", body), (
        ".iss-row has flex-wrap:wrap in base styles — "
        "desktop rows would wrap unnecessarily at widths above 600px"
    )


def test_iss_row_base_has_display_flex():
    """AC6: Base .iss-row must still have display:flex (desktop single-line layout preserved)."""
    body = _get_rule_body(BASE_CSS, ".iss-row")
    assert body is not None
    assert re.search(r"display\s*:\s*flex", body), (
        "Base .iss-row must still have display:flex for desktop single-line layout"
    )


def test_iss_title_base_flex_unchanged():
    """AC6: Base .iss-title flex must not be 100% width (desktop layout unchanged)."""
    body = _get_rule_body(BASE_CSS, ".iss-title")
    assert body is not None, "Base .iss-title rule not found"
    # Base should have flex:1 (or flex:1 ...) but NOT flex:1 1 100% — that's mobile only
    assert not re.search(r"flex\s*:\s*1\s+1\s+100%", body), (
        ".iss-title has flex:1 1 100% in base styles — "
        "desktop title would take full row width, breaking single-line layout"
    )

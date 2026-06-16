"""Tests for issue #1056 — Audit and Fix Sprint Board Impeccable Violations.

Reads CSS from project.html and JS source from src/sprint-board/ to verify
the design contract: zero impeccable anti-patterns, all values token-based.

AC coverage:
  AC1  — impeccable detect findings: zero (approximated by no hardcoded hex in
          sprint-board CSS rules and no side-tab borders)
  AC2  — Chip contrast: all sprint board chips use foundation color tokens only
  AC3  — Card spacing: sprint board card CSS uses spacing tokens only
  AC4  — Separator hierarchy: level-sep has elevated visual treatment via tokens
  AC5  — Alignment: sprint board ticket row spacing uses token-based grid
  AC6  — No custom/hardcoded values in sprint board color CSS
  AC7  — npm run build succeeds (bundle.js exists and is non-empty)
  AC8  — Live update (polling + SSE) remains intact
  AC9  — Dark theme renders correctly: token overrides present
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")
BUNDLE = REPO_ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"

# Sprint board CSS region: everything between the sc-v5 block comment and end-of-style
_SPRINT_BOARD_CSS_START = "Sprint board mock v5"
_SPRINT_BOARD_CSS_END   = "Bulk Create tab"

def _sprint_board_css() -> str:
    """Return the CSS region that belongs to the sprint board."""
    start = PROJECT_HTML.find(_SPRINT_BOARD_CSS_START)
    end   = PROJECT_HTML.find(_SPRINT_BOARD_CSS_END, start)
    assert start != -1, "sprint board CSS region not found"
    return PROJECT_HTML[start:end] if end != -1 else PROJECT_HTML[start:]


def _css_rule(selector: str, src: str = PROJECT_HTML) -> str:
    """Return the body of the first CSS rule matching selector exactly."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(src)
    assert m is not None, f"CSS rule for `{selector}` not found in project.html"
    return m.group(1)


def _fn_body(name: str, src: str = BOARD_JS) -> str:
    """Return the brace-balanced body of a named JS function."""
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — No side-tab anti-pattern in sprint board CSS
# impeccable's 'side-tab' rule fires on border-left/right: Npx solid (N >= 3)
# ═══════════════════════════════════════════════════════════════════════════════

_SIDE_TAB_RE = re.compile(
    r"border-(?:left|right)\s*:\s*([3-9]|\d{2,})px\s+solid",
    re.IGNORECASE,
)


def test_no_side_tab_on_active_agent_coder():
    """Active-agent coder card must not use a border-left side-tab (impeccable 'side-tab')."""
    rule = _css_rule(".smgmt-active-agent--coder")
    m = _SIDE_TAB_RE.search(rule)
    assert m is None, (
        ".smgmt-active-agent--coder must not use a colored side-tab border "
        f"(impeccable side-tab anti-pattern): found '{m.group() if m else ''}'"
    )


def test_no_side_tab_on_active_agent_tester():
    """Active-agent tester card must not use a border-left side-tab."""
    rule = _css_rule(".smgmt-active-agent--tester")
    m = _SIDE_TAB_RE.search(rule)
    assert m is None, (
        ".smgmt-active-agent--tester must not use a colored side-tab border "
        f"(impeccable side-tab anti-pattern): found '{m.group() if m else ''}'"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — Chip contrast: sprint board chips use foundation color tokens only
# ═══════════════════════════════════════════════════════════════════════════════

_HEX_COLOR_RE = re.compile(r"(?<!\w)#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])")
_COLOR_MIX_HARDCODED_RE = re.compile(
    r"color-mix\([^)]*#[0-9a-fA-F]{3,8}",
    re.IGNORECASE,
)


def _rule_has_no_hardcoded_color(selector: str, src: str = PROJECT_HTML) -> None:
    """Assert that a CSS rule contains no hardcoded hex or color-mix(#hex) patterns."""
    rule = _css_rule(selector, src)
    stripped = re.sub(r"/\*.*?\*/", "", rule, flags=re.DOTALL)
    for m in _HEX_COLOR_RE.finditer(stripped):
        raise AssertionError(
            f"CSS rule `{selector}` has hardcoded hex color '{m.group()}' "
            "— use a foundation token var(--*) instead"
        )
    for m in _COLOR_MIX_HARDCODED_RE.finditer(stripped):
        raise AssertionError(
            f"CSS rule `{selector}` uses color-mix() with a hardcoded hex "
            f"'{m.group()}' — replace with var(--*) token"
        )


def test_partial_finished_card_uses_tokens():
    """.smgmt-sprint-card.smgmt-partial-finished must use yellow token, not color-mix(#facc15)."""
    _rule_has_no_hardcoded_color(".smgmt-sprint-card.smgmt-partial-finished")


def test_partial_finished_header_uses_tokens():
    """.smgmt-sprint-card.smgmt-partial-finished .smgmt-sprint-header must use tokens."""
    _rule_has_no_hardcoded_color(
        ".smgmt-sprint-card.smgmt-partial-finished .smgmt-sprint-header"
    )


def test_state_badge_partial_uses_tokens():
    """.smgmt-state-badge.state-partial must use var(--yellow) and var(--yellow-bg)."""
    _rule_has_no_hardcoded_color(".smgmt-state-badge.state-partial")


def test_risk_flag_icon_uses_token():
    """.smgmt-risk-flag-icon must use var(--amber), not #d97706."""
    rule = _css_rule(".smgmt-risk-flag-icon")
    assert _HEX_COLOR_RE.search(rule) is None, (
        ".smgmt-risk-flag-icon must use var(--amber) token, not a hardcoded hex color"
    )


def test_dep_order_badge_uses_tokens():
    """.smgmt-dep-order-badge must use foundation tokens for background and color."""
    _rule_has_no_hardcoded_color(".smgmt-dep-order-badge")


def test_dep_order_badge_cycle_uses_tokens():
    """.smgmt-dep-order-badge.is-cycle must use var(--amber-bg) / var(--amber)."""
    _rule_has_no_hardcoded_color(".smgmt-dep-order-badge.is-cycle")


def test_sched_dep_badge_uses_tokens():
    """.smgmt-sched-dep-badge must use foundation tokens, not hardcoded rgba."""
    _rule_has_no_hardcoded_color(".smgmt-sched-dep-badge")


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — Card spacing uses foundation spacing tokens
# ═══════════════════════════════════════════════════════════════════════════════

def test_active_agents_padding_uses_token():
    """.smgmt-active-agents padding must use var(--space-*) tokens."""
    rule = _css_rule(".smgmt-active-agents")
    pad = re.search(r"padding:\s*([^;]+)", rule)
    if pad:
        val = pad.group(1).strip()
        assert "var(" in val, (
            f".smgmt-active-agents padding must use var(--space-*) tokens, got: {val}"
        )


def test_active_agent_padding_uses_token():
    """.smgmt-active-agent padding must use var(--space-*) tokens."""
    rule = _css_rule(".smgmt-active-agent")
    pad = re.search(r"padding:\s*([^;]+)", rule)
    if pad:
        val = pad.group(1).strip()
        assert "var(" in val, (
            f".smgmt-active-agent padding must use var(--space-*) tokens, got: {val}"
        )


def test_levels_padding_uses_token():
    """.smgmt-levels padding must use var(--space-*) tokens."""
    rule = _css_rule(".smgmt-levels")
    pad = re.search(r"padding:\s*([^;]+)", rule)
    if pad:
        val = pad.group(1).strip()
        assert "var(" in val, (
            f".smgmt-levels padding must use var(--space-*) tokens, got: {val}"
        )


def test_sc_v5_header_padding_uses_tokens():
    """.sc-v5 .sc-header padding must use var(--space-*) tokens, not raw px."""
    rule = _css_rule(".sc-v5 .sc-header")
    pad = re.search(r"padding:\s*([^;]+)", rule)
    if pad:
        val = pad.group(1).strip()
        assert "var(" in val, (
            f".sc-v5 .sc-header padding must use var(--space-*) tokens, got: {val}"
        )


def test_sc_v5_ticket_padding_uses_tokens():
    """.sc-v5 .smgmt-ticket padding must use var(--space-*) tokens."""
    # Compound rule for sc-v5 ticket
    pat = re.compile(r"\.sc-v5\s+\.smgmt-ticket\s*\{([^{}]+)\}")
    m = pat.search(PROJECT_HTML)
    if m:
        rule = m.group(1)
        pad = re.search(r"padding:\s*([^;]+)", rule)
        if pad:
            val = pad.group(1).strip()
            assert "var(" in val, (
                f".sc-v5 .smgmt-ticket padding must use var(--space-*) tokens, got: {val}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — Separator hierarchy: level-sep uses elevation tokens
# ═══════════════════════════════════════════════════════════════════════════════

def test_level_sep_background_uses_surface_token():
    """.level-sep background must use a surface token for visual elevation."""
    rule = _css_rule(".level-sep")
    bg = re.search(r"background(?:-color)?:\s*([^;]+)", rule)
    assert bg is not None, ".level-sep must have a background property for visual hierarchy"
    val = bg.group(1).strip()
    assert "var(--" in val, (
        f".level-sep background must use a CSS token for elevation, got: {val}"
    )
    assert _HEX_COLOR_RE.search(val) is None, (
        f".level-sep background must not hardcode a hex color: {val}"
    )


def test_level_sep_border_uses_token():
    """.level-sep border must use var(--border) token."""
    rule = _css_rule(".level-sep")
    assert "var(--border)" in rule, (
        ".level-sep must use var(--border) token for its separator line"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — Alignment: ancestor row spacing uses tokens
# ═══════════════════════════════════════════════════════════════════════════════

def test_ancestor_body_padding_no_off_scale():
    """.slp-ancestor-body padding must not contain off-scale raw px values."""
    rule = _css_rule(".slp-ancestor-body")
    pad = re.search(r"padding:\s*([^;]+)", rule)
    if pad:
        val = pad.group(1).strip()
        # Check for off-scale raw px values: not in {0, 4, 8, 12, 16, 24, 32}
        raw_px = re.findall(r"(\d+)px", val)
        allowed = {"0", "4", "8", "12", "16", "24", "32"}
        for px in raw_px:
            assert px in allowed or "var(" in val, (
                f".slp-ancestor-body padding has off-scale value {px}px "
                "— use var(--space-*) token or an allowed scale value"
            )


def test_focus_step_gap_uses_token_or_scale():
    """.smgmt-focus-step gap must use a token or on-scale value."""
    rule = _css_rule(".smgmt-focus-step")
    gap = re.search(r"gap:\s*([^;]+)", rule)
    if gap:
        val = gap.group(1).strip()
        if "var(" not in val:
            raw_px = re.findall(r"(\d+)px", val)
            allowed = {"4", "8", "12", "16", "24", "32"}
            for px in raw_px:
                assert px in allowed, (
                    f".smgmt-focus-step gap has off-scale value {px}px "
                    "— use var(--space-*) or an allowed scale value (4/8/12/16/24/32)"
                )


def test_draft_card_head_gap_uses_token_or_scale():
    """.smgmt-draft-card .smgmt-card-head gap must be on-scale."""
    pat = re.compile(r"\.smgmt-draft-card\s+\.smgmt-card-head\s*\{([^{}]+)\}")
    m = pat.search(PROJECT_HTML)
    if m:
        rule = m.group(1)
        gap = re.search(r"gap:\s*([^;]+)", rule)
        if gap:
            val = gap.group(1).strip()
            if "var(" not in val:
                raw_px = re.findall(r"(\d+)px", val)
                allowed = {"4", "8", "12", "16", "24", "32"}
                for px in raw_px:
                    assert px in allowed, (
                        f".smgmt-draft-card .smgmt-card-head gap has off-scale value {px}px"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — No hardcoded hex in key sprint board chip/badge/separator rules
# ═══════════════════════════════════════════════════════════════════════════════

def test_active_agent_coder_uses_tokens():
    """.smgmt-active-agent--coder must use only var(--*) tokens, no hardcoded hex."""
    rule = _css_rule(".smgmt-active-agent--coder")
    stripped = re.sub(r"/\*.*?\*/", "", rule, flags=re.DOTALL)
    for m in _HEX_COLOR_RE.finditer(stripped):
        raise AssertionError(
            f".smgmt-active-agent--coder has hardcoded hex '{m.group()}' — use var(--*)"
        )


def test_active_agent_tester_uses_tokens():
    """.smgmt-active-agent--tester must use only var(--*) tokens."""
    rule = _css_rule(".smgmt-active-agent--tester")
    stripped = re.sub(r"/\*.*?\*/", "", rule, flags=re.DOTALL)
    for m in _HEX_COLOR_RE.finditer(stripped):
        raise AssertionError(
            f".smgmt-active-agent--tester has hardcoded hex '{m.group()}' — use var(--*)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — npm run build: bundle.js exists and is non-empty
# ═══════════════════════════════════════════════════════════════════════════════

def test_bundle_exists():
    """static/dist/bundle.js exists after npm run build."""
    assert BUNDLE.exists(), "static/dist/bundle.js must exist"


def test_bundle_non_empty():
    """static/dist/bundle.js is non-empty."""
    assert BUNDLE.stat().st_size > 0, "bundle.js must not be empty"


def test_bundle_contains_sprint_board_code():
    """bundle.js contains sprint board code (_smgmtRender)."""
    text = BUNDLE.read_text(encoding="utf-8", errors="replace")
    assert "_smgmtRender" in text or "smgmt-sprint-card" in text, (
        "bundle.js must contain sprint board render code"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — Live update functionality preserved
# ═══════════════════════════════════════════════════════════════════════════════

def test_live_poll_restart_wired():
    """loadSprintMgmt still calls _smgmtLivePollRestart."""
    body = _fn_body("loadSprintMgmt")
    assert "_smgmtLivePollRestart" in body, (
        "loadSprintMgmt must call _smgmtLivePollRestart for live updates"
    )


def test_smgmt_render_update_subnav():
    """_smgmtRender still calls _smgmtUpdateSubnav on every render."""
    body = _fn_body("_smgmtRender")
    assert "_smgmtUpdateSubnav" in body, (
        "_smgmtRender must call _smgmtUpdateSubnav"
    )


def test_board_exports_load_sprint_mgmt():
    """loadSprintMgmt is still exported from board-render.js."""
    assert "export async function loadSprintMgmt(" in BOARD_JS, (
        "loadSprintMgmt must remain exported from board-render.js"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — Dark theme: CSS variables override correctly
# ═══════════════════════════════════════════════════════════════════════════════

def test_dark_theme_html_attribute():
    """<html> carries data-theme=\"dark\" so the board loads in dark mode."""
    assert 'data-theme="dark"' in PROJECT_HTML, (
        '<html> must have data-theme="dark" for dark-theme sprint board'
    )


def test_yellow_token_in_dark_override():
    """Dark theme section defines --yellow token for sprint board yellow states."""
    dark_section_start = PROJECT_HTML.find('[data-theme="dark"]')
    assert dark_section_start != -1, "Dark theme section must exist"
    dark_section = PROJECT_HTML[dark_section_start:dark_section_start + 3000]
    assert "--yellow" in dark_section, (
        "Dark theme must define --yellow token for partial-finished sprint states"
    )


def test_no_dark_theme_hex_override_for_dep_badge():
    """dep-order-badge dark override must not use hardcoded hex — tokens handle dark mode."""
    # After the fix, the dark override should be removed (tokens handle both themes)
    # OR if kept, it must use var(--*) tokens
    dark_override = re.compile(
        r'\[data-theme="dark"\]\s*\.smgmt-dep-order-badge\s*\{([^{}]+)\}',
        re.DOTALL,
    )
    m = dark_override.search(PROJECT_HTML)
    if m:
        rule_body = m.group(1)
        for hex_match in _HEX_COLOR_RE.finditer(rule_body):
            raise AssertionError(
                f"Dark override for .smgmt-dep-order-badge has hardcoded hex "
                f"'{hex_match.group()}' — use var(--*) tokens instead"
            )


def test_no_dark_theme_hex_override_for_sched_dep_badge():
    """sched-dep-badge dark override must not use hardcoded hex."""
    dark_override = re.compile(
        r'\[data-theme="dark"\]\s*\.smgmt-sched-dep-badge\s*\{([^{}]+)\}',
        re.DOTALL,
    )
    m = dark_override.search(PROJECT_HTML)
    if m:
        rule_body = m.group(1)
        for hex_match in _HEX_COLOR_RE.finditer(rule_body):
            raise AssertionError(
                f"Dark override for .smgmt-sched-dep-badge has hardcoded hex "
                f"'{hex_match.group()}' — use var(--*) tokens instead"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# inline style violations in board-render.js
# ═══════════════════════════════════════════════════════════════════════════════

def test_running_card_sprint_name_no_offscale_font():
    """Running card sprint name style must not use off-scale font-size (e.g. 15px)."""
    # 15px is off-scale — scale is 11/12/13/14/16/18/24
    body = _fn_body("_smgmtRunningCardHtml")
    # Check inline styles for off-scale font-size
    off_scale_font = re.compile(r'font-size:\s*(?:15|17|19|20|21|22|23)px')
    m = off_scale_font.search(body)
    assert m is None, (
        f"_smgmtRunningCardHtml has off-scale font-size in inline style: '{m.group() if m else ''}' "
        "— use var(--font-size-*) or an allowed size (11/12/13/14/16/18/24px)"
    )


def test_backlog_drop_hint_no_offscale_padding():
    """Backlog drop-hint inline style must not use off-scale padding (14px/18px)."""
    body = _fn_body("_smgmtRenderBacklog")
    off_scale_pad = re.compile(r'padding:\s*(?:\d+px\s+)*(?:(?:1[3578]|19|2[01]|2[3-9])|1[4])px')
    m = off_scale_pad.search(body)
    assert m is None, (
        f"_smgmtRenderBacklog has off-scale padding in inline style: '{m.group() if m else ''}' "
        "— use var(--space-*) tokens"
    )

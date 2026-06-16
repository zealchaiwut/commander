"""Tests for issue #1055 — Redesign Sprint Board for Scannability and Token Consistency.

Reads JS source (board-render.js) and CSS from project.html to verify the design contract.

AC coverage:
  AC1  — Ticket cards include all five anatomy fields: id, title, label/status chip,
          agent, and size in _smgmtTicketRowHtml
  AC2  — Level separators (.level-sep) are visually distinct and use CSS tokens from
          tokens.css for all spacing values (no raw px for gap/padding/margin)
  AC3  — Section headers (.smgmt-section-label) use token values for font-size and spacing
  AC4  — No off-scale raw pixel values for spacing in board-facing CSS rules
  AC5  — Dark theme applied: <html> carries data-theme="dark"
  AC6  — Live-update behavior preserved: _smgmtRender exists and still wires
          _smgmtLivePollRestart and _smgmtUpdateSubnav
  AC8  — npm run build artifact: static/dist/bundle.js exists and is non-empty
  AC9  — Vanilla JS only — no framework imports in sprint-board source files
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
SPRINT_BOARD_DIR = REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board"

# Off-scale spacing values per DESIGN.md: only 4, 8, 12, 16, 24, 32 px are allowed.
_OFF_SCALE = re.compile(
    r"(?:gap|padding|margin):\s*[^;{]*?(?<!\w)(?:[2-3]|[5-7]|[9]|1[013-9]|2[01]|2[3-9]|[3-9]\d)\s*px"
)
# Token reference pattern: var(--space-...) or var(--font-size-...)
_TOKEN_REF = re.compile(r"var\(--(?:space|font-size)-\w+\)")


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
    assert brace != -1, f"no opening brace found for {name}"
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str, src: str = PROJECT_HTML) -> str:
    """Return the body of the first CSS rule matching selector exactly."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(src)
    assert m is not None, f"CSS rule for `{selector}` not found in project.html"
    return m.group(1)


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — Ticket card anatomy: id + title + status chip + agent + size
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__ticket_row_has_id_field():
    """_smgmtTicketRowHtml emits the ticket number link (.smgmt-ticket-num)."""
    body = _fn_body("_smgmtTicketRowHtml")
    assert "smgmt-ticket-num" in body, "ticket row must render the id (#num) field"


def test_board_redesign__ticket_row_has_title_field():
    """_smgmtTicketRowHtml emits the ticket title span."""
    body = _fn_body("_smgmtTicketRowHtml")
    assert "smgmt-ticket-title" in body, "ticket row must render the title field"


def test_board_redesign__ticket_row_has_status_chip():
    """_smgmtTicketRowHtml emits the status chip (.smgmt-ticket-status)."""
    body = _fn_body("_smgmtTicketRowHtml")
    assert "smgmt-ticket-status" in body, "ticket row must render the status/label chip"


def test_board_redesign__ticket_row_has_size_field():
    """_smgmtTicketRowHtml emits size information (size pill or estimate badge)."""
    body = _fn_body("_smgmtTicketRowHtml")
    assert "smgmt-ticket-size-pill" in body or "estimateBadgeHtml" in body, (
        "ticket row must render the size field"
    )


def test_board_redesign__ticket_row_has_agent_field():
    """_smgmtTicketRowHtml emits an agent tag derived from ticket status (AC1).

    In the planning view the live 'agent' field is absent, but the status tells
    us which agent role is handling the ticket: in-progress → coder, sit → tester.
    The rendered HTML must include a smgmt-ticket-agent-tag element.
    """
    body = _fn_body("_smgmtTicketRowHtml")
    assert "smgmt-ticket-agent-tag" in body, (
        "ticket row must render an agent chip (smgmt-ticket-agent-tag) derived from status"
    )


def test_board_redesign__ticket_row_agent_coder_for_in_progress():
    """Agent chip is 'coder' when status is 'in-progress'."""
    body = _fn_body("_smgmtTicketRowHtml")
    # Must derive agent from status; 'coder' must appear in the logic
    assert "coder" in body.lower(), (
        "_smgmtTicketRowHtml must map in-progress status to coder agent"
    )


def test_board_redesign__ticket_row_agent_tester_for_sit():
    """Agent chip is 'tester' when status is 'sit'."""
    body = _fn_body("_smgmtTicketRowHtml")
    assert "tester" in body.lower(), (
        "_smgmtTicketRowHtml must map sit status to tester agent"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — Level separators are visually distinct and use tokens
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__level_sep_gap_uses_token():
    """Level separator gap uses a CSS token (var(--space-*)), not a raw px value."""
    rule = _css_rule(".level-sep")
    assert "var(--space-" in rule, (
        ".level-sep gap must use var(--space-*) token, not a raw pixel value"
    )
    # Specifically the gap property must not contain a plain pixel value
    gap_match = re.search(r"gap:\s*([^;]+)", rule)
    if gap_match:
        gap_val = gap_match.group(1).strip()
        assert "var(" in gap_val, f".level-sep gap must use a token, got: {gap_val}"


def test_board_redesign__level_sep_padding_uses_token():
    """Level separator padding uses CSS tokens."""
    rule = _css_rule(".level-sep")
    padding_match = re.search(r"padding:\s*([^;]+)", rule)
    if padding_match:
        pad_val = padding_match.group(1).strip()
        assert "var(" in pad_val, f".level-sep padding must use token vars, got: {pad_val}"


def test_board_redesign__level_sep_margin_uses_token():
    """Level separator margin uses CSS tokens."""
    rule = _css_rule(".level-sep")
    margin_match = re.search(r"margin:\s*([^;]+)", rule)
    if margin_match:
        margin_val = margin_match.group(1).strip()
        # 0 is valid without a token; any non-zero value must be a token
        parts = margin_val.split()
        for p in parts:
            if p != "0" and not p.startswith("var("):
                raise AssertionError(
                    f".level-sep margin has non-token value '{p}' in '{margin_val}'"
                )


def test_board_redesign__level_sep_visually_distinct():
    """Level separator has a visual treatment (background or distinct border) to stand out."""
    rule = _css_rule(".level-sep")
    has_bg = "background" in rule
    has_border = "border" in rule
    assert has_bg or has_border, (
        ".level-sep must have a background or border treatment to be visually distinct "
        "from card rows and column headers"
    )


def test_board_redesign__level_sep_font_size_uses_token():
    """Level separator font-size uses a CSS token."""
    rule = _css_rule(".level-sep")
    fs_match = re.search(r"font-size:\s*([^;]+)", rule)
    if fs_match:
        fs_val = fs_match.group(1).strip()
        assert "var(" in fs_val, f".level-sep font-size must use a token, got: {fs_val}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — Section headers have clear typographic hierarchy using tokens
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__section_label_font_size_uses_token():
    """Section label (.smgmt-section-label) font-size uses a CSS token."""
    rule = _css_rule(".smgmt-section-label")
    fs_match = re.search(r"font-size:\s*([^;]+)", rule)
    assert fs_match, ".smgmt-section-label must have font-size property"
    fs_val = fs_match.group(1).strip()
    assert "var(" in fs_val, (
        f".smgmt-section-label font-size must use var(--font-size-*) token, got: {fs_val}"
    )


def test_board_redesign__section_label_margin_uses_tokens():
    """Section label margin uses CSS space tokens."""
    rule = _css_rule(".smgmt-section-label")
    margin_match = re.search(r"margin:\s*([^;]+)", rule)
    if margin_match:
        margin_val = margin_match.group(1).strip()
        parts = margin_val.split()
        for p in parts:
            if p != "0" and not p.startswith("var("):
                raise AssertionError(
                    f".smgmt-section-label margin has non-token value '{p}' in '{margin_val}'"
                )


def test_board_redesign__section_label_gap_uses_token():
    """Section label gap uses a CSS token."""
    rule = _css_rule(".smgmt-section-label")
    gap_match = re.search(r"gap:\s*([^;]+)", rule)
    if gap_match:
        gap_val = gap_match.group(1).strip()
        assert "var(" in gap_val, (
            f".smgmt-section-label gap must use var(--space-*) token, got: {gap_val}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — No off-scale raw pixel spacing in board CSS rules
# ═══════════════════════════════════════════════════════════════════════════════

def _base_ticket_rule(src: str = PROJECT_HTML) -> str:
    """Return the body of the base (non-compound) .smgmt-ticket rule."""
    # Match only `    .smgmt-ticket {` — not a compound selector like `.foo .smgmt-ticket`
    pat = re.compile(r"(?:^|\n)\s+\.smgmt-ticket\s*\{([^{}]+)\}", re.MULTILINE)
    m = pat.search(src)
    assert m is not None, "base .smgmt-ticket rule not found"
    return m.group(1)


def test_board_redesign__ticket_row_gap_uses_token():
    """.smgmt-ticket gap uses a CSS token."""
    rule = _base_ticket_rule()
    gap_match = re.search(r"gap:\s*([^;]+)", rule)
    if gap_match:
        gap_val = gap_match.group(1).strip()
        assert "var(" in gap_val, (
            f".smgmt-ticket gap must use a token var, got: {gap_val}"
        )


def test_board_redesign__ticket_row_padding_uses_tokens():
    """.smgmt-ticket padding uses CSS tokens (no off-scale raw px)."""
    rule = _base_ticket_rule()
    padding_match = re.search(r"padding:\s*([^;]+)", rule)
    if padding_match:
        pad_val = padding_match.group(1).strip()
        assert "var(" in pad_val, (
            f".smgmt-ticket padding must use var(--space-*) tokens, got: {pad_val}"
        )


def test_board_redesign__ticket_status_chip_font_size_uses_token():
    """.smgmt-ticket-status font-size uses a CSS token."""
    rule = _css_rule(".smgmt-ticket-status")
    fs_match = re.search(r"font-size:\s*([^;]+)", rule)
    if fs_match:
        fs_val = fs_match.group(1).strip()
        assert "var(" in fs_val, (
            f".smgmt-ticket-status font-size must use a token, got: {fs_val}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — Dark theme applied throughout the board view
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__dark_theme_on_html_element():
    """The <html> element carries data-theme=\"dark\" so the board is dark-themed."""
    assert 'data-theme="dark"' in PROJECT_HTML, (
        '<html lang="en" data-theme="dark"> must be present so board loads in dark mode'
    )


def test_board_redesign__newly_modified_css_uses_tokens_not_hex():
    """The CSS rules being redesigned (level-sep, section-label) must not hardcode hex.

    These specific rules must use var(--*) tokens for all color values so the dark
    theme override in tokens.css applies correctly.
    """
    hex_re = re.compile(r"(?<!\w)#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])")
    for selector in (".level-sep", ".smgmt-section-label"):
        rule = _css_rule(selector)
        # Strip comments and rgba() calls before checking
        stripped = re.sub(r"/\*.*?\*/", "", rule, flags=re.DOTALL)
        stripped = re.sub(r"rgba?\([^)]+\)", "", stripped)
        for m in hex_re.finditer(stripped):
            raise AssertionError(
                f"CSS rule `{selector}` must use var(--*) tokens, not hardcoded hex: {m.group()}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — Live-update behavior preserved
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__smgmt_render_exists():
    """_smgmtRender is still exported from board-render.js."""
    assert "export function _smgmtRender(" in BOARD_JS, (
        "_smgmtRender must remain exported from board-render.js"
    )


def test_board_redesign__load_sprint_mgmt_calls_live_poll_restart():
    """loadSprintMgmt still wires _smgmtLivePollRestart for live update behavior."""
    body = _fn_body("loadSprintMgmt")
    assert "_smgmtLivePollRestart" in body, (
        "loadSprintMgmt must still call _smgmtLivePollRestart to preserve live update behavior"
    )


def test_board_redesign__smgmt_render_calls_update_subnav():
    """_smgmtRender still calls _smgmtUpdateSubnav on every render."""
    body = _fn_body("_smgmtRender")
    assert "_smgmtUpdateSubnav" in body, (
        "_smgmtRender must still call _smgmtUpdateSubnav to keep the subnav in sync"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — npm run build artifact present
# ═══════════════════════════════════════════════════════════════════════════════

def test_board_redesign__bundle_exists():
    """static/dist/bundle.js exists (built via npm run build)."""
    assert BUNDLE.exists(), "static/dist/bundle.js must exist after npm run build"


def test_board_redesign__bundle_is_non_empty():
    """static/dist/bundle.js is non-empty."""
    assert BUNDLE.stat().st_size > 0, "static/dist/bundle.js must not be empty"


def test_board_redesign__bundle_contains_board_render():
    """bundle.js contains code from board-render.js (e.g. _smgmtTicketRowHtml)."""
    bundle_text = BUNDLE.read_text(encoding="utf-8", errors="replace")
    assert "_smgmtTicketRowHtml" in bundle_text or "smgmt-ticket-agent-tag" in bundle_text, (
        "bundle.js must include board-render.js output"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — Vanilla JS only — no framework dependencies in sprint-board source
# ═══════════════════════════════════════════════════════════════════════════════

FRAMEWORK_IMPORTS = ["react", "vue", "svelte", "angular", "preact", "solid-js"]


def test_board_redesign__no_framework_imports_in_sprint_board():
    """Sprint-board JS files must not import external framework packages."""
    for js_file in SPRINT_BOARD_DIR.glob("*.js"):
        content = js_file.read_text(encoding="utf-8")
        for fw in FRAMEWORK_IMPORTS:
            # Check for ESM import of a framework
            assert not re.search(
                rf'from\s+["\'](?:https?://[^/]*)?{re.escape(fw)}["\']',
                content,
                re.IGNORECASE,
            ), f"{js_file.name} must not import the '{fw}' framework"

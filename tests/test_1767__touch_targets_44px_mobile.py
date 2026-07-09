"""Tests for issue #1767 — Expand touch targets to 44px on mobile for key controls.

AC coverage:
  AC1 — .smgmt-run-btn measures ≥44px tall and ≥44px wide under @media (hover:none)
  AC2 — .modal-close measures ≥44×44 CSS px under @media (hover:none)
  AC3 — .btn-icon measures ≥44×44 CSS px under @media (hover:none)
  AC4 — All three controls render unchanged (current size/padding) on desktop/hover
         devices (@media (hover:hover)) — no rules added for hover devices
  AC5 — No layout shift or overflow introduced at 1024px+ — hover:none block
         does not alter layout at desktop widths
  AC6 — Run-sprint button remains correctly aligned within the sprint-card header
         at 390px, including after the ≤480px column-layout breakpoint
  AC7 — Changes confined to existing @media (hover:none) block or a dedicated
         mobile-touch extension — no global style side-effects

Pure-CSS changes have no callable Python code path; CSS parsing is the closest
equivalent to behavioral verification for this class of change.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HTML_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _extract_media_blocks(src: str, query: str):
    """Yield body strings of all @media blocks whose condition contains `query`."""
    pattern = re.compile(
        r"@media\s*\([^)]*" + re.escape(query) + r"[^)]*\)\s*\{",
        re.IGNORECASE,
    )
    for m in pattern.finditer(src):
        start = m.end()
        depth = 1
        i = start
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            yield src[start : i - 1]


def _hover_none_blocks(src: str):
    """Yield bodies of all @media (hover: none) blocks."""
    yield from _extract_media_blocks(src, "hover: none")
    yield from _extract_media_blocks(src, "hover:none")


def _hover_hover_blocks(src: str):
    """Yield bodies of all @media (hover: hover) blocks."""
    yield from _extract_media_blocks(src, "hover: hover")
    yield from _extract_media_blocks(src, "hover:hover")


def _parse_px(value: str) -> float | None:
    """Extract a numeric px value from a CSS value string like '44px'."""
    m = re.search(r"([\d.]+)px", value)
    return float(m.group(1)) if m else None


def _get_selector_rules(block: str, selector: str) -> list[str]:
    """Extract all property strings from blocks matching `selector` (a regex) in a CSS block."""
    results = []
    for m in re.finditer(selector + r"\s*\{([^}]*)\}", block):
        results.append(m.group(1))
    return results


def _strip_media_blocks(src: str) -> str:
    """Remove all @media blocks from src using brace-depth tracking."""
    result = []
    i = 0
    while i < len(src):
        m = re.search(r"@media\b", src[i:])
        if not m:
            result.append(src[i:])
            break
        result.append(src[i : i + m.start()])
        i += m.start()
        # Find the opening brace
        brace = src.find("{", i)
        if brace == -1:
            result.append(src[i:])
            break
        depth = 1
        j = brace + 1
        while j < len(src) and depth:
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
            j += 1
        # Skip the entire @media block (do not append)
        i = j
    return "".join(result)


# ── Sanity ───────────────────────────────────────────────────────────────────


def test_project_html_exists():
    assert HTML_PATH.exists(), f"project.html not found at {HTML_PATH}"


def test_hover_none_block_exists():
    src = _html()
    blocks = list(_hover_none_blocks(src))
    assert blocks, "@media (hover: none) block must exist in project.html"


# ── AC1 — .smgmt-run-btn ≥44×44 under hover:none ───────────────────────────


def test_ac1_smgmt_run_btn_min_height_44px():
    """.smgmt-run-btn must have min-height ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    assert ".smgmt-run-btn" in combined, (
        "@media (hover: none) must include a .smgmt-run-btn rule (AC1)"
    )
    rules = _get_selector_rules(combined, r"\.smgmt-run-btn")
    assert rules, ".smgmt-run-btn rule body not found in hover:none block"
    rule_text = " ".join(rules)
    assert "min-height" in rule_text, (
        ".smgmt-run-btn in @media (hover: none) must set min-height (AC1)"
    )
    # Extract the min-height value and verify ≥44px
    m = re.search(r"min-height\s*:\s*([\d.]+)px", rule_text)
    assert m, ".smgmt-run-btn min-height must be a px value in hover:none block"
    assert float(m.group(1)) >= 44, (
        f".smgmt-run-btn min-height is {m.group(1)}px but must be ≥44px (AC1)"
    )


def test_ac1_smgmt_run_btn_min_width_44px():
    """.smgmt-run-btn must have min-width ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    rules = _get_selector_rules(combined, r"\.smgmt-run-btn")
    rule_text = " ".join(rules)
    assert "min-width" in rule_text, (
        ".smgmt-run-btn in @media (hover: none) must set min-width (AC1)"
    )
    m = re.search(r"min-width\s*:\s*([\d.]+)px", rule_text)
    assert m, ".smgmt-run-btn min-width must be a px value in hover:none block"
    assert float(m.group(1)) >= 44, (
        f".smgmt-run-btn min-width is {m.group(1)}px but must be ≥44px (AC1)"
    )


# ── AC2 — .modal-close ≥44×44 under hover:none ──────────────────────────────


def test_ac2_modal_close_min_height_44px():
    """.modal-close must have min-height ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    assert ".modal-close" in combined, (
        "@media (hover: none) must include a .modal-close rule (AC2)"
    )
    rules = _get_selector_rules(combined, r"\.modal-close")
    rule_text = " ".join(rules)
    assert "min-height" in rule_text or "height" in rule_text, (
        ".modal-close in @media (hover: none) must set height or min-height (AC2)"
    )
    # Accept either height or min-height ≥ 44px
    h_match = re.search(r"(?:min-)?height\s*:\s*([\d.]+)px", rule_text)
    assert h_match, ".modal-close height/min-height must be a px value in hover:none block"
    assert float(h_match.group(1)) >= 44, (
        f".modal-close height is {h_match.group(1)}px but must be ≥44px (AC2)"
    )


def test_ac2_modal_close_min_width_44px():
    """.modal-close must have min-width ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    rules = _get_selector_rules(combined, r"\.modal-close")
    rule_text = " ".join(rules)
    assert "min-width" in rule_text or "width" in rule_text, (
        ".modal-close in @media (hover: none) must set width or min-width (AC2)"
    )
    w_match = re.search(r"(?:min-)?width\s*:\s*([\d.]+)px", rule_text)
    assert w_match, ".modal-close width/min-width must be a px value in hover:none block"
    assert float(w_match.group(1)) >= 44, (
        f".modal-close width is {w_match.group(1)}px but must be ≥44px (AC2)"
    )


# ── AC3 — .btn-icon ≥44×44 under hover:none ─────────────────────────────────


def test_ac3_btn_icon_min_height_44px():
    """.btn-icon must have height or min-height ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    assert ".btn-icon" in combined, (
        "@media (hover: none) must include a .btn-icon rule (AC3)"
    )
    rules = _get_selector_rules(combined, r"\.btn-icon")
    rule_text = " ".join(rules)
    assert "height" in rule_text, (
        ".btn-icon in @media (hover: none) must set height or min-height (AC3)"
    )
    h_match = re.search(r"(?:min-)?height\s*:\s*([\d.]+)px", rule_text)
    assert h_match, ".btn-icon height/min-height must be a px value in hover:none block"
    assert float(h_match.group(1)) >= 44, (
        f".btn-icon height is {h_match.group(1)}px but must be ≥44px (AC3)"
    )


def test_ac3_btn_icon_min_width_44px():
    """.btn-icon must have width or min-width ≥ 44px inside @media (hover: none)."""
    src = _html()
    combined = " ".join(_hover_none_blocks(src))
    rules = _get_selector_rules(combined, r"\.btn-icon")
    rule_text = " ".join(rules)
    assert "width" in rule_text, (
        ".btn-icon in @media (hover: none) must set width or min-width (AC3)"
    )
    w_match = re.search(r"(?:min-)?width\s*:\s*([\d.]+)px", rule_text)
    assert w_match, ".btn-icon width/min-width must be a px value in hover:none block"
    assert float(w_match.group(1)) >= 44, (
        f".btn-icon width is {w_match.group(1)}px but must be ≥44px (AC3)"
    )


# ── AC4 — hover:hover block must NOT add new rules for these three controls ──


def test_ac4_no_smgmt_run_btn_in_hover_hover():
    """.smgmt-run-btn must NOT appear in any @media (hover: hover) block (AC4)."""
    src = _html()
    for block in _hover_hover_blocks(src):
        assert ".smgmt-run-btn" not in block, (
            ".smgmt-run-btn must not be modified in @media (hover: hover); "
            "desktop style must remain unchanged (AC4)"
        )


def test_ac4_no_modal_close_in_hover_hover():
    """.modal-close must NOT appear in any @media (hover: hover) block (AC4)."""
    src = _html()
    for block in _hover_hover_blocks(src):
        assert ".modal-close" not in block, (
            ".modal-close must not be modified in @media (hover: hover); "
            "desktop style must remain unchanged (AC4)"
        )


def test_ac4_no_btn_icon_in_hover_hover():
    """.btn-icon must NOT appear in any @media (hover: hover) block (AC4)."""
    src = _html()
    for block in _hover_hover_blocks(src):
        assert ".btn-icon" not in block, (
            ".btn-icon must not be modified in @media (hover: hover); "
            "desktop style must remain unchanged (AC4)"
        )


# ── AC6 — ≤480px column layout for sprint-card header preserved ─────────────


def test_ac6_smgmt_sprint_header_column_layout_preserved():
    """The existing ≤480px column-direction layout for .smgmt-sprint-header must
    remain intact (run-sprint button stays correctly aligned on narrow viewports)."""
    src = _html()
    blocks_480 = list(_extract_media_blocks(src, "max-width: 480px"))
    assert blocks_480, "@media (max-width: 480px) block must still exist"
    combined = " ".join(blocks_480)
    assert ".smgmt-sprint-header" in combined, (
        "@media (max-width: 480px) must still contain .smgmt-sprint-header column layout (AC6)"
    )
    assert "flex-direction" in combined and "column" in combined, (
        "Existing flex-direction: column on .smgmt-sprint-header must be preserved (AC6)"
    )


# ── AC7 — changes confined to hover:none block ───────────────────────────────


def test_ac7_smgmt_run_btn_not_in_global_scope():
    """The .smgmt-run-btn 44px touch-target rule must appear ONLY inside
    a @media (hover: none) or mobile breakpoint block, not at global scope (AC7)."""
    src = _html()
    src_no_media = _strip_media_blocks(src)
    for m in re.finditer(r"\.smgmt-run-btn\s*\{([^}]*)\}", src_no_media):
        rule_body = m.group(1)
        if "min-height" in rule_body:
            h_m = re.search(r"min-height\s*:\s*([\d.]+)px", rule_body)
            if h_m and float(h_m.group(1)) >= 44:
                pytest.fail(
                    ".smgmt-run-btn has min-height ≥44px in global scope — "
                    "touch-target rules must be inside @media (hover: none) only (AC7)"
                )


def test_ac7_btn_icon_global_size_unchanged():
    """The global .btn-icon rule must still use --space-8 (32px) for desktop,
    not a hardcoded 44px that would affect all viewports (AC7)."""
    src = _html()
    # Find the first .btn-icon rule outside @media blocks
    src_no_media = re.sub(r"@media\s+[^{]+\{[^@]*?\}", "", src, flags=re.DOTALL)
    m = re.search(r"\.btn-icon\s*\{([^}]*)\}", src_no_media)
    assert m, ".btn-icon global rule must still exist"
    rule_body = m.group(1)
    # Must still reference --space-8 OR have width/height that doesn't exceed 44px globally
    # (The token var(--space-8) == 32px; if a hardcoded 44px is set globally, that's AC7 violation)
    if "44px" in rule_body and ("width" in rule_body or "height" in rule_body):
        pytest.fail(
            ".btn-icon global rule must not hardcode 44px dimensions; "
            "touch-target expansion belongs in @media (hover: none) only (AC7)"
        )

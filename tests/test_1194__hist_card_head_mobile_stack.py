"""
Tests for issue #1194 — Stack Sprint History card header on mobile.
All ACs are CSS static-analysis checks against project.html.
"""
import re
import pytest

HTML_FILE = "apps/dashboard/static/project.html"


@pytest.fixture(scope="module")
def html_source():
    with open(HTML_FILE, encoding="utf-8") as f:
        return f.read()


def _extract_media_600(source):
    """Return all @media (max-width:600px) block bodies concatenated using brace counting."""
    header_re = re.compile(r"@media\s*\(\s*max-width\s*:\s*600px\s*\)")
    blocks = []
    pos = 0
    while True:
        m = header_re.search(source, pos)
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
        blocks.append(source[start + 1 : i - 1])
        pos = i
    return "\n".join(blocks)


# AC1 & AC2: At 375px and 600px — .hist-card-head stacks into a column inside @media (max-width:600px)
def test_hist_card_head_flex_direction_column_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head\b[^{]*\{[^}]*\bflex-direction\s*:\s*column", block
    ), ".hist-card-head must have flex-direction:column inside @media (max-width:600px)"


# AC2: At 600px — .hist-card-head has gap: 8px inside @media (max-width:600px)
def test_hist_card_head_gap_8px_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head\b[^{]*\{[^}]*\bgap\s*:\s*8px", block
    ), ".hist-card-head must have gap:8px inside @media (max-width:600px)"


# AC3: .hist-card-head-left spans full width at ≤600px
def test_hist_card_head_left_full_width_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head-left\b[^{]*\{[^}]*\bflex-basis\s*:\s*100%", block
    ), ".hist-card-head-left must have flex-basis:100% inside @media (max-width:600px)"


# AC3: .hist-card-head-right spans full width at ≤600px
def test_hist_card_head_right_full_width_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head-right\b[^{]*\{[^}]*\bflex-basis\s*:\s*100%", block
    ), ".hist-card-head-right must have flex-basis:100% inside @media (max-width:600px)"


# AC4: No horizontal overflow — .hist-card-head has overflow-x:hidden at ≤600px
def test_hist_card_head_no_overflow_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head\b[^{]*\{[^}]*\boverflow-x\s*:\s*hidden", block
    ), ".hist-card-head must have overflow-x:hidden inside @media (max-width:600px)"


# AC5: Desktop layout unchanged — base .hist-card-head still uses grid (not flex-direction:column)
def test_hist_card_head_base_grid_layout_unchanged(html_source):
    # Use all matches and find the standalone base rule (has display:grid)
    matches = re.findall(r"\.hist-card-head\s*\{([^}]+)\}", html_source)
    assert matches, "Could not find any .hist-card-head { } rule"
    grid_match = next((m for m in matches if re.search(r"\bdisplay\s*:\s*grid", m)), None)
    assert grid_match is not None, (
        "Base .hist-card-head must still have display:grid for desktop layout"
    )
    assert not re.search(r"\bflex-direction\s*:\s*column", grid_match), (
        "Base .hist-card-head must NOT have flex-direction:column — only applies at ≤600px"
    )


# AC6: Structural check — action button classes remain present in the HTML
def test_hist_head_btn_class_present(html_source):
    assert "hist-head-btn" in html_source, (
        "hist-head-btn class must remain present in the HTML (action buttons must not be removed)"
    )


# AC6: .hist-head-actions class still present so interactions are preserved
def test_hist_head_actions_class_present(html_source):
    assert "hist-head-actions" in html_source, (
        "hist-head-actions class must remain present (action interactions must not be broken)"
    )

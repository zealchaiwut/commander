"""
Tests for issue #1194 — Stack Sprint History card header on mobile (≤600px).
Static CSS analysis against project.html — no live server required.
"""
import re
import pytest

HTML_FILE = "apps/dashboard/static/project.html"


@pytest.fixture(scope="module")
def html_source():
    with open(HTML_FILE, encoding="utf-8") as f:
        return f.read()


def _extract_media_600(source):
    """Return all @media (max-width:600px) block bodies concatenated."""
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


def test_hist_card_stack__375px_viewport_stacks_to_column(html_source):
    """AC: At ≤600px, .hist-card-head stacks into a column (flex-direction: column)."""
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.hist-card-head\b[^{]*\{[^}]*flex-direction\s*:\s*column", block), (
        ".hist-card-head must have flex-direction:column inside @media (max-width:600px)"
    )


def test_hist_card_stack__600px_viewport_column_with_gap(html_source):
    """AC: At ≤600px, .hist-card-head has gap:8px between stacked sections."""
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.hist-card-head\b[^{]*\{[^}]*\bgap\s*:\s*8px", block), (
        ".hist-card-head must have gap:8px inside @media (max-width:600px)"
    )


def test_hist_card_stack__full_width_flex_basis_600px(html_source):
    """AC: .hist-card-head-left and .hist-card-head-right each span full width at ≤600px."""
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-card-head-(?:left|right)\b[^{]*\{[^}]*(?:flex-basis|width)\s*:\s*100%",
        block,
    ), ".hist-card-head-left/.hist-card-head-right must have flex-basis:100% or width:100% inside @media (max-width:600px)"


def test_hist_card_stack__no_overflow_at_600px(html_source):
    """AC: No horizontal scrollbar/overflow on .hist-card-head at ≤600px."""
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.hist-card-head\b[^{]*\{[^}]*overflow(?:-x)?\s*:\s*hidden", block), (
        ".hist-card-head must have overflow(-x):hidden inside @media (max-width:600px)"
    )


def test_hist_card_stack__desktop_unchanged(html_source):
    """AC: Desktop layout (>600px) is visually unchanged — .hist-card-head uses grid."""
    no_media = re.sub(r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", html_source)
    base_matches = re.findall(r"\.hist-card-head\s*\{([^}]+)\}", no_media)
    assert base_matches, "Could not find any base .hist-card-head { } rule"
    found = any(
        re.search(r"grid-template-columns\s*:\s*minmax\(0,\s*1fr\)\s*auto", m)
        for m in base_matches
    )
    assert found, (
        "Base .hist-card-head must have grid-template-columns:minmax(0,1fr) auto for desktop"
    )


def test_hist_card_stack__button_interactions_functional(html_source):
    """AC: All badge/action button interactions still functional — classes are present."""
    assert ".hist-head-actions" in html_source, ".hist-head-actions class must be defined"
    assert ".hist-head-btn" in html_source, ".hist-head-btn class must be defined"

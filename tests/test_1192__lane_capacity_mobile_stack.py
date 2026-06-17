"""
Tests for issue #1192 — Stack lane capacity dots on mobile screens.
All ACs are CSS static-analysis checks against project.html.
"""
import re
import pytest

HTML_FILE = "apps/dashboard/static/project.html"


@pytest.fixture(scope="module")
def html_source():
    with open(HTML_FILE, encoding="utf-8") as f:
        return f.read()


def _extract_media_640(source):
    """Return all @media (max-width:640px) block bodies concatenated using brace counting."""
    header_re = re.compile(r"@media\s*\(\s*max-width\s*:\s*640px\s*\)")
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


# AC1 & AC2: At 375px and 640px — .lane-capacity has flex-direction: column inside @media (max-width:640px)
def test_lane_capacity_flex_direction_column_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(
        r"\.lane-capacity\b[^{]*\{[^}]*\bflex-direction\s*:\s*column", block
    ), ".lane-capacity must have flex-direction:column inside @media (max-width:640px)"


# AC1 & AC2: At 375px and 640px — .lane-capacity has gap: 4px inside @media (max-width:640px)
def test_lane_capacity_gap_4px_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(
        r"\.lane-capacity\b[^{]*\{[^}]*\bgap\s*:\s*4px", block
    ), ".lane-capacity must have gap:4px inside @media (max-width:640px)"


# AC4: At 375px and 640px — .lane-dots has flex-wrap: wrap inside @media (max-width:640px)
def test_lane_dots_flex_wrap_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(
        r"\.lane-dots\b[^{]*\{[^}]*\bflex-wrap\s*:\s*wrap", block
    ), ".lane-dots must have flex-wrap:wrap inside @media (max-width:640px)"


# AC3 & AC6: Desktop layout unchanged — base .lane-capacity still has display:flex
def test_lane_capacity_base_display_flex_unchanged(html_source):
    base_match = re.search(r"\.lane-capacity\s*\{([^}]+)\}", html_source)
    assert base_match, "Could not find base .lane-capacity { } rule"
    base_css = base_match.group(1)
    assert re.search(r"\bdisplay\s*:\s*flex", base_css), (
        "Base .lane-capacity must still have display:flex for desktop layout"
    )


# AC3: Desktop layout unchanged — base .lane-capacity must NOT have flex-direction:column
def test_lane_capacity_base_no_column_direction(html_source):
    base_match = re.search(r"\.lane-capacity\s*\{([^}]+)\}", html_source)
    assert base_match, "Could not find base .lane-capacity { } rule"
    base_css = base_match.group(1)
    assert not re.search(r"\bflex-direction\s*:\s*column", base_css), (
        "Base .lane-capacity must NOT have flex-direction:column — desktop layout must be unchanged"
    )


# AC6: Desktop layout — base .lane-dots must NOT have flex-wrap:wrap
def test_lane_dots_base_no_flex_wrap(html_source):
    base_match = re.search(r"\.lane-dots\s*\{([^}]+)\}", html_source)
    assert base_match, "Could not find base .lane-dots { } rule"
    base_css = base_match.group(1)
    assert not re.search(r"\bflex-wrap\s*:\s*wrap", base_css), (
        "Base .lane-dots must NOT have flex-wrap:wrap — only applies inside @media (max-width:640px)"
    )

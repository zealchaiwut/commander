"""
Tests for issue #1191 — Wrap rail nodes and truncate titles on mobile.
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


# AC6: .node has flex-wrap: wrap inside @media (max-width: 600px)
def test_node_flex_wrap_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.node\b[^{]*\{[^}]*\bflex-wrap\s*:\s*wrap", block), (
        ".node must have flex-wrap:wrap inside @media (max-width:600px)"
    )


# AC7: .node-title has flex: 1 1 100% inside @media (max-width: 600px)
def test_node_title_flex_100pct_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.node-title\b[^{]*\{[^}]*\bflex\s*:\s*1\s+1\s+100%", block), (
        ".node-title must have flex:1 1 100% inside @media (max-width:600px)"
    )


# AC7: .node-title has min-width: 0 inside @media (max-width: 600px)
def test_node_title_min_width_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.node-title\b[^{]*\{[^}]*\bmin-width\s*:\s*0", block), (
        ".node-title must have min-width:0 inside @media (max-width:600px)"
    )


# AC7: .node-title has overflow: hidden inside @media (max-width: 600px)
def test_node_title_overflow_hidden_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.node-title\b[^{]*\{[^}]*\boverflow\s*:\s*hidden", block), (
        ".node-title must have overflow:hidden inside @media (max-width:600px)"
    )


# AC7: .node-title has text-overflow: ellipsis inside @media (max-width: 600px)
def test_node_title_text_overflow_ellipsis_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\.node-title\b[^{]*\{[^}]*\btext-overflow\s*:\s*ellipsis", block), (
        ".node-title must have text-overflow:ellipsis inside @media (max-width:600px)"
    )


# AC8: Badges have flex: 0 1 auto inside @media (max-width: 600px)
def test_badge_flex_0_1_auto_in_600px_breakpoint(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(r"\bflex\s*:\s*0\s+1\s+auto", block), (
        "Badge elements must have flex:0 1 auto inside @media (max-width:600px)"
    )


# AC5: flex-wrap:wrap on .node is ONLY inside the breakpoint, not in base CSS
def test_node_flex_wrap_not_in_base_css(html_source):
    # The base .node rule should not contain flex-wrap
    base_match = re.search(r"\.node\s*\{([^}]+)\}", html_source)
    assert base_match, "Could not find base .node { } rule"
    base_css = base_match.group(1)
    assert "flex-wrap" not in base_css, (
        "flex-wrap must not appear in the base .node rule — only inside @media (max-width:600px)"
    )


# AC4: Desktop layout unchanged — base .node rule still has display:flex without flex-wrap
def test_node_base_display_flex_unchanged(html_source):
    base_match = re.search(r"\.node\s*\{([^}]+)\}", html_source)
    assert base_match, "Could not find base .node { } rule"
    base_css = base_match.group(1)
    assert re.search(r"\bdisplay\s*:\s*flex", base_css), (
        "Base .node rule must still have display:flex for desktop layout"
    )


# AC1 & AC2 (structural proxy): .node-title white-space:nowrap is present in base CSS
# so ellipsis works at 375px when title overflows the 100% flex container
def test_node_title_white_space_nowrap_in_base(html_source):
    title_match = re.search(r"\.node-title\s*\{([^}]+)\}", html_source)
    assert title_match, "Could not find base .node-title { } rule"
    base_css = title_match.group(1)
    assert re.search(r"\bwhite-space\s*:\s*nowrap", base_css), (
        ".node-title must have white-space:nowrap in base CSS so ellipsis fires on overflow"
    )

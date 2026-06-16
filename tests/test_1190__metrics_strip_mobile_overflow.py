"""
Tests for issue #1190 — Fix metrics strip overflow on mobile viewports.
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
        # Find the opening brace after the header
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
        blocks.append(source[start + 1:i - 1])
        pos = i
    return "\n".join(blocks)


# AC: .metrics gap reduces to 6px at max-width:640px
def test_metrics_gap_6px_at_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    # Should set .metrics { gap: 6px }
    assert re.search(r'\.metrics\s*\{[^}]*\bgap\s*:\s*6px', block), \
        ".metrics gap must be 6px inside @media (max-width:640px)"


# AC: .metric uses flex:1 1 auto at max-width:640px
def test_metric_flex_1_1_auto_at_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(r'\.metric\b[^{]*\{[^}]*\bflex\s*:\s*1\s+1\s+auto', block), \
        ".metric must have flex:1 1 auto inside @media (max-width:640px)"


# AC: .metric uses min-width:120px at max-width:640px
def test_metric_min_width_120px_at_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(r'\.metric\b[^{]*\{[^}]*\bmin-width\s*:\s*120px', block), \
        ".metric must have min-width:120px inside @media (max-width:640px)"


# AC: .metric-label font-size is 9px at max-width:640px
def test_metric_label_font_size_9px_at_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(r'\.metric-label\s*\{[^}]*\bfont-size\s*:\s*9px', block), \
        ".metric-label must have font-size:9px inside @media (max-width:640px)"


# AC: Desktop layout unchanged — .metrics rule outside mobile media query keeps gap:8px
def test_desktop_metrics_gap_unchanged(html_source):
    # The base .metrics rule (outside any media query) must still have gap:8px
    # Find the .metrics { ... } block that is NOT inside a media query
    # Strategy: locate all .metrics { ... } occurrences and check the base one
    base_pattern = r'\.metrics\s*\{\s*[^}]*\bgap\s*:\s*8px'
    assert re.search(base_pattern, html_source), \
        "Base .metrics rule (desktop) must keep gap:8px"


# AC: .metric-time flex-basis 230px preserved (time card remains visible without clipping)
def test_metric_time_flex_basis_preserved(html_source):
    assert re.search(r'\.metric-time\s*\{[^}]*flex\s*:\s*1\s+1\s+230px', html_source), \
        ".metric-time must still have flex:1 1 230px in base rules"


# AC: .metrics flex-wrap:wrap is present (enables multi-row wrapping on mobile)
def test_metrics_flex_wrap_wrap(html_source):
    assert re.search(r'\.metrics\s*\{[^}]*flex-wrap\s*:\s*wrap', html_source), \
        ".metrics must have flex-wrap:wrap to allow cards to wrap on narrow viewports"

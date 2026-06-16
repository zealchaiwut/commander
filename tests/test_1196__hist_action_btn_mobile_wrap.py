"""
Tests for issue #1196 — Wrap Sprint History action buttons for mobile touch targets.
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


def _remove_media_blocks(source):
    """Strip all @media blocks to isolate base styles."""
    return re.sub(r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", source)


# AC1: .hist-head-actions has flex-wrap:wrap inside @media (max-width:600px)
def test_hist_head_actions_flex_wrap_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-head-actions\b[^{]*\{[^}]*\bflex-wrap\s*:\s*wrap", block
    ), ".hist-head-actions must have flex-wrap:wrap inside @media (max-width:600px)"


# AC1: .hist-head-actions has gap:6px inside @media (max-width:600px)
def test_hist_head_actions_gap_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-head-actions\b[^{]*\{[^}]*\bgap\s*:\s*6px", block
    ), ".hist-head-actions must have gap:6px inside @media (max-width:600px)"


# AC1: .hist-verbs has flex-wrap:wrap inside @media (max-width:600px)
def test_hist_verbs_flex_wrap_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-verbs\b[^{]*\{[^}]*\bflex-wrap\s*:\s*wrap", block
    ), ".hist-verbs must have flex-wrap:wrap inside @media (max-width:600px)"


# AC1: .hist-verbs has gap:6px inside @media (max-width:600px)
def test_hist_verbs_gap_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-verbs\b[^{]*\{[^}]*\bgap\s*:\s*6px", block
    ), ".hist-verbs must have gap:6px inside @media (max-width:600px)"


# AC2: .hist-head-btn has flex:1 1 auto inside @media (max-width:600px)
def test_hist_head_btn_flex_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-head-btn\b[^{]*\{[^}]*\bflex\s*:\s*1\s+1\s+auto", block
    ), ".hist-head-btn must have flex:1 1 auto inside @media (max-width:600px)"


# AC2: .hist-head-btn has min-width:80px inside @media (max-width:600px)
def test_hist_head_btn_min_width_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-head-btn\b[^{]*\{[^}]*\bmin-width\s*:\s*80px", block
    ), ".hist-head-btn must have min-width:80px inside @media (max-width:600px)"


# AC2: .hist-head-btn has min-height:44px inside @media (max-width:600px)
def test_hist_head_btn_min_height_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-head-btn\b[^{]*\{[^}]*\bmin-height\s*:\s*44px", block
    ), ".hist-head-btn must have min-height:44px inside @media (max-width:600px)"


# AC2: .hist-verb has flex:1 1 auto inside @media (max-width:600px)
def test_hist_verb_flex_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-verb\b[^{]*\{[^}]*\bflex\s*:\s*1\s+1\s+auto", block
    ), ".hist-verb must have flex:1 1 auto inside @media (max-width:600px)"


# AC2: .hist-verb has min-width:80px inside @media (max-width:600px)
def test_hist_verb_min_width_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-verb\b[^{]*\{[^}]*\bmin-width\s*:\s*80px", block
    ), ".hist-verb must have min-width:80px inside @media (max-width:600px)"


# AC2 / AC5: .hist-verb has min-height:44px inside @media (max-width:600px)
def test_hist_verb_min_height_in_600px(html_source):
    block = _extract_media_600(html_source)
    assert block, "No @media (max-width:600px) block found"
    assert re.search(
        r"\.hist-verb\b[^{]*\{[^}]*\bmin-height\s*:\s*44px", block
    ), ".hist-verb must have min-height:44px inside @media (max-width:600px)"


# AC3 / AC4: No overflow-x:auto or scroll on .hist-head-actions (wraps, doesn't scroll)
def test_hist_head_actions_no_overflow_x(html_source):
    matches = re.findall(r"\.hist-head-actions\s*\{([^}]+)\}", html_source)
    for rule_body in matches:
        assert not re.search(r"\boverflow-x\s*:\s*(auto|scroll)", rule_body), (
            ".hist-head-actions must not introduce overflow-x:auto or scroll"
        )


# AC3 / AC4: No overflow-x:auto or scroll on .hist-verbs (wraps, doesn't scroll)
def test_hist_verbs_no_overflow_x(html_source):
    matches = re.findall(r"\.hist-verbs\s*\{([^}]+)\}", html_source)
    for rule_body in matches:
        assert not re.search(r"\boverflow-x\s*:\s*(auto|scroll)", rule_body), (
            ".hist-verbs must not introduce overflow-x:auto or scroll"
        )


# AC6: Base .hist-head-btn must NOT have min-height:44px (touch target only in mobile media)
def test_hist_head_btn_no_min_height_in_base(html_source):
    no_media = _remove_media_blocks(html_source)
    base_matches = re.findall(r"\.hist-head-btn\b[^{]*\{([^}]*)\}", no_media)
    for rule_body in base_matches:
        assert not re.search(r"\bmin-height\s*:\s*44px", rule_body), (
            ".hist-head-btn must NOT have min-height:44px in base styles — "
            "44px touch target only applies inside @media (max-width:600px)"
        )


# AC6: Base .hist-verb must NOT have min-height:44px (touch target only in mobile media)
def test_hist_verb_no_min_height_in_base(html_source):
    no_media = _remove_media_blocks(html_source)
    base_matches = re.findall(r"\.hist-verb\b[^{]*\{([^}]*)\}", no_media)
    for rule_body in base_matches:
        assert not re.search(r"\bmin-height\s*:\s*44px", rule_body), (
            ".hist-verb must NOT have min-height:44px in base styles — "
            "44px touch target only applies inside @media (max-width:600px)"
        )


# AC6: Base .hist-head-btn must NOT have min-width:80px (flex sizing only in mobile media)
def test_hist_head_btn_no_min_width_in_base(html_source):
    no_media = _remove_media_blocks(html_source)
    base_matches = re.findall(r"\.hist-head-btn\b[^{]*\{([^}]*)\}", no_media)
    for rule_body in base_matches:
        assert not re.search(r"\bmin-width\s*:\s*80px", rule_body), (
            ".hist-head-btn must NOT have min-width:80px in base styles — "
            "min-width:80px only applies inside @media (max-width:600px)"
        )


# AC6: Base .hist-verb must NOT have min-width:80px (flex sizing only in mobile media)
def test_hist_verb_no_min_width_in_base(html_source):
    no_media = _remove_media_blocks(html_source)
    base_matches = re.findall(r"\.hist-verb\b[^{]*\{([^}]*)\}", no_media)
    for rule_body in base_matches:
        assert not re.search(r"\bmin-width\s*:\s*80px", rule_body), (
            ".hist-verb must NOT have min-width:80px in base styles — "
            "min-width:80px only applies inside @media (max-width:600px)"
        )

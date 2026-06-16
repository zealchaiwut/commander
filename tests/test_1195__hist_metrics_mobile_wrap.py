"""
Tests for issue #1195 — Wrap Sprint History metrics row on narrow screens.
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


# AC1 & AC2: At 375px and 640px — .est-bar fills full row width inside @media (max-width:640px)
def test_est_bar_full_width_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    assert re.search(
        r"\.est-bar\b[^{]*\{[^}]*\bwidth\s*:\s*100%", block
    ), ".est-bar must have width:100% inside @media (max-width:640px)"


# AC1 & AC2: .est-bar should not flex-grow into remaining space — use flex:none or flex-grow:0
def test_est_bar_no_flex_grow_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    # Either flex:none or flex-grow:0 is acceptable; both prevent infinite growth
    has_flex_none = bool(re.search(
        r"\.est-bar\b[^{]*\{[^}]*\bflex\s*:\s*none", block
    ))
    has_flex_grow_0 = bool(re.search(
        r"\.est-bar\b[^{]*\{[^}]*\bflex-grow\s*:\s*0", block
    ))
    assert has_flex_none or has_flex_grow_0, (
        ".est-bar must have flex:none or flex-grow:0 inside @media (max-width:640px) "
        "to prevent it from only filling remaining row space"
    )


# AC4: .hist-badge flex-shrinks but does NOT stretch to fill the row (no flex-grow:1)
def test_hist_badge_flex_shrink_no_grow_in_640px(html_source):
    block = _extract_media_640(html_source)
    assert block, "No @media (max-width:640px) block found"
    # Must have flex-shrink applied
    assert re.search(
        r"\.hist-badge\b[^{]*\{[^}]*\bflex-shrink\s*:\s*[^;}\s]", block
    ), ".hist-badge must have flex-shrink set inside @media (max-width:640px)"
    # Must NOT have flex-grow:1 (badges must not stretch)
    badge_block_match = re.search(r"\.hist-badge\b[^{]*\{([^}]*)\}", block)
    if badge_block_match:
        badge_rules = badge_block_match.group(1)
        assert not re.search(r"\bflex-grow\s*:\s*1\b", badge_rules), (
            ".hist-badge must NOT have flex-grow:1 — badges should shrink but not stretch"
        )


# AC5: No overflow-x:auto or overflow-x:scroll on .hist-metrics (no scroll introduced)
def test_hist_metrics_no_horizontal_scroll(html_source):
    matches = re.findall(r"\.hist-metrics\s*\{([^}]+)\}", html_source)
    for rule_body in matches:
        assert not re.search(r"\boverflow-x\s*:\s*(auto|scroll)", rule_body), (
            ".hist-metrics must not have overflow-x:auto or overflow-x:scroll "
            "(no horizontal scroll should be introduced)"
        )


# AC6: Responsive rules are inside @media (max-width:640px) only — not in base styles
def test_est_bar_width_100_only_in_media_query(html_source):
    # Find all .est-bar rules OUTSIDE any @media block
    # We extract base rules by removing all @media blocks first
    no_media = re.sub(r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", html_source)
    base_matches = re.findall(r"\.est-bar\b[^{]*\{([^}]*)\}", no_media)
    for rule_body in base_matches:
        assert not re.search(r"\bwidth\s*:\s*100%", rule_body), (
            ".est-bar must NOT have width:100% in base styles — "
            "it should only apply inside @media (max-width:640px)"
        )


# AC3: Desktop layout unchanged — base .est-bar still uses flex:1 (not 100% width)
def test_est_bar_base_flex_1_unchanged(html_source):
    # Remove all @media blocks, find base .est-bar rules
    no_media = re.sub(r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", html_source)
    base_matches = re.findall(r"\.est-bar\b[^{]*\{([^}]*)\}", no_media)
    assert base_matches, "Could not find any base .est-bar { } rule"
    flex_1 = next(
        (m for m in base_matches if re.search(r"\bflex\s*:\s*1\b", m)), None
    )
    assert flex_1 is not None, (
        "Base .est-bar must still have flex:1 for desktop layout (unchanged at 641px+)"
    )


# AC3: Base .hist-metrics desktop layout preserved (display:flex, no breaking changes)
def test_hist_metrics_base_layout_preserved(html_source):
    no_media = re.sub(r"@media\s*[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", html_source)
    base_matches = re.findall(r"\.hist-metrics\s*\{([^}]+)\}", no_media)
    assert base_matches, "Could not find any base .hist-metrics { } rule"
    found_flex = any(re.search(r"\bdisplay\s*:\s*flex", m) for m in base_matches)
    assert found_flex, "Base .hist-metrics must still have display:flex"

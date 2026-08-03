"""Tests for issue #2073: Failures table hides Agent and Reason on phones.

Reads project.html directly from disk — no server needed.
CSS is hard to test behaviourally; per the AC, asserting the rendered
DOM/class structure is acceptable for the mobile column rules.
"""
import pathlib
import re

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def project_html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1 / AC5: mobile CSS hides Sprint (col2) and Time (col6), not Agent (col3) or Reason (col5) ──


def test_fbox_mobile_hides_sprint_col2(project_html: str):
    """AC1: Sprint (col 2) is hidden at ≤600px."""
    # Find the @media (max-width: 600px) block that contains fbox-table rules.
    # We look for both compact (max-width:600px) and spaced (max-width: 600px) forms.
    pattern = re.compile(
        r"@media\s*\(max-width:\s*600px\)[^{]*\{([^}]*\.fbox-table[^}]*\}[^}]*)\}",
        re.DOTALL,
    )
    matches = pattern.findall(project_html)
    assert matches, "No @media (max-width: 600px) block containing .fbox-table found"

    combined = "\n".join(matches)
    assert "nth-child(2)" in combined, (
        "Sprint column (nth-child(2)) must be hidden at ≤600px"
    )


def test_fbox_mobile_hides_time_col6(project_html: str):
    """AC1: Time (col 6) is hidden at ≤600px."""
    pattern = re.compile(
        r"@media\s*\(max-width:\s*600px\)[^{]*\{([^}]*\.fbox-table[^}]*\}[^}]*)\}",
        re.DOTALL,
    )
    matches = pattern.findall(project_html)
    assert matches, "No @media (max-width: 600px) block containing .fbox-table found"

    combined = "\n".join(matches)
    assert "nth-child(6)" in combined, (
        "Time column (nth-child(6)) must be hidden at ≤600px"
    )


def test_fbox_mobile_does_not_hide_agent_col3(project_html: str):
    """AC1: Agent (col 3) must NOT be hidden in the mobile CSS block."""
    # Find mobile blocks that target .fbox-table
    pattern = re.compile(
        r"@media\s*\(max-width:\s*600px\)[^{]*\{([^}]*\.fbox-table[^}]*\}[^}]*)\}",
        re.DOTALL,
    )
    matches = pattern.findall(project_html)
    assert matches, "No @media (max-width: 600px) block containing .fbox-table found"

    for block in matches:
        assert "nth-child(3)" not in block, (
            "Agent column (nth-child(3)) must NOT be hidden on mobile; "
            "only Sprint (col 2) and Time (col 6) should be hidden"
        )


def test_fbox_mobile_does_not_hide_reason_col5(project_html: str):
    """AC1: Reason (col 5) must NOT be hidden in the mobile CSS block."""
    pattern = re.compile(
        r"@media\s*\(max-width:\s*600px\)[^{]*\{([^}]*\.fbox-table[^}]*\}[^}]*)\}",
        re.DOTALL,
    )
    matches = pattern.findall(project_html)
    assert matches, "No @media (max-width: 600px) block containing .fbox-table found"

    for block in matches:
        assert "nth-child(5)" not in block, (
            "Reason column (nth-child(5)) must NOT be hidden on mobile; "
            "only Sprint (col 2) and Time (col 6) should be hidden"
        )


def test_fbox_table_wrap_preserved(project_html: str):
    """AC3: .fbox-table-wrap scroll container must be present in project.html."""
    assert "fbox-table-wrap" in project_html, (
        ".fbox-table-wrap horizontal scroll container must be present"
    )
    assert "overflow-x" in project_html, (
        ".fbox-table-wrap overflow-x style must be present"
    )


def test_fbox_column_order_in_css(project_html: str):
    """AC4: Verify the column structure is correct — col 2 Sprint and col 6 Time are hidden."""
    # The mobile rule must target exactly columns 2 and 6, no others (for fbox-table).
    pattern = re.compile(
        r"@media\s*\(max-width:\s*600px\)[^{]*\{([^}]*\.fbox-table[^}]*\}[^}]*)\}",
        re.DOTALL,
    )
    matches = pattern.findall(project_html)
    assert matches, "No mobile fbox-table block found"

    combined = "\n".join(matches)
    # Must hide col 2 and col 6
    assert "nth-child(2)" in combined, "Sprint (col 2) must be hidden"
    assert "nth-child(6)" in combined, "Time (col 6) must be hidden"
    # Must NOT hide col 3 or col 5
    assert "nth-child(3)" not in combined, "Agent (col 3) must NOT be hidden"
    assert "nth-child(5)" not in combined, "Reason (col 5) must NOT be hidden"

"""Tests for issue #2159: .tkt-retry-btn CSS class must be defined in project.html.

The three Retry buttons in the Issues tab (loadTickets error states) use class
.tkt-retry-btn, but no CSS rule for that selector existed, causing them to render
as unstyled browser-default buttons.

AC1 — A .tkt-retry-btn CSS rule exists in project.html.
AC2 — The rule includes cursor: pointer (button must be visually interactive).
AC3 — The rule includes a border property (consistent with dashboard button affordance).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text(
    encoding="utf-8"
)


def _extract_tkt_retry_rule(src: str) -> str:
    """Return the CSS rule block for .tkt-retry-btn, or empty string if absent."""
    # Match .tkt-retry-btn { ... } — captures the rule body
    m = re.search(r"\.tkt-retry-btn\s*\{([^}]*)\}", src)
    return m.group(1) if m else ""


# =============================================================================
# AC1 — .tkt-retry-btn CSS rule exists in project.html
# =============================================================================


def test_ac1_tkt_retry_btn_rule_exists():
    """AC1: A .tkt-retry-btn CSS rule must be defined in project.html."""
    rule_body = _extract_tkt_retry_rule(PROJECT_HTML)
    assert rule_body, (
        ".tkt-retry-btn CSS rule is missing from project.html. "
        "The three Retry buttons in the Issues tab (loadTickets error states) "
        "have class .tkt-retry-btn but no matching CSS rule (issue #2159)."
    )


# =============================================================================
# AC2 — The rule includes cursor: pointer
# =============================================================================


def test_ac2_tkt_retry_btn_has_cursor_pointer():
    """AC2: .tkt-retry-btn must have cursor: pointer so it reads as interactive."""
    rule_body = _extract_tkt_retry_rule(PROJECT_HTML)
    assert rule_body, ".tkt-retry-btn rule is missing — see AC1."
    assert "cursor" in rule_body and "pointer" in rule_body, (
        ".tkt-retry-btn must include 'cursor: pointer' to signal interactivity "
        "(issue #2159 AC2)."
    )


# =============================================================================
# AC3 — The rule includes a border property (button affordance)
# =============================================================================


def test_ac3_tkt_retry_btn_has_border():
    """AC3: .tkt-retry-btn must have a border to match dashboard button affordances."""
    rule_body = _extract_tkt_retry_rule(PROJECT_HTML)
    assert rule_body, ".tkt-retry-btn rule is missing — see AC1."
    assert "border" in rule_body, (
        ".tkt-retry-btn must include a 'border' property to be consistent with "
        "dashboard button styling (issue #2159 AC3)."
    )

"""
Issue #2140: Remove stale 'advisor' reference from project.html
tab-switch comment. After Advisor was deleted in #2075, the
feature-flag comment still listed it.
"""
import re
from pathlib import Path


PROJECT_HTML = (
    Path(__file__).parent.parent / "apps/dashboard/static/project.html"
)


def _find_tab_switch_comment(text: str) -> str:
    m = re.search(
        r"// Feature flags \([^)]*\)"
        r" — hide disabled surfaces before first paint\.",
        text,
    )
    assert m, "Tab-switch feature-flag comment not found in project.html"
    return m.group(0)


def test_advisor_removed_from_tab_switch_comment():
    text = PROJECT_HTML.read_text()
    comment = _find_tab_switch_comment(text)
    assert "advisor" not in comment, (
        "Stale 'advisor' reference still present in "
        f"tab-switch comment: {comment!r}"
    )


def test_tab_switch_comment_retains_sign_off_and_plan_next():
    text = PROJECT_HTML.read_text()
    comment = _find_tab_switch_comment(text)
    assert "sign-off" in comment, (
        "sign-off should still be listed in the tab-switch comment"
    )
    assert "plan-next" in comment, (
        "plan-next should still be listed in the tab-switch comment"
    )

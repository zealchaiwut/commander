"""Tests for issue #2140: Remove stale advisor reference in project.html tab-switch comment"""
import os


def test_stale_advisor_comment_cleanup__comment_updated():
    """AC: Stale 'advisor' reference removed from tab-switch feature-flag comment."""
    # This is a comment-only fix; the feature code shipped in #2075.
    # Verify the comment no longer mentions 'advisor'.

    html_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "apps/dashboard/static/project.html"
    )

    with open(html_path, "r") as f:
        content = f.read()

    # Find the line containing the tab-switch feature-flag comment
    # It should read: "Feature flags (sign-off, plan-next) — hide disabled surfaces before first paint."
    # and NOT contain "advisor"

    target_comment = "Feature flags (sign-off, plan-next) — hide disabled surfaces before first paint."
    assert target_comment in content, f"Expected comment not found in project.html"

    # Ensure the old stale comment (with 'advisor') is NOT present
    stale_comment = "Feature flags (sign-off, advisor, plan-next)"
    assert stale_comment not in content, f"Stale 'advisor' reference still present in comment"

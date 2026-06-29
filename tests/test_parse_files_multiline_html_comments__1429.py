"""Tests for issue #1429: parse_files_to_touch mishandles multi-line HTML comments

AC1 — parse_files_to_touch tracks an in_comment flag
AC2 — All lines while in_comment is True are skipped
AC3 — The opening line containing <!-- (without same-line -->) is also skipped entirely
AC4 — Single-line comments continue to be skipped correctly
AC5 — Regression test covers the exact two-line comment from feature.md (lines 33–34)
AC6 — When a BA submits an issue using the unedited feature template, estimator output contains no spurious paths
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.estimate_issue import parse_files_to_touch  # noqa: E402


def test_parse_files_multiline_html_comments__tracks_in_comment_flag():
    """AC1: parse_files_to_touch tracks an in_comment flag set when <!-- opens without --> on same line."""
    body = (
        "## Files to touch\n"
        "<!-- This is a multi-line\n"
        "comment that spans two lines -->\n"
        "services/sprint_manager/pipeline.py\n"
    )
    result = parse_files_to_touch(body)
    # The two comment lines should be skipped entirely
    assert "<!-- This is a multi-line" not in result
    assert "comment that spans two lines -->" not in result
    assert result == ["services/sprint_manager/pipeline.py"]


def test_parse_files_multiline_html_comments__skips_lines_while_in_comment():
    """AC2: All lines while in_comment is True are skipped and not added to paths."""
    body = (
        "## Files to touch\n"
        "services/first.py\n"
        "<!-- Opening a comment\n"
        "middle line of comment\n"
        "closing comment -->\n"
        "services/second.py\n"
    )
    result = parse_files_to_touch(body)
    assert "middle line of comment" not in result
    assert result == ["services/first.py", "services/second.py"]


def test_parse_files_multiline_html_comments__skips_opening_line():
    """AC3: The opening line containing <!-- (without same-line -->) is also skipped entirely."""
    body = (
        "## Files to touch\n"
        "<!-- Opening comment without close\n"
        "close comment line -->\n"
        "services/pipeline.py\n"
    )
    result = parse_files_to_touch(body)
    # The opening line should be skipped, not parsed as a path
    assert "<!-- Opening comment without close" not in result
    assert result == ["services/pipeline.py"]


def test_parse_files_multiline_html_comments__single_line_still_works():
    """AC4: Single-line comments (both <!-- and --> on same line) continue to be skipped correctly."""
    body = (
        "## Files to touch\n"
        "<!-- single-line comment -->\n"
        "services/pipeline.py\n"
        "<!-- another single-line comment -->\n"
        "services/server.py\n"
    )
    result = parse_files_to_touch(body)
    assert "<!-- single-line comment -->" not in result
    assert "<!-- another single-line comment -->" not in result
    assert result == ["services/pipeline.py", "services/server.py"]


def test_parse_files_multiline_html_comments__feature_template_comment():
    """AC5: Regression test covers the exact two-line comment from .github/ISSUE_TEMPLATE/feature.md (lines 42-43)."""
    # This is the exact comment from the feature template
    body = (
        "## Files to touch\n"
        "<!-- Optional — list one repo-relative path per line (e.g. services/sprint_manager/pipeline.py).\n"
        "     The estimator will merge these with its inferred paths. Leave blank if unknown. -->\n"
        "services/sprint_manager/estimate_issue.py\n"
    )
    result = parse_files_to_touch(body)
    # Neither line of the comment should appear in the result
    assert "<!-- Optional — list one repo-relative path per line (e.g. services/sprint_manager/pipeline.py)." not in result
    assert "     The estimator will merge these with its inferred paths. Leave blank if unknown. -->" not in result
    # Only the real path should be returned
    assert result == ["services/sprint_manager/estimate_issue.py"]


def test_parse_files_multiline_html_comments__unedited_template_no_spurious_paths():
    """AC6: When a BA submits an issue using the unedited feature template, no spurious paths from comment text appear."""
    # This simulates submitting the unedited feature template
    body = (
        "## What & Why\nSome feature.\n\n"
        "## Acceptance Criteria\n- [ ] AC1\n\n"
        "## Files to touch\n"
        "<!-- Optional — list one repo-relative path per line (e.g. services/sprint_manager/pipeline.py).\n"
        "     The estimator will merge these with its inferred paths. Leave blank if unknown. -->\n"
        "## Out of Scope\nNothing.\n"
    )
    result = parse_files_to_touch(body)
    # Should be empty since only the comment was in the Files to touch section
    assert result == []
    # Specifically, no fragments of the comment text should appear
    assert not any("estimator" in r for r in result)
    assert not any("-->" in r for r in result)

"""Tests for issue #1429: parse_files_to_touch mishandles multi-line HTML comments.

The estimator's `parse_files_to_touch` only skipped a line that *starts* with
`<!--` or *equals* `-->`; it did not track multi-line HTML-comment state. The
feature template ships a TWO-line comment in its '## Files to touch' section, so
a BA who submits the unedited template would have the comment's closing line
(`... Leave blank if unknown. -->`) parsed as a spurious "path" and injected
into `files_likely_affected`.

AC1 — `parse_files_to_touch` tracks an `in_comment` flag: set when a line
       contains `<!--` without a closing `-->` on the same line, cleared when a
       subsequent line contains `-->`.
AC2 — All lines while `in_comment` is True are skipped (not added to paths).
AC3 — The opening line containing `<!--` (without same-line `-->`) is skipped.
AC4 — Single-line comments (both `<!--` and `-->` on the same line) continue to
       be skipped — no regression.
AC5 — A regression test covers the exact two-line comment from
       `.github/ISSUE_TEMPLATE/feature.md` and asserts neither line appears.
AC6 — When a BA submits an issue using the unedited feature template, the
       estimator's `files_likely_affected` output contains no spurious paths
       derived from HTML comment text.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

FEATURE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature.md"

from services.sprint_manager.estimate_issue import (  # noqa: E402
    parse_files_to_touch,
    run_estimator,
)


def _template_files_to_touch_comment() -> tuple[str, str]:
    """Return the exact two-line HTML comment from the template's
    '## Files to touch' section (opening line, closing line)."""
    text = FEATURE_TEMPLATE.read_text()
    lines = text.split("\n")
    # Find the '## Files to touch' heading, then the first <!-- comment after it.
    start = next(
        i for i, ln in enumerate(lines)
        if re.match(r"^#+\s+Files to touch", ln, re.IGNORECASE)
    )
    open_idx = next(
        i for i in range(start + 1, len(lines)) if "<!--" in lines[i]
    )
    # Closing line is the first subsequent line containing -->
    close_idx = next(
        i for i in range(open_idx, len(lines)) if "-->" in lines[i]
    )
    # The template comment is genuinely multi-line (open and close differ).
    assert close_idx > open_idx, "template comment is not two-line"
    return lines[open_idx], lines[close_idx]


# ── AC1 / AC2 / AC3: multi-line comment state is tracked and fully skipped ──

def test_multiline_comment_all_lines_skipped():
    """A multi-line HTML comment in the section is skipped in its entirety."""
    body = (
        "## Files to touch\n"
        "<!-- This comment opens here\n"
        "and continues across lines\n"
        "and finally closes here -->\n"
        "services/sprint_manager/pipeline.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/pipeline.py"]


def test_multiline_comment_opening_line_skipped():
    """The opening `<!--` line (no same-line `-->`) is not parsed as a path."""
    body = (
        "## Files to touch\n"
        "<!-- opening line without a closer\n"
        "closing line -->\n"
    )
    result = parse_files_to_touch(body)
    assert result == []
    assert not any("<!--" in p for p in result)


def test_multiline_comment_closing_line_skipped():
    """The closing line containing `-->` (with trailing text) is skipped."""
    body = (
        "## Files to touch\n"
        "<!-- open\n"
        "Leave blank if unknown. -->\n"
        "services/sprint_manager/estimate_issue.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/estimate_issue.py"]
    assert not any("-->" in p for p in result)
    assert "Leave blank if unknown. -->" not in result


# ── AC4: single-line comments still skipped (no regression) ──

def test_single_line_comment_still_skipped():
    """Both `<!--` and `-->` on the same line → skipped; paths still parsed."""
    body = (
        "## Files to touch\n"
        "<!-- optional: one repo-relative path per line -->\n"
        "services/sprint_manager/pipeline.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/pipeline.py"]


def test_single_line_comment_with_trailing_path_after():
    """A real path on the line after a single-line comment is still collected."""
    body = (
        "## Files to touch\n"
        "<!-- note -->\n"
        "apps/dashboard/server.py\n"
        "<!-- another note -->\n"
        "services/sprint_manager/models.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["apps/dashboard/server.py", "services/sprint_manager/models.py"]


# ── AC5: regression test using the exact two-line template comment ──

def test_exact_template_two_line_comment_not_parsed_as_paths():
    """The verbatim two-line comment from feature.md yields no spurious paths."""
    open_line, close_line = _template_files_to_touch_comment()
    body = (
        "## Files to touch\n"
        f"{open_line}\n"
        f"{close_line}\n"
        "services/sprint_manager/estimate_issue.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/estimate_issue.py"]
    assert open_line.strip() not in result
    assert close_line.strip() not in result
    # No fragment of the comment text leaks through.
    assert not any("-->" in p or "<!--" in p for p in result)
    assert not any("Leave blank if unknown" in p for p in result)


# ── AC6: estimator output carries no spurious comment-derived paths ──

def _mock_process(stdout: str):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _base_payload(**overrides):
    payload = {
        "size": "M",
        "estimated_minutes": 15,
        "confidence": "high",
        "files_likely_affected": [],
        "risk_flags": [],
        "dependencies": [],
        "rationale": "n/a",
    }
    payload.update(overrides)
    return payload


def test_estimator_unedited_template_no_spurious_comment_paths():
    """End-to-end: an unedited-template body → no HTML-comment text in
    files_likely_affected."""
    from unittest.mock import patch

    open_line, close_line = _template_files_to_touch_comment()
    body = (
        "## What & Why\nSomething.\n\n"
        "## Files to touch\n"
        f"{open_line}\n"
        f"{close_line}\n\n"
        "## Acceptance Criteria\n- [ ] Done\n"
    )
    payload = _base_payload(files_likely_affected=["services/sprint_manager/pipeline.py"])
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1429, issue_data)

    assert err is None
    fla = estimate["files_likely_affected"]
    assert not any("-->" in p or "<!--" in p for p in fla)
    assert not any("Leave blank if unknown" in p for p in fla)
    assert close_line.strip() not in fla

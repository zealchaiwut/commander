"""Tests for issue #707: Harden follow-up issue URL extraction in reviewer parsing.

The reviewer log parser must extract follow-up issue numbers from
`gh issue create` URLs while excluding comment URLs (which contain
`#issuecomment-`). The original implementation used a fragile position-based
heuristic (`log_text[pos] == "#"`) that silently failed when logs wrapped or
reformatted URLs. This is replaced by stricter regex anchoring exposed via the
helper `_extract_follow_up_issue_nums(log_text, eff_repo, summary_issue_num)`.

AC items verified:
  AC-1  Plain issue-creation URLs are extracted as follow-up numbers.
  AC-2  Comment URLs (`/issues/N#issuecomment-M`) are excluded.
  AC-3  Comment URLs wrapped in markdown / parens (`(...url#issuecomment-M)`)
        are still excluded — the fragile position check is gone.
  AC-4  An issue URL followed by a NON-comment fragment (e.g. `#issue-123`,
        `#top`) is NOT excluded — only `#issuecomment-` is treated as a comment.
  AC-5  The summary issue number is excluded.
  AC-6  Duplicate URLs are de-duplicated, preserving first-seen order.
  AC-7  Only URLs for the given repo are matched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm

REPO = "zealchaiwut/commander"


def _extract(log_text, eff_repo=REPO, summary=None):
    return sm._extract_follow_up_issue_nums(log_text, eff_repo, summary)


# AC-1 -----------------------------------------------------------------------
def test_plain_issue_urls_extracted():
    log = (
        "Creating ticket...\n"
        f"https://github.com/{REPO}/issues/123\n"
        f"https://github.com/{REPO}/issues/124\n"
    )
    assert _extract(log) == [123, 124]


# AC-2 -----------------------------------------------------------------------
def test_comment_urls_excluded():
    log = (
        f"https://github.com/{REPO}/issues/500#issuecomment-99887766\n"
        f"https://github.com/{REPO}/issues/123\n"
    )
    assert _extract(log) == [123]


# AC-3 -----------------------------------------------------------------------
def test_comment_url_wrapped_in_markdown_excluded():
    # Position-based check would break here: the char after the number is ')'
    # in one rendering and '#' only deeper in the string. Stricter anchoring
    # must still recognise the issuecomment fragment.
    log = (
        f"See [review](https://github.com/{REPO}/issues/500#issuecomment-12345) for details\n"
        f"https://github.com/{REPO}/issues/777\n"
    )
    assert _extract(log) == [777]


# AC-4 -----------------------------------------------------------------------
def test_non_comment_fragment_not_excluded():
    # A real issue URL that happens to carry a non-comment anchor must still
    # count as a follow-up ticket.
    log = (
        f"https://github.com/{REPO}/issues/321#issue-456\n"
        f"https://github.com/{REPO}/issues/322#top\n"
    )
    assert _extract(log) == [321, 322]


# AC-5 -----------------------------------------------------------------------
def test_summary_issue_excluded():
    log = (
        f"https://github.com/{REPO}/issues/900\n"
        f"https://github.com/{REPO}/issues/123\n"
    )
    assert _extract(log, summary=900) == [123]


# AC-6 -----------------------------------------------------------------------
def test_duplicates_deduped_preserving_order():
    log = (
        f"https://github.com/{REPO}/issues/123\n"
        f"https://github.com/{REPO}/issues/124\n"
        f"https://github.com/{REPO}/issues/123\n"
    )
    assert _extract(log) == [123, 124]


# AC-7 -----------------------------------------------------------------------
def test_other_repo_urls_ignored():
    log = (
        "https://github.com/someone/else/issues/999\n"
        f"https://github.com/{REPO}/issues/123\n"
    )
    assert _extract(log) == [123]


def test_empty_repo_returns_empty():
    log = f"https://github.com/{REPO}/issues/123\n"
    assert _extract(log, eff_repo=None) == []

"""Tester verification for issue #707: Harden follow-up issue URL extraction (UAT).

Backend-only ticket — no HTTP surface. The hardened logic lives in the pure
helper `_extract_follow_up_issue_nums(log_text, eff_repo, summary_issue_num)`
in services/sprint_manager/sprint_manager.py, so it is verified directly.

Derived acceptance criteria:
  AC-1  Comment URLs (`#issuecomment-`) are excluded even when wrapped /
        reformatted (the fragile char-after-match heuristic is gone).
  AC-2  Issue URLs carrying a NON-comment fragment (`#issue-N`, `#top`) are
        retained — only `#issuecomment-` counts as a comment.
  AC-3  Plain URLs are extracted in first-seen order, de-duplicated, with the
        sprint summary issue removed; a falsy repo yields [].
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm

REPO = "zealchaiwut/commander"


def _extract(log_text, eff_repo=REPO, summary=None):
    return sm._extract_follow_up_issue_nums(log_text, eff_repo, summary)


# --- AC-1: comment URLs excluded even when wrapped ---------------------------
def test_follow_up_url_extraction__comment_urls_excluded_when_wrapped():
    # Comment URL inside markdown link + parens, AND a bare comment URL with
    # trailing prose. Old position-based check (`log_text[pos] == "#"`) failed
    # the wrapped case; the negative-lookahead anchoring must drop both.
    log = (
        f"Posted review: [thread](https://github.com/{REPO}/issues/84#issuecomment-555) done.\n"
        f"https://github.com/{REPO}/issues/84#issuecomment-556 (duplicate notice)\n"
        f"Filed follow-up https://github.com/{REPO}/issues/710\n"
    )
    assert _extract(log) == [710]


# --- AC-2: non-comment fragments retained -----------------------------------
def test_follow_up_url_extraction__non_comment_fragment_retained():
    log = (
        f"https://github.com/{REPO}/issues/321#issue-456\n"
        f"https://github.com/{REPO}/issues/322#top\n"
        f"https://github.com/{REPO}/issues/323#issuecomment-1\n"  # this one drops
    )
    assert _extract(log) == [321, 322]


# --- AC-3: order + dedupe + summary removal + falsy repo --------------------
def test_follow_up_url_extraction__order_dedupe_summary_and_empty_repo():
    log = (
        f"Created https://github.com/{REPO}/issues/707\n"
        f"Created https://github.com/{REPO}/issues/708\n"
        f"Summary issue https://github.com/{REPO}/issues/84\n"
        f"Re-mention https://github.com/{REPO}/issues/707\n"
    )
    # summary 84 removed; 707/708 in first-seen order; dup 707 collapsed.
    assert _extract(log, summary=84) == [707, 708]
    # falsy repo short-circuits.
    assert _extract(log, eff_repo=None, summary=84) == []
    assert _extract(log, eff_repo="", summary=84) == []

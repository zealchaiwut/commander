"""Tests for issue #2158: Failures-table mobile column-hide rule must not be duplicated.

The .fbox-table nth-child hide rule must appear in exactly ONE @media (max-width: 600px)
block — the canonical failures-table block. It must NOT also appear inside the history-card
@media block (alongside .hist-card-mini / .hist-card-head rules).

AC:
  AC1 — The .fbox-table mobile column-hide rule appears exactly once across all CSS in project.html.
  AC2 — The history-card @media (max-width: 600px) block (the one containing .hist-card-mini)
         does NOT contain any .fbox-table column-hide rule.
  AC3 — The canonical failures-table @media (max-width: 600px) block (the one containing
         the issue #2073 comment) still hides Sprint (col 2) and Time (col 6).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text(
    encoding="utf-8"
)

# Regex that matches the fbox-table nth-child hide selector (col 2 or col 6)
# in any form: multi-line or compact, with or without spaces after colon.
_FBOX_HIDE_PAT = re.compile(
    r"\.fbox-table\s+(?:th|td):nth-child\([26]\)"
)


def _extract_media_600_blocks(src: str) -> list[str]:
    """Return the full body text of every @media (max-width: 600px) block."""
    blocks: list[str] = []
    search_from = 0
    while True:
        media_idx = src.find("@media", search_from)
        if media_idx == -1:
            break
        brace_open = src.find("{", media_idx)
        if brace_open == -1:
            break
        header = src[media_idx:brace_open]
        if "max-width" not in header or "600px" not in header:
            # Not the right breakpoint — skip but advance past opening brace
            search_from = brace_open + 1
            continue
        # Walk the block to find its matching closing brace
        depth = 0
        end = brace_open
        for i in range(brace_open, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        blocks.append(src[brace_open + 1 : end])
        search_from = end + 1
    return blocks


# =============================================================================
# AC1 — The column-hide rule appears exactly once across all 600px @media blocks
# =============================================================================


def test_ac1_fbox_hide_rule_appears_exactly_once():
    """AC1: The .fbox-table column-hide rule must appear in exactly one @media (max-width: 600px) block."""
    blocks = _extract_media_600_blocks(PROJECT_HTML)
    assert blocks, "No @media (max-width: 600px) blocks found in project.html"

    blocks_with_rule = [b for b in blocks if _FBOX_HIDE_PAT.search(b)]
    assert len(blocks_with_rule) == 1, (
        f"Expected the .fbox-table column-hide rule to appear in exactly 1 "
        f"@media (max-width: 600px) block, but found it in {len(blocks_with_rule)} blocks. "
        f"The duplicate at the history-card @media block must be removed (issue #2158)."
    )


# =============================================================================
# AC2 — The history-card @media block does NOT contain the fbox-table rule
# =============================================================================


def test_ac2_history_card_media_block_has_no_fbox_rule():
    """AC2: The @media (max-width: 600px) block containing .hist-card-mini must not have .fbox-table rules."""
    blocks = _extract_media_600_blocks(PROJECT_HTML)
    for block in blocks:
        if ".hist-card-mini" in block:
            assert not _FBOX_HIDE_PAT.search(block), (
                "The @media (max-width: 600px) block containing .hist-card-mini must NOT "
                "contain a .fbox-table column-hide rule. Remove the duplicate at line ~2239 "
                "and keep only the canonical failures-table block (issue #2158)."
            )
            return
    # If .hist-card-mini is not in any 600px block, the test still passes
    # (it means the block was removed or restructured, which is also fine).


# =============================================================================
# AC3 — The canonical failures-table block still hides col 2 and col 6
# =============================================================================


def test_ac3_canonical_block_hides_sprint_col2():
    """AC3: The canonical @media (max-width: 600px) failures-table block must still hide col 2."""
    blocks = _extract_media_600_blocks(PROJECT_HTML)
    # The canonical block references the comment from issue #2073
    canonical = [b for b in blocks if "nth-child(2)" in b and ".fbox-table" in b]
    assert canonical, (
        "No @media (max-width: 600px) block hiding .fbox-table nth-child(2) found. "
        "The canonical failures-table column-hide rule must still be present (issue #2158 AC3)."
    )
    assert any("nth-child(2)" in b for b in canonical), (
        "Sprint column (nth-child(2)) must be hidden in the canonical @media block."
    )


def test_ac3_canonical_block_hides_time_col6():
    """AC3: The canonical @media (max-width: 600px) failures-table block must still hide col 6."""
    blocks = _extract_media_600_blocks(PROJECT_HTML)
    canonical = [b for b in blocks if "nth-child(6)" in b and ".fbox-table" in b]
    assert canonical, (
        "No @media (max-width: 600px) block hiding .fbox-table nth-child(6) found. "
        "The canonical failures-table column-hide rule must still be present (issue #2158 AC3)."
    )

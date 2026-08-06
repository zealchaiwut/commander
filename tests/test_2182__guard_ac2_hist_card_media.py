"""Tests for issue #2182: Guard against vacuous pass in test_ac2_history_card_media_block_has_no_fbox_rule.

The original test could pass vacuously when no @media (max-width: 600px) block containing
.hist-card-mini existed — the loop would complete without ever asserting anything.

AC:
  AC1 — test_ac2_history_card_media_block_has_no_fbox_rule asserts that at least one 600px
         block containing .hist-card-mini was found before completing.
  AC2 — Renaming .hist-card-mini to another selector causes the test to fail (not pass vacuously).
  AC3 — The test still passes when valid CSS contains a .hist-card-mini block with no fbox rule.
  AC4 — The guard assertion message identifies the missing block clearly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))

import test_2158__fbox_mobile_rule_deduplicated as _mod_2158

# Import with underscore prefix so pytest does not collect it as a test case
_func_ac2 = _mod_2158.test_ac2_history_card_media_block_has_no_fbox_rule

# CSS with a 600px block that does NOT contain .hist-card-mini (triggers the guard)
_CSS_NO_HIST_CARD_MINI = """<style>
@media (max-width: 600px) {
    .fbox-table th:nth-child(2),
    .fbox-table td:nth-child(2) { display: none; }
    .fbox-table th:nth-child(6),
    .fbox-table td:nth-child(6) { display: none; }
}
</style>"""

# CSS where .hist-card-mini has been renamed to something with no substring match.
# Uses .hist-card-compact to simulate a rename that truly removes the selector.
_CSS_RENAMED_SELECTOR = """<style>
@media (max-width: 600px) {
    .hist-card-compact { display: block; }
    .hist-card-head { font-size: 12px; }
}
</style>"""

# Valid CSS: a 600px block with .hist-card-mini but no conflicting fbox rule
_CSS_VALID = """<style>
@media (max-width: 600px) {
    .hist-card-mini { display: block; }
    .hist-card-head { font-size: 12px; }
}
</style>"""


# =============================================================================
# AC1 — guard fires when no 600px block contains .hist-card-mini
# =============================================================================


def test_ac1_guard_fires_when_no_hist_card_mini_block(monkeypatch):
    """AC1: test_ac2 must fail (not pass vacuously) when no 600px block contains .hist-card-mini."""
    monkeypatch.setattr(_mod_2158, "PROJECT_HTML", _CSS_NO_HIST_CARD_MINI)
    with pytest.raises(AssertionError) as exc_info:
        _func_ac2()
    assert exc_info.value.args, "Guard must produce a non-empty assertion message"


# =============================================================================
# AC2 — renaming the selector causes the test to fail (not vacuously pass)
# =============================================================================


def test_ac2_renamed_selector_causes_test_to_fail(monkeypatch):
    """AC2: Renaming .hist-card-mini from all 600px blocks must cause test_ac2 to fail."""
    monkeypatch.setattr(_mod_2158, "PROJECT_HTML", _CSS_RENAMED_SELECTOR)
    with pytest.raises(AssertionError):
        _func_ac2()


# =============================================================================
# AC3 — valid CSS (hist-card-mini present, no fbox rule) still passes
# =============================================================================


def test_ac3_valid_css_passes(monkeypatch):
    """AC3: Valid CSS with .hist-card-mini and no fbox rule must not raise an assertion."""
    monkeypatch.setattr(_mod_2158, "PROJECT_HTML", _CSS_VALID)
    _func_ac2()  # must not raise


# =============================================================================
# AC4 — guard message identifies the missing block
# =============================================================================


def test_ac4_guard_message_identifies_missing_block(monkeypatch):
    """AC4: The assertion message must reference the missing block so failures are actionable."""
    monkeypatch.setattr(_mod_2158, "PROJECT_HTML", _CSS_NO_HIST_CARD_MINI)
    with pytest.raises(AssertionError) as exc_info:
        _func_ac2()
    msg = str(exc_info.value)
    assert "hist-card-mini" in msg, (
        f"Guard message must mention 'hist-card-mini' so the failure is actionable. Got: {msg!r}"
    )

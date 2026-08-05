"""Tests for issue #1996: precise TAP '# fail N' parsing utility.

Behavioral tests verifying that _parse_tap_fail_count (in tests/tap_utils.py)
uses an anchored '# fail N' regex rather than the broader 'fail\\s+(\\d+)'
pattern that the vacuous assertion used, which could produce wrong counts when
test names or diagnostic lines contain 'fail'.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from tap_utils import _parse_tap_fail_count  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TAP_UTILS = _REPO_ROOT / "tests" / "tap_utils.py"


def test_parse_tap_fail_count_exists():
    """AC: _parse_tap_fail_count helper must exist in tests/tap_utils.py."""
    assert _TAP_UTILS.exists(), "tests/tap_utils.py is missing (issue #1996)"
    assert callable(_parse_tap_fail_count), (
        "_parse_tap_fail_count is not importable from tests.tap_utils (issue #1996)"
    )


def test_parse_returns_zero_for_all_passing():
    """Returns 0 when the TAP '# fail 0' summary line is present."""
    tap = "# tests 3\n# pass 3\n# fail 0\n"
    assert _parse_tap_fail_count(tap) == 0


def test_parse_returns_correct_nonzero_count():
    """Returns the exact failure count from the TAP '# fail N' summary line."""
    tap = "# tests 2\n# pass 1\n# fail 1\n"
    assert _parse_tap_fail_count(tap) == 1


def test_parse_not_fooled_by_fail_in_test_name():
    """Is not confused when 'fail' appears inside a test name without a trailing digit."""
    tap = "ok 1 - test_should_not_fail_abruptly\n# tests 1\n# pass 1\n# fail 0\n"
    assert _parse_tap_fail_count(tap) == 0


def test_parse_anchored_to_hash_prefix():
    """Anchors to '# fail N', ignoring bare 'fail N' in diagnostic message lines.

    Old regex r'fail\\s+(\\d+)' would match 'fail 3' inside the message and
    return 3 (wrong).  New regex r'# fail\\s+(\\d+)' skips the diagnostic and
    matches the TAP summary '# fail 1', returning 1 (correct).
    """
    tap = (
        "not ok 1 - test_foo\n"
        "  message: 'expected operation to fail 3 times'\n"
        "# tests 1\n# pass 0\n# fail 1\n"
    )
    assert _parse_tap_fail_count(tap) == 1


def test_parse_absent_summary_returns_zero():
    """Returns 0 (safe default) when no '# fail N' line is present in output."""
    assert _parse_tap_fail_count("some output without tap summary") == 0

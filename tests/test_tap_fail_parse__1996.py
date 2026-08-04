"""Tests for issue #1996: precise TAP '# fail N' parsing in test_node_test_suite_zero_failures.

Behavioral tests verifying that _parse_tap_fail_count (to be defined in
test_fetch_spy_harness__1831) uses an anchored '# fail N' regex rather than
the broader 'fail\\s+(\\d+)' pattern that the vacuous assertion used, which
could produce wrong counts when test names or diagnostic lines contain 'fail'.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TARGET = _REPO_ROOT / "tests" / "test_fetch_spy_harness__1831.py"


def _load_harness_module():
    spec = importlib.util.spec_from_file_location("_harness_1831", _TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_tap_fail_count_exists():
    """AC: _parse_tap_fail_count helper must be exported from test_fetch_spy_harness__1831."""
    mod = _load_harness_module()
    assert hasattr(mod, "_parse_tap_fail_count"), (
        "_parse_tap_fail_count is not defined in test_fetch_spy_harness__1831.py; "
        "add a module-level helper with regex r'# fail\\s+(\\d+)' (issue #1996)"
    )


def test_parse_returns_zero_for_all_passing():
    """Returns 0 when the TAP '# fail 0' summary line is present."""
    mod = _load_harness_module()
    tap = "# tests 3\n# pass 3\n# fail 0\n"
    assert mod._parse_tap_fail_count(tap) == 0


def test_parse_returns_correct_nonzero_count():
    """Returns the exact failure count from the TAP '# fail N' summary line."""
    mod = _load_harness_module()
    tap = "# tests 2\n# pass 1\n# fail 1\n"
    assert mod._parse_tap_fail_count(tap) == 1


def test_parse_not_fooled_by_fail_in_test_name():
    """Is not confused when 'fail' appears inside a test name without a trailing digit."""
    mod = _load_harness_module()
    tap = "ok 1 - test_should_not_fail_abruptly\n# tests 1\n# pass 1\n# fail 0\n"
    assert mod._parse_tap_fail_count(tap) == 0


def test_parse_anchored_to_hash_prefix():
    """Anchors to '# fail N', ignoring bare 'fail N' in diagnostic message lines.

    Old regex r'fail\\s+(\\d+)' would match 'fail 3' inside the message and
    return 3 (wrong).  New regex r'# fail\\s+(\\d+)' skips the diagnostic and
    matches the TAP summary '# fail 1', returning 1 (correct).
    """
    mod = _load_harness_module()
    tap = (
        "not ok 1 - test_foo\n"
        "  message: 'expected operation to fail 3 times'\n"
        "# tests 1\n# pass 0\n# fail 1\n"
    )
    assert mod._parse_tap_fail_count(tap) == 1


def test_parse_absent_summary_returns_zero():
    """Returns 0 (safe default) when no '# fail N' line is present in output."""
    mod = _load_harness_module()
    assert mod._parse_tap_fail_count("some output without tap summary") == 0

"""Tests for issue #1500: gates.py proxy identity check breaks under importlib.reload.

This is an additional comprehensive test to verify the fix handles all edge cases.
The primary test suite is in test_1500__gates_proxy_reload_safety.py.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestLookupInSmRobustness:
    """Ensure _lookup_in_sm handles edge cases correctly."""

    def test_non_function_attributes_are_not_skipped(self):
        """Non-function attributes (e.g. a string or mock) should not be
        incorrectly skipped due to code-origin checks."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        # A string value is not a function — should be returned even if
        # local_fn is a function
        test_string = "not_a_function"
        original = getattr(sm, "_test_attr", None)
        sm._test_attr = test_string
        try:
            result = gates._lookup_in_sm("_test_attr", gates._run_timed)
            assert result == test_string
        finally:
            if original is not None:
                sm._test_attr = original
            else:
                delattr(sm, "_test_attr")

    def test_lambda_is_detected_as_patch(self):
        """A lambda (different qualname) should be detected as an active patch."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        lambda_patch = lambda: None  # different qualname than _run_timed
        original = sm._run_timed
        sm._run_timed = lambda_patch
        try:
            result = gates._lookup_in_sm("_run_timed", gates._run_timed)
            assert result is lambda_patch
        finally:
            sm._run_timed = original

    def test_none_attribute_returns_none(self):
        """If sprint_manager doesn't have the attribute, return None."""
        from services.sprint_manager import gates

        result = gates._lookup_in_sm("_definitely_nonexistent_attr_xyz", lambda: None)
        assert result is None

    def test_works_when_sm_module_not_loaded(self):
        """If sprint_manager isn't in sys.modules, should return None gracefully."""
        from services.sprint_manager import gates

        # This scenario is unlikely but the function should handle it
        # by checking if _sm is not None before accessing it
        with patch.dict(sys.modules, {"sprint_manager": None}):
            result = gates._lookup_in_sm("_run_timed", gates._run_timed)
            # Should still work because the function checks if _sm is not None
            assert result is None or isinstance(result, types.FunctionType)


class TestProxyCallsWorkWithStaleCopies:
    """Verify that all proxy helper functions work correctly with stale refs."""

    def test_proxy_calls_dont_infinite_loop(self, tmp_path):
        """Call each proxy function to ensure none cause RecursionError."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        # Create stale copies for key functions
        stale_fns = {
            "_run_timed": gates._run_timed,
            "_file_line_count_at_ref": gates._file_line_count_at_ref,
        }

        for fn_name, local_fn in stale_fns.items():
            stale = types.FunctionType(
                local_fn.__code__,
                local_fn.__globals__,
                local_fn.__name__,
                local_fn.__defaults__,
                local_fn.__closure__,
            )
            original = getattr(sm, fn_name)
            setattr(sm, fn_name, stale)
            try:
                # Just ensure we can call the function without RecursionError
                if fn_name == "_run_timed":
                    rc, out, err = gates._run_timed("true", cwd=tmp_path)
                    assert rc == 0
                elif fn_name == "_file_line_count_at_ref":
                    result = gates._file_line_count_at_ref("HEAD", "nope.py", cwd=tmp_path)
                    assert result is None  # tmp_path is not a git repo
            finally:
                setattr(sm, fn_name, original)

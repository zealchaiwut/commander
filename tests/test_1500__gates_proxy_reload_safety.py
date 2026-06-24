"""Tests for issue #1500 — gates.py proxy identity check breaks under
importlib.reload (cross-test poisoning).

Root cause: after importlib.reload(gates) the module's __dict__ is updated
in-place, so OLD function objects (still held by sprint_manager via from-
imports) see NEW function names via __globals__.  _lookup_in_sm then sees
  sm.fn (OLD) is not local_fn (NEW)
and wrongly treats the OLD function as an active monkeypatch, causing:
  - infinite recursion for direct proxy calls (_file_line_count_at_ref)
  - stale function dispatch in _run_quality_gates via _resolve()

Fix: compare by code origin (co_qualname + co_filename + co_firstlineno)
instead of identity alone.  Same source == not a patch, even across reloads.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


def _make_stale_copy(fn):
    """Return a new function object built from the same __code__ as *fn*.

    This mimics what importlib.reload(gates) produces: a fresh function object
    whose identity differs from the pre-reload copy held by sprint_manager, but
    whose code origin (qualname / filename / firstlineno) is identical.
    """
    return types.FunctionType(
        fn.__code__,
        fn.__globals__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )


class TestLookupInSmReloadSafety:
    """AC1: _lookup_in_sm must not treat a stale pre-reload sprint_manager
    copy as an active monkeypatch."""

    def test_returns_none_for_stale_pre_reload_copy(self):
        """Simulate the post-reload scenario: sprint_manager holds a stale copy
        (same source, different identity).  _lookup_in_sm must return None."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        local_fn = gates._file_line_count_at_ref
        stale_fn = _make_stale_copy(local_fn)

        # Temporarily install the stale copy as sprint_manager's attribute
        original = sm._file_line_count_at_ref
        sm._file_line_count_at_ref = stale_fn
        try:
            result = gates._lookup_in_sm("_file_line_count_at_ref", local_fn)
        finally:
            sm._file_line_count_at_ref = original

        assert result is None, (
            f"_lookup_in_sm returned {result!r} for a stale pre-reload copy "
            f"— wrongly treated as active monkeypatch (issue #1500)"
        )

    def test_returns_none_for_run_timed_stale_copy(self):
        """Same check for _run_timed (used by every proxy call in gates.py)."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        local_fn = gates._run_timed
        stale_fn = _make_stale_copy(local_fn)

        original = sm._run_timed
        sm._run_timed = stale_fn
        try:
            result = gates._lookup_in_sm("_run_timed", local_fn)
        finally:
            sm._run_timed = original

        assert result is None, (
            f"_lookup_in_sm returned {result!r} for a stale _run_timed copy "
            f"(issue #1500)"
        )

    def test_still_returns_mock_for_active_patch(self):
        """A genuine monkeypatch (different qualname / no __code__) must still
        be detected and returned so tests that patch sprint_manager keep working."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        mock_fn = MagicMock(name="mock_run_timed")
        original = sm._run_timed
        sm._run_timed = mock_fn
        try:
            result = gates._lookup_in_sm("_run_timed", gates._run_timed)
        finally:
            sm._run_timed = original

        assert result is mock_fn, (
            f"_lookup_in_sm returned {result!r} instead of the active mock"
        )

    def test_returns_none_when_no_patch_active(self):
        """With no monkeypatch, sprint_manager.fn IS gates.fn: return None."""
        from services.sprint_manager import gates
        result = gates._lookup_in_sm("_run_timed", gates._run_timed)
        assert result is None


class TestReloadDoesNotCauseRecursion:
    """AC2: proxy calls must not enter infinite recursion when sprint_manager
    holds a stale (pre-reload) function reference."""

    def test_file_line_count_no_recursion_with_stale_ref(self, tmp_path):
        """With a stale sm ref, _file_line_count_at_ref must not recurse —
        it must call subprocess normally and return None for a non-repo path."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        local_fn = gates._file_line_count_at_ref
        stale_fn = _make_stale_copy(local_fn)

        original = sm._file_line_count_at_ref
        sm._file_line_count_at_ref = stale_fn
        try:
            try:
                result = gates._file_line_count_at_ref("HEAD", "nope.py", cwd=tmp_path)
            except RecursionError:
                pytest.fail(
                    "RecursionError: _file_line_count_at_ref recursed with a "
                    "stale sprint_manager reference — issue #1500 not fixed"
                )
            assert result is None  # tmp_path is not a git repo
        finally:
            sm._file_line_count_at_ref = original

    def test_run_timed_no_recursion_with_stale_ref(self, tmp_path):
        """With a stale sm ref, _run_timed must not recurse."""
        from services.sprint_manager import gates
        import sprint_manager as sm

        local_fn = gates._run_timed
        stale_fn = _make_stale_copy(local_fn)

        original = sm._run_timed
        sm._run_timed = stale_fn
        try:
            try:
                rc, out, err = gates._run_timed("true", cwd=tmp_path)
            except RecursionError:
                pytest.fail(
                    "RecursionError: _run_timed recursed with a stale "
                    "sprint_manager reference — issue #1500 not fixed"
                )
            assert rc == 0
        finally:
            sm._run_timed = original


class TestReloadTestNolongerCallsReload:
    """AC3: test_1281__extract_gates_to_gates_py.py must not contain
    importlib.reload(gates) — that call was the trigger for cross-test
    poisoning (issue #1500)."""

    def test_1281_test_file_has_no_reload_call(self):
        import ast
        test_file = REPO_ROOT / "tests" / "test_1281__extract_gates_to_gates_py.py"
        assert test_file.exists(), f"{test_file} not found"
        source = test_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "reload"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "importlib"):
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id == "gates":
                pytest.fail(
                    "test_1281 still calls importlib.reload(gates) — "
                    "remove it to stop cross-test poisoning (issue #1500)"
                )

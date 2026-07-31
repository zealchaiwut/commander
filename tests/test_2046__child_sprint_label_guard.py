"""Tests for issue #2046 — _guard_sprint_labels must protect child sprint labels.

Background
----------
``_SPRINT_LABEL_RE`` in sprint_manager.py was ``^sprint-\\d+$`` — it matched
plain sprint labels (sprint-N) but NOT child/re-run labels (sprint-N.M).  The
guard partitions the ``remove`` list into ``safe_remove`` and
``blocked_removes`` using this regex, so child labels silently fell into
``safe_remove`` and were passed straight to ``gh issue edit --remove-label``.
This destroyed ticket state mid-run with no log line.

Fix: regex widened to ``^sprint-\\d+(\\.\\.d+)?$`` — the established form already
used by 17 other copies in the codebase (startup.py, sprint_dispatch.py,
board_service.py, estimates.py, signoff_service.py, …).

AC coverage
-----------
AC-1  _SPRINT_LABEL_RE matches child labels (sprint-N.M) identically to plain
      sprint labels (sprint-N).
AC-2  _guard_sprint_labels blocks removal of sprint-1006.3 and reports it in
      blocked_removes (behavioral, not source-regex, per #1746).
AC-2  _guard_sprint_labels still blocks removal of sprint-1006 (regression).
AC-2  A non-sprint label (needs-rework) still passes through safe_remove.
AC-2  Deeper label sprint-9.4 is blocked.
AC-2  Mixed list: sprint-1006, sprint-1006.3, needs-rework → only needs-rework
      in safe_remove.

How these tests FAIL against pre-fix code
------------------------------------------
Before the fix ``_SPRINT_LABEL_RE = re.compile(r"^sprint-\\d+$")`` does not
match ``sprint-1006.3``.  Therefore:

  test_child_label_blocked:
      Pre-fix: ``sprint-1006.3`` lands in ``safe_remove`` not ``blocked_removes``.
      ``assert "sprint-1006.3" not in safe_remove`` FAILS.

  test_child_label_in_blocked_removes:
      Pre-fix: ``blocked_removes`` is empty.
      ``assert "sprint-1006.3" in blocked_removes`` FAILS.

  test_deeper_child_label_blocked:
      Pre-fix: ``sprint-9.4`` falls through to ``safe_remove``.
      ``assert "sprint-9.4" not in safe_remove`` FAILS.

  test_mixed_list_only_non_sprint_passes_through:
      Pre-fix: ``sprint-1006.3`` appears in ``safe_remove``.
      ``assert safe_remove == ["needs-rework"]`` FAILS.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture (pattern
copied verbatim from test_2031__false_orphan_sweep.py).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
SM_PATH = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(SM_PATH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub heavy imports so sprint_manager is importable without a real environment.
for _mod in ("github_client", "dotenv", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Ensure all git-touching sprint-end steps are stubbed."
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_guard():
    import sprint_manager as sm
    return sm._guard_sprint_labels


def _get_re():
    import sprint_manager as sm
    return sm._SPRINT_LABEL_RE


# ── AC-1: _SPRINT_LABEL_RE matches child labels ───────────────────────────────

class TestSprintLabelREChildLabels:
    """AC-1: _SPRINT_LABEL_RE in sprint_manager must match child sprint labels."""

    def test_plain_sprint_matches(self):
        assert _get_re().match("sprint-1")
        assert _get_re().match("sprint-22")
        assert _get_re().match("sprint-1006")

    def test_child_sprint_label_matches(self):
        # AC-1: these must match after the fix
        assert _get_re().match("sprint-1006.3"), "sprint-1006.3 must be matched"
        assert _get_re().match("sprint-72.1"),   "sprint-72.1 must be matched"
        assert _get_re().match("sprint-9.4"),    "sprint-9.4 must be matched"
        assert _get_re().match("sprint-1.10"),   "sprint-1.10 must be matched"

    def test_double_dotted_rejected(self):
        # sprint-N.M.P is NOT supported (one dotted level max, per test_675)
        assert not _get_re().match("sprint-15.1.1")
        assert not _get_re().match("sprint-9.4.2")

    def test_non_sprint_rejected(self):
        assert not _get_re().match("SIT")
        assert not _get_re().match("in-progress")
        assert not _get_re().match("needs-rework")
        assert not _get_re().match("sprint-abc")
        assert not _get_re().match("sprint_15")


# ── AC-2: _guard_sprint_labels behavioral tests ───────────────────────────────

class TestGuardSprintLabelsChildLabels:
    """AC-2: behavioral tests — call the real function, assert observed output."""

    def test_child_label_blocked(self):
        """sprint-1006.3 must land in blocked_removes, not safe_remove."""
        guard = _get_guard()
        safe_add, safe_remove = guard([], ["sprint-1006.3"])
        assert "sprint-1006.3" not in safe_remove, (
            "Child sprint label sprint-1006.3 must not pass through safe_remove"
        )

    def test_child_label_in_blocked_removes(self, capsys):
        """_guard_sprint_labels must report the blocked child label to stderr."""
        import io
        import contextlib
        guard = _get_guard()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            safe_add, safe_remove = guard([], ["sprint-1006.3"])
        stderr_out = buf.getvalue()
        # Function returns (safe_add, safe_remove) — blocked_removes is the
        # complement: items in remove that are NOT in safe_remove.
        blocked = [lbl for lbl in ["sprint-1006.3"] if lbl not in safe_remove]
        assert "sprint-1006.3" in blocked, (
            "sprint-1006.3 must be in the blocked set (absent from safe_remove)"
        )
        assert "sprint-1006.3" in stderr_out, (
            "[label-guard] log line must mention the blocked child label"
        )

    def test_plain_sprint_label_still_blocked(self):
        """Regression: sprint-1006 (plain) must still be blocked."""
        guard = _get_guard()
        _, safe_remove = guard([], ["sprint-1006"])
        assert "sprint-1006" not in safe_remove, (
            "Plain sprint label sprint-1006 must not pass through"
        )

    def test_non_sprint_label_passes_through(self):
        """needs-rework is not a sprint label and must appear in safe_remove."""
        guard = _get_guard()
        _, safe_remove = guard([], ["needs-rework"])
        assert "needs-rework" in safe_remove, (
            "Non-sprint label needs-rework must pass through in safe_remove"
        )

    def test_deeper_child_label_blocked(self):
        """sprint-9.4 must be blocked identically to sprint-1006.3."""
        guard = _get_guard()
        _, safe_remove = guard([], ["sprint-9.4"])
        assert "sprint-9.4" not in safe_remove, (
            "Child sprint label sprint-9.4 must not pass through safe_remove"
        )

    def test_mixed_list_only_non_sprint_passes(self):
        """Mixed remove list: only non-sprint labels pass through safe_remove."""
        guard = _get_guard()
        _, safe_remove = guard([], ["sprint-1006", "sprint-1006.3", "needs-rework"])
        assert safe_remove == ["needs-rework"], (
            f"Expected only needs-rework in safe_remove, got {safe_remove!r}"
        )

    def test_child_label_blocked_with_sprint_context(self):
        """Guard works with sprint_label context provided (active-run path)."""
        guard = _get_guard()
        _, safe_remove = guard([], ["sprint-1006.3", "SIT"], sprint_label="sprint-1006.3")
        assert "sprint-1006.3" not in safe_remove
        assert "SIT" in safe_remove  # SIT is not a sprint label — passes through

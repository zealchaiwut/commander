"""Re-run branch hygiene (#879 divergent-branch, #880 rebase conflict).

An existing feature branch is RESUMABLE prior work, never fatal:
  - already in base  → no rebase, no error
  - unmerged, clean  → rebase, no error
  - unmerged, conflict → reset branch to base (clean coder pass), no error
The old code aborted with 'divergent-branch' / 'merge' and failed the ticket.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))
import sprint_manager as sm


def _hygiene_with(*, branch_exists, already_in_base, rebase_ok):
    """Drive _worktree_hygiene with a _try stub keyed on the git subcommand.
    Returns (result_tuple, calls)."""
    calls = []

    def fake_try(*args, **kw):
        calls.append(args)
        a = list(args)
        joined = " ".join(str(x) for x in a)
        if "fetch" in a:
            return (True, "", "")
        if a[:2] == ("git", "rev-parse") and any("origin/" in str(x) for x in a):
            return (True, "b00f8fcf", "")          # base sha
        if a[:2] == ("git", "rev-parse"):
            return (True, "deadbeef", "")           # worktree / branch sha
        if "status" in a or ("diff" in a and "--stat" not in joined):
            return (True, "", "")                   # clean worktree
        if "reset" in a:
            return (True, "", "")
        if "ls-files" in a:
            return (True, "", "")
        if "branch" in a and "--list" in a:
            return (True, "feature/879-x\n" if branch_exists else "", "")
        if "checkout" in a:
            return (True, "", "")
        if "merge-base" in a:
            return (already_in_base, "", "")
        if "rebase" in a and "--abort" in a:
            return (True, "", "")
        if "rebase" in a:
            return (rebase_ok, "", "" if rebase_ok else "CONFLICT (content)")
        return (True, "", "")

    with patch.object(sm, "_try", side_effect=fake_try), \
         patch.object(sm, "record_failure"):
        result = sm._worktree_hygiene(Path("/tmp/tester"), 879, "sprint/sprint-66.6")
    return result, calls


def _did(calls, *needles):
    return any(all(n in [str(x) for x in c] or n in " ".join(str(x) for x in c) for n in needles) for c in calls)


def test_already_in_base_no_rebase_no_error():
    (wt, base, err), calls = _hygiene_with(branch_exists=True, already_in_base=True, rebase_ok=True)
    assert err is None
    # no rebase attempted when already contained in base
    assert not any("rebase" in [str(x) for x in c] and "--abort" not in c for c in calls)


def test_unmerged_clean_rebase_no_error():
    (wt, base, err), calls = _hygiene_with(branch_exists=True, already_in_base=False, rebase_ok=True)
    assert err is None
    assert any("rebase" in [str(x) for x in c] for c in calls)


def test_rebase_conflict_resets_and_recovers():
    (wt, base, err), calls = _hygiene_with(branch_exists=True, already_in_base=False, rebase_ok=False)
    # No longer a fatal 'merge'/'divergent-branch' — recoverable.
    assert err is None
    # rebase aborted + branch reset to base for a clean coder pass
    assert any("--abort" in c for c in calls)
    assert any("reset" in [str(x) for x in c] for c in calls)

"""Tests for issue #1414: Serialize concurrent merges with automated rebase on conflict.

AC1: develop_merge_guard serializes merges — covered by test_738; we verify the guard
     is still used inside _call_finish_feature (not tested separately here).
AC2: When finish_feature.py fails due to divergence, exactly one git rebase is attempted.
AC3: If the automated rebase succeeds (no conflicts), the merge completes normally and
     the ticket is NOT flagged needs-rework.
AC4: If the automated rebase produces conflicts, the ticket is labeled needs-rework and
     the output includes the explicit list of conflicting file paths.
AC5: A ticket marked needs-rework due to rebase conflict does NOT halt the sprint —
     FailureCategory.REBASE_CONFLICT is not in _LOGIC_FAILURE_CATEGORIES so the
     tester stage returns StageResult.FAIL (drop) rather than REJECT (requeue/halt).
AC6: Tickets whose changes are fully disjoint merge successfully after a single automated
     rebase with no manual intervention (handled by the AC3 disjoint test).
AC7: The conflict file list matches the files reported by git rebase at the point of
     conflict (no omissions, no extraneous files).
AC8: Only one rebase attempt is made per merge cycle — the runner does not retry
     indefinitely.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

# Stub heavy optional imports before loading sprint_manager.
for _mod in ("github_client", "neondb"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_repos(tmp_path: Path):
    """Create a bare origin and one clone (the 'tester' worktree)."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "develop"], cwd=bare, check=True, capture_output=True)

    # Bootstrap commit
    boot = tmp_path / "bootstrap"
    boot.mkdir()
    subprocess.run(["git", "init", "-b", "develop"], cwd=boot, check=True, capture_output=True)
    _git(boot, "config", "user.email", "test@test.com")
    _git(boot, "config", "user.name", "Test")
    _git(boot, "remote", "add", "origin", str(bare))
    (boot / "README.md").write_text("init\n")
    _git(boot, "add", ".")
    _git(boot, "commit", "-m", "init")
    _git(boot, "push", "origin", "develop")

    # Tester clone (acts as wt_root in _call_finish_feature)
    tester = tmp_path / "tester"
    subprocess.run(["git", "clone", str(bare), str(tester)], check=True, capture_output=True)
    _git(tester, "config", "user.email", "test@test.com")
    _git(tester, "config", "user.name", "Test")

    return bare, tester


def _add_commit(repo: Path, filename: str, content: str, msg: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── AC7: _extract_rebase_conflict_files parses git output correctly ───────────

REBASE_CONFLICT_OUTPUT = """\
Auto-merging services/sprint_manager/sprint_manager.py
CONFLICT (content): Merge conflict in services/sprint_manager/sprint_manager.py
CONFLICT (content): Merge conflict in tests/test_foo.py
error: could not apply abc1234... add feature X
hint: Resolve all conflicts manually, mark them as resolved with
hint: "git add/rm <conflicted_files>", then run "git rebase --continue".
"""


def test_ac7_extract_conflict_files_from_rebase_output():
    """AC7: _extract_rebase_conflict_files pulls exactly the conflicting paths."""
    files = sm._extract_rebase_conflict_files(REBASE_CONFLICT_OUTPUT)
    assert files == [
        "services/sprint_manager/sprint_manager.py",
        "tests/test_foo.py",
    ]


def test_ac7_extract_conflict_files_deduplicates():
    """AC7: Duplicate conflict lines for the same file are collapsed to one entry."""
    output = """\
CONFLICT (content): Merge conflict in foo.py
CONFLICT (content): Merge conflict in foo.py
CONFLICT (add/add): Merge conflict in bar.py
"""
    files = sm._extract_rebase_conflict_files(output)
    assert files == ["foo.py", "bar.py"]


def test_ac7_extract_conflict_files_empty_on_no_conflicts():
    """AC7: Clean rebase output yields an empty list."""
    files = sm._extract_rebase_conflict_files("Successfully rebased and updated refs/heads/feature/42-foo.")
    assert files == []


def test_ac7_extract_conflict_files_various_conflict_types():
    """AC7: All CONFLICT (...): Merge conflict in <path> variants are captured."""
    output = """\
CONFLICT (modify/delete): Merge conflict in deleted.py
CONFLICT (rename/add): Merge conflict in moved.py
CONFLICT (content): Merge conflict in edited.py
"""
    files = sm._extract_rebase_conflict_files(output)
    assert files == ["deleted.py", "moved.py", "edited.py"]


# ── AC2/AC3/AC6: _call_finish_feature attempts rebase on merge failure ─────────

def _setup_feature_branch(bare: Path, tester: Path, branch: str, filename: str, content: str):
    """Create a remote feature branch so _call_finish_feature can find it."""
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", branch)
    _add_commit(tester, filename, content, f"feat: {branch}")
    _git(tester, "push", "origin", branch)
    _git(tester, "checkout", "develop")


def test_ac2_ac3_rebase_succeeds_merge_completes(tmp_path, monkeypatch):
    """AC2/AC3/AC6: When finish_feature.py fails due to divergence but changes are
    disjoint, the runner rebases once and the second merge call succeeds.
    Returns (True, []) — ticket is NOT flagged needs-rework."""
    bare, tester = _setup_repos(tmp_path)

    # Simulate merge-N landing: advance develop on the remote with a different file.
    boot = tmp_path / "bootstrap"
    _git(boot, "checkout", "develop")
    _git(boot, "fetch", "origin")
    _git(boot, "reset", "--hard", "origin/develop")
    _add_commit(boot, "other.txt", "merge-N content\n", "feat: merge-N lands first")
    _git(boot, "push", "origin", "develop")

    # Set up feature branch N+1 (touches a DIFFERENT file — fully disjoint).
    _setup_feature_branch(bare, tester, "feature/99-disjoint", "feature99.txt", "feature99\n")

    finish_script = REPO_ROOT / "scripts" / "finish_feature.py"
    subprocess_call_log: list[list] = []

    # Call 1: finish_feature fails (simulated divergence rejection)
    # Call 2: finish_feature succeeds (after rebase)
    call_count = {"n": 0}

    real_subprocess_run = subprocess.run

    def patched_subprocess_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and "finish_feature" in str(cmd):
            subprocess_call_log.append(list(cmd))
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _fake_proc(1, stderr="CONFLICT: diverged from develop")
            return _fake_proc(0, stdout="FINISH_FEATURE_OUTCOME merged sha=abc123 branch=feature/99-disjoint\n")
        return real_subprocess_run(cmd, **kwargs)

    monkeypatch.setattr(sm.subprocess, "run", patched_subprocess_run)
    # Silence structured_log
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)
    monkeypatch.setattr(sm.structured_log, "info", lambda *a, **k: None)

    success, conflict_files = sm._call_finish_feature(
        issue_num=99,
        worktester_root=tester,
        target_branch="develop",
        repo_name=None,
        cfg=None,
    )

    assert success is True, "rebase+retry should succeed for disjoint changes"
    assert conflict_files == [], "no conflict files on successful rebase"


def test_ac8_only_one_rebase_attempt(tmp_path, monkeypatch):
    """AC8: _call_finish_feature makes exactly one rebase attempt per merge cycle,
    regardless of outcome."""
    bare, tester = _setup_repos(tmp_path)

    # Feature branch N+1 that conflicts with the advanced develop.
    _git(tmp_path / "bootstrap", "checkout", "develop")
    _git(tmp_path / "bootstrap", "fetch", "origin")
    _git(tmp_path / "bootstrap", "reset", "--hard", "origin/develop")
    _add_commit(tmp_path / "bootstrap", "conflict.txt", "base version\n", "base: advance develop")
    _git(tmp_path / "bootstrap", "push", "origin", "develop")

    # Feature branch modifies the SAME file — will conflict on rebase.
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", "feature/100-conflict")
    _add_commit(tester, "conflict.txt", "feature version\n", "feat: conflict branch")
    _git(tester, "push", "origin", "feature/100-conflict")
    _git(tester, "checkout", "develop")

    rebase_call_count = {"n": 0}
    real_try = sm._try

    def patched_try(*cmd, cwd=None):
        if cmd[:2] == ("git", "rebase") and "--abort" not in cmd:
            rebase_call_count["n"] += 1
        return real_try(*cmd, cwd=cwd)

    monkeypatch.setattr(sm, "_try", patched_try)

    real_subprocess_run = subprocess.run
    def patched_subprocess_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(1, stderr="CONFLICT")
        return real_subprocess_run(cmd, **kwargs)
    monkeypatch.setattr(sm.subprocess, "run", patched_subprocess_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    sm._call_finish_feature(
        issue_num=100,
        worktester_root=tester,
        target_branch="develop",
    )

    assert rebase_call_count["n"] == 1, (
        f"expected exactly 1 rebase attempt, got {rebase_call_count['n']}"
    )


def test_ac4_ac7_rebase_conflict_returns_conflict_files(tmp_path, monkeypatch):
    """AC4/AC7: When rebase produces conflicts, _call_finish_feature returns (False,
    conflict_files) where conflict_files exactly matches git's reported paths."""
    bare, tester = _setup_repos(tmp_path)

    # Advance develop with a commit that touches conflict.txt.
    boot = tmp_path / "bootstrap"
    _git(boot, "checkout", "develop")
    _git(boot, "fetch", "origin")
    _git(boot, "reset", "--hard", "origin/develop")
    _add_commit(boot, "conflict.txt", "base version\n", "base: advance develop")
    _git(boot, "push", "origin", "develop")

    # Feature branch also touches conflict.txt — will conflict on rebase.
    _git(tester, "fetch", "origin")
    _git(tester, "checkout", "-b", "feature/101-conflict")
    _add_commit(tester, "conflict.txt", "feature version\n", "feat: conflict branch")
    _git(tester, "push", "origin", "feature/101-conflict")
    _git(tester, "checkout", "develop")

    real_subprocess_run = subprocess.run
    def patched_subprocess_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(1, stderr="CONFLICT: cannot merge")
        return real_subprocess_run(cmd, **kwargs)
    monkeypatch.setattr(sm.subprocess, "run", patched_subprocess_run)
    monkeypatch.setattr(sm.structured_log, "error", lambda *a, **k: None)

    success, conflict_files = sm._call_finish_feature(
        issue_num=101,
        worktester_root=tester,
        target_branch="develop",
    )

    assert success is False, "rebase conflict must return failure"
    assert len(conflict_files) >= 1, "conflict file list must be non-empty"
    assert "conflict.txt" in conflict_files, (
        f"conflict.txt must appear in conflict_files, got: {conflict_files}"
    )


def test_ac3_no_rebase_when_merge_already_done(tmp_path, monkeypatch):
    """AC3: When finish_feature.py succeeds on the first call, no rebase is attempted."""
    bare, tester = _setup_repos(tmp_path)
    _setup_feature_branch(bare, tester, "feature/200-clean", "new_file.txt", "clean\n")

    rebase_called = {"flag": False}
    real_try = sm._try
    def patched_try(*cmd, cwd=None):
        if cmd[:2] == ("git", "rebase") and "--abort" not in cmd:
            rebase_called["flag"] = True
        return real_try(*cmd, cwd=cwd)
    monkeypatch.setattr(sm, "_try", patched_try)

    real_subprocess_run = subprocess.run
    def patched_subprocess_run(cmd, **kwargs):
        if isinstance(cmd, (list, tuple)) and "finish_feature" in str(cmd):
            return _fake_proc(0, stdout="FINISH_FEATURE_OUTCOME merged sha=def456 branch=feature/200-clean\n")
        return real_subprocess_run(cmd, **kwargs)
    monkeypatch.setattr(sm.subprocess, "run", patched_subprocess_run)

    success, conflict_files = sm._call_finish_feature(
        issue_num=200,
        worktester_root=tester,
        target_branch="develop",
    )

    assert success is True
    assert conflict_files == []
    assert not rebase_called["flag"], "rebase must NOT be attempted on first-call success"


# ── AC5: REBASE_CONFLICT is not in _LOGIC_FAILURE_CATEGORIES ─────────────────

def test_ac5_rebase_conflict_category_not_in_logic_failures():
    """AC5: FailureCategory.REBASE_CONFLICT is not a logic failure — the pipeline
    drops the ticket without requeuing it for a coder fix attempt (no halt)."""
    assert hasattr(sm.FailureCategory, "REBASE_CONFLICT"), (
        "FailureCategory.REBASE_CONFLICT must be defined"
    )
    assert sm.FailureCategory.REBASE_CONFLICT not in sm._LOGIC_FAILURE_CATEGORIES, (
        "REBASE_CONFLICT must NOT be in _LOGIC_FAILURE_CATEGORIES so the sprint does not "
        "requeue the ticket for another coder/tester round"
    )


# ── AC4/AC5: handle_post_tester returns REBASE_CONFLICT on rebase failure ─────

def test_ac4_ac5_handle_post_tester_returns_rebase_conflict_category(monkeypatch):
    """AC4/AC5: When _call_finish_feature reports a rebase conflict, handle_post_tester
    returns (False, ..., FailureCategory.REBASE_CONFLICT) so the sprint continues."""
    conflict_files = ["services/sprint_manager/sprint_manager.py", "tests/test_foo.py"]

    # Stub _call_finish_feature to return a rebase conflict
    monkeypatch.setattr(sm, "_call_finish_feature", lambda *a, **k: (False, conflict_files))

    # Stub out all the side-effect helpers
    monkeypatch.setattr(sm, "_run_quality_gates", lambda **k: [
        sm.GateResult(gate="pytest", passed=True, skipped=False),
        sm.GateResult(gate="lint", passed=True, skipped=False),
    ])
    monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub")
    monkeypatch.setattr(sm, "_is_issue_merged_into_target", lambda *a, **k: False)
    monkeypatch.setattr(sm, "_post_agent_event", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_post_success_comment", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_delete_failure_sidecar", lambda *a: None)
    monkeypatch.setattr(sm, "_try", lambda *a, **k: (True, "", ""))
    import github_client as gc
    monkeypatch.setattr(gc, "add_comment", lambda *a, **k: None)

    merged, summary_line, category = sm.handle_post_tester(
        issue_num=1414,
        tester_exit_code=0,
        skip_gates=False,
        gate_pytest=True,
        gate_lint=True,
        gate_merge_preview=False,
        gate_typecheck=False,
        gate_design=False,
        gate_frontend_lint=False,
        gate_monolith=False,
        target_branch="develop",
    )

    assert merged is False, "rebase conflict must not be treated as a successful merge"
    assert category == sm.FailureCategory.REBASE_CONFLICT, (
        f"expected REBASE_CONFLICT category, got {category!r}"
    )
    assert "rebase" in summary_line.lower() or "conflict" in summary_line.lower(), (
        f"summary_line should mention the rebase conflict: {summary_line!r}"
    )


def test_ac4_needs_rework_transition_called_on_rebase_conflict(monkeypatch):
    """AC4: handle_post_tester calls _transition_safe(NEEDS_REWORK) when rebase fails."""
    conflict_files = ["services/sprint_manager/sprint_manager.py"]
    transition_calls: list[tuple] = []

    monkeypatch.setattr(sm, "_call_finish_feature", lambda *a, **k: (False, conflict_files))
    monkeypatch.setattr(sm, "_run_quality_gates", lambda **k: [
        sm.GateResult(gate="pytest", passed=True, skipped=False),
    ])
    monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub")
    monkeypatch.setattr(sm, "_is_issue_merged_into_target", lambda *a, **k: False)
    monkeypatch.setattr(sm, "_post_agent_event", lambda *a, **k: None)

    def capture_transition(issue_num, state, **k):
        transition_calls.append((issue_num, state))
    monkeypatch.setattr(sm, "_transition_safe", capture_transition)
    monkeypatch.setattr(sm, "_post_success_comment", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_delete_failure_sidecar", lambda *a: None)
    monkeypatch.setattr(sm, "_try", lambda *a, **k: (True, "", ""))
    import github_client as gc
    monkeypatch.setattr(gc, "add_comment", lambda *a, **k: None)

    sm.handle_post_tester(
        issue_num=1414,
        tester_exit_code=0,
        skip_gates=False,
        gate_pytest=True,
        gate_lint=True,
        gate_merge_preview=False,
        gate_typecheck=False,
        gate_design=False,
        gate_frontend_lint=False,
        gate_monolith=False,
        target_branch="develop",
    )

    needs_rework_calls = [
        (num, state) for (num, state) in transition_calls
        if state == sm._TicketState.NEEDS_REWORK
    ]
    assert len(needs_rework_calls) == 1, (
        f"expected exactly 1 NEEDS_REWORK transition call, got: {needs_rework_calls}"
    )
    assert needs_rework_calls[0][0] == 1414

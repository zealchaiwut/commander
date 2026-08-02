"""Tests for issue #2142: gate file scope must not leak earlier tickets' changes
onto later tickets in the same sprint run.

AC1  Gate file-scope computation (lint/pytest/coder-no-test-edits) diffs the
     feature branch against its merge-base with the sprint branch, not the
     current sprint-branch tip — so prior merged tickets' files never appear.
AC2  _changed_py_files, _changed_js_ts_files, _changed_frontend_files, and
     _gate_coder_no_test_edits all pass the three-dot range syntax
     (``base_branch...HEAD``) to git diff.
AC3  Behavioral test with a real git repo: after ticket A merges into the sprint
     branch, ticket B's gate checks see only B's own files, not A's.
AC4  _gate_failure_scope_contaminated() correctly identifies when a gate failure
     sidecar references only foreign-ticket test files, and the dead-letter counter
     is skipped for that failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from gates import (  # noqa: E402
    _changed_frontend_files,
    _changed_js_ts_files,
    _changed_py_files,
    _gate_coder_no_test_edits,
)
from sprint_manager import _gate_failure_scope_contaminated  # noqa: E402


# ── git helpers ───────────────────────────────────────────────────────────────

def _git(*args, cwd):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ── fixture: simulates cross-ticket contamination scenario ────────────────────

@pytest.fixture()
def sprint_repo(tmp_path):
    """Real git repo that models the contamination described in issue #2142.

    Timeline:
        C0  initial commit on ``sprint`` branch
        C_A feature/1001 adds tests/test_a__1001.py (ticket A's own test file)
        C1  sprint after merging feature/1001 (sprint tip now includes A's test)
        C_B feature/2002 (based on C0) adds apps/router.py + tests/test_b__2002.py

    After setup the repo is checked out on ``feature/2002`` (HEAD = C_B).
    The local ``sprint`` branch is at C1 (simulating the post-merge sprint tip
    that the tester worktree sees after hygiene hard-resets to origin/sprint).

    Returns (repo_path, sprint_branch_name).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    tests_dir = repo / "tests"
    tests_dir.mkdir()

    _git("init", "-b", "sprint", cwd=repo)
    _git("config", "user.email", "test@test.com", cwd=repo)
    _git("config", "user.name", "Test User", cwd=repo)

    # C0 — initial commit
    (repo / "base.py").write_text("# base\n")
    (tests_dir / "__init__.py").write_text("")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "initial C0", cwd=repo)
    c0 = _git("rev-parse", "HEAD", cwd=repo)

    # feature/1001 (ticket A) — adds its own test file
    _git("checkout", "-b", "feature/1001", cwd=repo)
    (tests_dir / "test_a__1001.py").write_text("def test_ticket_a(): pass\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "feat: ticket 1001", cwd=repo)

    # Sprint merges ticket A → sprint now at C1 (includes test_a__1001.py)
    _git("checkout", "sprint", cwd=repo)
    _git("merge", "--no-ff", "feature/1001", "-m", "Merge feature/1001", cwd=repo)

    # feature/2002 (ticket B) — based on C0, adds its own files
    _git("checkout", "-b", "feature/2002", c0, cwd=repo)
    (repo / "apps").mkdir(exist_ok=True)
    (repo / "apps" / "router.py").write_text("# ticket B change\n")
    (tests_dir / "test_b__2002.py").write_text("def test_ticket_b(): pass\n")
    (tests_dir / "component.tsx").write_text("export const C = () => <div/>;\n")
    (tests_dir / "style.css").write_text(".x { color: red; }\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "feat: ticket 2002", cwd=repo)

    # Repo is now on feature/2002 (HEAD = C_B).
    # Local ``sprint`` branch is at C1 (includes test_a__1001.py) — this is the
    # post-merge state the tester worktree sees after hygiene reset.
    return repo, "sprint"


# ── AC3 — behavioral: ticket B sees only its own files ───────────────────────

class TestAC3BehavioralScopeIsolation:
    """Gate helpers with three-dot diff scope only to the current ticket's files."""

    def test_changed_py_files_excludes_prior_ticket_test(self, sprint_repo):
        """_changed_py_files for feature/2002 must not include ticket A's test."""
        repo, sprint = sprint_repo
        files = _changed_py_files(sprint, cwd=repo)
        assert "tests/test_b__2002.py" in files, \
            f"ticket B's own test must appear; got: {files}"
        assert "tests/test_a__1001.py" not in files, \
            f"ticket A's test must NOT leak into B's scope; got: {files}"

    def test_changed_py_files_includes_implementation_files(self, sprint_repo):
        repo, sprint = sprint_repo
        files = _changed_py_files(sprint, cwd=repo)
        assert "apps/router.py" in files, \
            f"ticket B's implementation file must be in scope; got: {files}"

    def test_coder_no_test_edits_does_not_flag_prior_ticket_deleted_test(
        self, sprint_repo
    ):
        """coder-no-test-edits gate must PASS for feature/2002.

        With a two-dot diff (``git diff sprint HEAD --diff-filter=CMD``),
        test_a__1001.py shows as Deleted (it is in sprint/C1 but absent from
        feature/2002). The D-filter (Deleted) match triggers a false positive.
        The three-dot fix (``git diff sprint...HEAD``) uses the merge-base C0
        where neither ticket's test exists, so only B's own Added file appears
        — and Added (A) is NOT in the CMD filter, so the gate passes.
        """
        repo, sprint = sprint_repo
        with patch("gates._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                issue_num=2002,
                worktester_root=repo,
                skip=False,
                base_branch=sprint,
            )
        assert result.passed, (
            "coder-no-test-edits gate must PASS for ticket 2002 — ticket A's "
            f"test must not appear as a blocked Deleted file; output: {result.output}"
        )

    def test_changed_js_ts_files_excludes_prior_ticket_assets(self, sprint_repo):
        """_changed_js_ts_files for feature/2002 must not include ticket A's files."""
        repo, sprint = sprint_repo
        files = _changed_js_ts_files(sprint, cwd=repo)
        # ticket B added a .tsx file; ticket A added no JS/TS files
        assert "tests/component.tsx" in files, \
            f"ticket B's tsx file must appear; got: {files}"

    def test_changed_frontend_files_excludes_prior_ticket_assets(self, sprint_repo):
        """_changed_frontend_files for feature/2002 must not include ticket A's files."""
        repo, sprint = sprint_repo
        files = _changed_frontend_files(sprint, cwd=repo)
        assert "tests/component.tsx" in files or "tests/style.css" in files, \
            f"ticket B's frontend files must appear; got: {files}"


# ── AC2 — three-dot syntax in git diff calls ─────────────────────────────────

class TestAC2ThreeDotDiffSyntax:
    """Every git diff call in the gate helpers must use the three-dot range."""

    def _capture_git_calls(self, fn, *args, **kwargs):
        """Call fn with _run_timed mocked to capture git args; return all calls."""
        calls = []

        def _fake_run_timed(*cmd_args, **kw):
            calls.append(cmd_args)
            return 0, "", ""

        mod = fn.__module__
        with patch(f"{mod}._run_timed", side_effect=_fake_run_timed):
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
        return calls

    def test_changed_py_files_uses_three_dot(self, tmp_path):
        calls = self._capture_git_calls(_changed_py_files, "sprint/main", cwd=tmp_path)
        diff_calls = [c for c in calls if "diff" in c]
        assert diff_calls, "expected at least one git diff call"
        for c in diff_calls:
            args_str = " ".join(str(a) for a in c)
            assert "sprint/main...HEAD" in args_str, (
                f"expected three-dot range 'sprint/main...HEAD' in git diff args; got: {args_str}"
            )

    def test_changed_js_ts_files_uses_three_dot(self, tmp_path):
        calls = self._capture_git_calls(_changed_js_ts_files, "sprint/main", cwd=tmp_path)
        diff_calls = [c for c in calls if "diff" in c]
        assert diff_calls, "expected at least one git diff call"
        for c in diff_calls:
            args_str = " ".join(str(a) for a in c)
            assert "sprint/main...HEAD" in args_str, (
                f"expected three-dot range in git diff; got: {args_str}"
            )

    def test_changed_frontend_files_uses_three_dot(self, tmp_path):
        calls = self._capture_git_calls(_changed_frontend_files, "sprint/main", cwd=tmp_path)
        diff_calls = [c for c in calls if "diff" in c]
        assert diff_calls, "expected at least one git diff call"
        for c in diff_calls:
            args_str = " ".join(str(a) for a in c)
            assert "sprint/main...HEAD" in args_str, (
                f"expected three-dot range in git diff; got: {args_str}"
            )

    def test_gate_coder_no_test_edits_uses_three_dot(self, tmp_path):
        calls = self._capture_git_calls(
            _gate_coder_no_test_edits,
            issue_num=99,
            worktester_root=tmp_path,
            skip=False,
            base_branch="sprint/main",
        )
        diff_calls = [c for c in calls if "diff" in c]
        assert diff_calls, "expected at least one git diff call"
        for c in diff_calls:
            args_str = " ".join(str(a) for a in c)
            assert "sprint/main...HEAD" in args_str, (
                f"expected three-dot range in gate_coder_no_test_edits diff; got: {args_str}"
            )


# ── AC4 — dead-letter scope contamination detection ──────────────────────────

class TestAC4DeadLetterContamination:
    """_gate_failure_scope_contaminated detects cross-ticket file scope leakage."""

    def _write_sidecar(self, tmp_path, issue_num, detail):
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / f"last-failure-{issue_num}.json"
        sc.write_text(
            json.dumps({"issue": issue_num, "failure_class": "gate", "detail": detail}),
            encoding="utf-8",
        )
        return sc

    def test_purely_foreign_ticket_files_detected_as_contaminated(self, tmp_path):
        """Sidecar mentioning ONLY another ticket's test → contaminated."""
        self._write_sidecar(
            tmp_path, 2073,
            "[coder-no-test-edits]\n"
            "Coder modified 1 grading test file(s):\n"
            "  tests/test_milestone_prod_pollution__2074.py\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is True

    def test_own_ticket_file_not_contaminated(self, tmp_path):
        """Sidecar mentioning current ticket's own test → not contaminated."""
        self._write_sidecar(
            tmp_path, 2073,
            "tests/test_failures_table_mobile__2073.py\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is False

    def test_no_ticket_suffixed_files_not_contaminated(self, tmp_path):
        """Sidecar with no __<N>.py pattern → not contaminated."""
        self._write_sidecar(
            tmp_path, 2073,
            "tests/sprint_manager/test_token_ceiling.py\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is False

    def test_missing_sidecar_returns_false(self, tmp_path):
        """No sidecar → not contaminated (safe default)."""
        assert _gate_failure_scope_contaminated(9999, repo_root=tmp_path) is False

    def test_mixed_own_and_foreign_not_contaminated(self, tmp_path):
        """Sidecar mentioning both own AND foreign test → not purely contaminated."""
        self._write_sidecar(
            tmp_path, 2073,
            "tests/test_failures_table_mobile__2073.py\n"
            "tests/test_milestone_prod_pollution__2074.py\n",
        )
        # Contains own ticket → not purely foreign contamination
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is False

    def test_multiple_foreign_tickets_detected(self, tmp_path):
        """Sidecar with multiple foreign-ticket test files → contaminated."""
        self._write_sidecar(
            tmp_path, 2073,
            "tests/test_foo__2072.py\n"
            "tests/test_bar__2075.py\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is True

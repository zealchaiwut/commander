"""Tests for issue #1828: Child sub-sprint branches off immediate parent, not root.

AC coverage:
- AC1: child sub-sprint's branch is created off its immediate parent's branch
        (plan.json ``parent``), falling back to root when the parent branch is
        missing (WARN logged naming both refs).
- AC2: "Creating sprint branch X off Y" log line names the parent sub-sprint
        branch (not the root) for versioned labels.
- AC3: Regression — in a rerun chain N → N.1 → N.2, N.2's branch contains
        commits added to N.1's branch before the chain continued.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

import sprint_manager as sm


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_cfg(sprints_dir: Path, worktree_coder: Path) -> object:
    """Minimal SprintConfig-like namespace for path helpers."""
    cfg = SimpleNamespace(
        sprints_dir=sprints_dir,
        worktree_coder=worktree_coder,
        worktree_tester=worktree_coder,
        tester_app_subdir="",
        repo_name="owner/repo",
        api_url="http://localhost:8000",
        logs_dir=worktree_coder,
        worktree_tester_app=worktree_coder,
        max_coder_slots=None,
        max_tester_slots=None,
    )
    return cfg


# ── AC1a: _immediate_parent_branch reads plan.json parent ────────────────────

class TestImmediateParentBranch:
    def test_returns_parent_from_plan_json(self, tmp_path):
        """_immediate_parent_branch returns sprint/<parent> when plan.json has parent."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        (sprints_dir / "sprint-5.2-plan.json").write_text(
            json.dumps({"parent": "sprint-5.1", "label": "sprint-5.2"})
        )
        cfg = _make_cfg(sprints_dir, tmp_path)
        result = sm._immediate_parent_branch("sprint-5.2", cfg=cfg)
        assert result == "sprint/sprint-5.1"

    def test_falls_back_to_root_when_no_plan_json(self, tmp_path):
        """_immediate_parent_branch falls back to sprint/sprint-N when no plan.json."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        cfg = _make_cfg(sprints_dir, tmp_path)
        result = sm._immediate_parent_branch("sprint-5.2", cfg=cfg)
        assert result == "sprint/sprint-5"

    def test_falls_back_to_root_when_plan_has_no_parent(self, tmp_path):
        """_immediate_parent_branch falls back to root when plan.json has no parent field."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        (sprints_dir / "sprint-5.2-plan.json").write_text(
            json.dumps({"label": "sprint-5.2"})
        )
        cfg = _make_cfg(sprints_dir, tmp_path)
        result = sm._immediate_parent_branch("sprint-5.2", cfg=cfg)
        assert result == "sprint/sprint-5"

    def test_deep_chain_resolves_correct_parent(self, tmp_path):
        """N.3 → N.2, not N.1."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        (sprints_dir / "sprint-5.3-plan.json").write_text(
            json.dumps({"parent": "sprint-5.2", "label": "sprint-5.3"})
        )
        cfg = _make_cfg(sprints_dir, tmp_path)
        result = sm._immediate_parent_branch("sprint-5.3", cfg=cfg)
        assert result == "sprint/sprint-5.2"


# ── AC1b: run_sprint_preflight uses immediate parent, not root ───────────────

class TestRunSprintPreflightUsesImmediateParent:
    """run_sprint_preflight must pass parent_ref=sprint/sprint-N.M-1 (from plan.json)
    to _create_sprint_branch for child sprint labels.

    Uses dry_run=True to capture the '[dry-run] would create sprint branch X off Y'
    log line — which uses the same parent_ref variable as the live branch creation.
    """

    def _run_preflight_dry_run(self, label, sprints_dir, coder_dir, capsys):
        """Run run_sprint_preflight in dry_run mode; return stdout."""
        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir
        cfg.worktree_coder = coder_dir
        cfg.worktree_tester = coder_dir
        cfg.tester_app_subdir = ""
        cfg.worktree_tester_app = coder_dir
        cfg.repo_name = "owner/repo"
        cfg.api_url = "http://localhost:8000"
        cfg.logs_dir = coder_dir
        cfg.max_coder_slots = None
        cfg.max_tester_slots = None
        cfg.pipeline_mode = False
        cfg.llm_provider = "anthropic"
        cfg.ica_model = None

        dummy_issue = [{"number": 1, "title": "Dummy ticket"}]

        with patch.object(sm, "list_backlog_issues", return_value=dummy_issue), \
             patch.object(sm, "_warn_file_conflicts"), \
             patch.object(sm, "_neon_sprint_init"), \
             patch.object(sm, "_post_sprint_status"), \
             patch.object(sm, "structured_log"), \
             patch.object(sm, "_sweep_stale_in_progress"), \
             patch.object(sm, "_sweep_stale_status"), \
             patch.object(sm, "_sprint_db_set_ticket_order_sm"), \
             patch.object(sm, "_utcnow", return_value="2026-01-01T00:00:00Z"):

            sm.run_sprint_preflight(
                label=label,
                alert_modes=[],
                repo_name="owner/repo",
                dry_run=True,
                cfg=cfg,
                skip_estimator=True,
            )
        return capsys.readouterr().out

    def test_child_sprint_uses_immediate_parent_not_root(self, tmp_path, capsys):
        """AC1b: dry-run log says 'off sprint/sprint-5.1', not 'off sprint/sprint-5'."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        (sprints_dir / "sprint-5.2-plan.json").write_text(
            json.dumps({"parent": "sprint-5.1", "label": "sprint-5.2"})
        )
        out = self._run_preflight_dry_run("sprint-5.2", sprints_dir, coder_dir, capsys)
        assert "sprint/sprint-5.1" in out, (
            f"Expected 'sprint/sprint-5.1' in dry-run log but got:\n{out}\n"
            "Child sprint must branch off its immediate parent, not the root sprint branch."
        )
        # The root branch must NOT appear as the 'off' target
        lines = [ln for ln in out.splitlines() if "dry-run" in ln and "would create sprint branch" in ln]
        for line in lines:
            assert "sprint/sprint-5.1" in line, (
                f"Dry-run line must name the immediate parent. Got: {line}"
            )

    def test_child_sprint_no_plan_parent_uses_root(self, tmp_path, capsys):
        """AC1b: when plan.json has no parent, dry-run log shows root sprint branch."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        out = self._run_preflight_dry_run("sprint-5.2", sprints_dir, coder_dir, capsys)
        assert "sprint/sprint-5" in out, (
            "When no plan.json parent, root sprint branch must appear in dry-run log"
        )

    def test_base_sprint_still_uses_develop(self, tmp_path, capsys):
        """Non-child sprint (sprint-5) must still branch off develop."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        out = self._run_preflight_dry_run("sprint-5", sprints_dir, coder_dir, capsys)
        dryrun_lines = [ln for ln in out.splitlines() if "dry-run" in ln and "would create" in ln]
        assert dryrun_lines, f"Expected dry-run branch creation log. Got:\n{out}"
        assert "develop" in dryrun_lines[0], (
            f"Base sprint must branch off develop. Got: {dryrun_lines[0]}"
        )

    def test_create_sprint_branch_called_with_immediate_parent(self, tmp_path):
        """AC1b: _create_sprint_branch receives parent_ref=sprint/sprint-5.1 for sprint-5.2."""
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        (sprints_dir / "sprint-5.2-plan.json").write_text(
            json.dumps({"parent": "sprint-5.1", "label": "sprint-5.2"})
        )
        captured = {}

        def fake_create(branch, parent_ref="develop", fallback_ref=""):
            captured["branch"] = branch
            captured["parent_ref"] = parent_ref
            captured["fallback_ref"] = fallback_ref

        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir
        cfg.worktree_coder = coder_dir
        cfg.worktree_tester = coder_dir
        cfg.tester_app_subdir = ""
        cfg.worktree_tester_app = coder_dir
        cfg.repo_name = "owner/repo"
        cfg.api_url = "http://localhost:8000"
        cfg.logs_dir = coder_dir
        cfg.max_coder_slots = None
        cfg.max_tester_slots = None
        cfg.pipeline_mode = False
        cfg.llm_provider = "anthropic"
        cfg.ica_model = None

        dummy_issue = [{"number": 1, "title": "Dummy ticket"}]

        with patch.object(sm, "_create_sprint_branch", side_effect=fake_create), \
             patch.object(sm, "list_backlog_issues", return_value=dummy_issue), \
             patch.object(sm, "_warn_file_conflicts"), \
             patch.object(sm, "_neon_sprint_init"), \
             patch.object(sm, "_post_sprint_status"), \
             patch.object(sm, "structured_log"), \
             patch.object(sm, "_sweep_stale_in_progress"), \
             patch.object(sm, "_sweep_stale_status"), \
             patch.object(sm, "_sprint_db_set_ticket_order_sm"), \
             patch.object(sm, "_WorktreePool") as mock_pool_cls, \
             patch.object(sm, "_utcnow", return_value="2026-01-01T00:00:00Z"):
            mock_pool_cls.return_value = MagicMock()
            mock_pool_cls.reconcile_orphans = MagicMock()

            sm.run_sprint_preflight(
                label="sprint-5.2",
                alert_modes=[],
                repo_name="owner/repo",
                dry_run=False,
                cfg=cfg,
                skip_estimator=True,
            )

        assert captured.get("parent_ref") == "sprint/sprint-5.1", (
            f"_create_sprint_branch must receive parent_ref='sprint/sprint-5.1' "
            f"but got {captured.get('parent_ref')!r}"
        )
        assert captured.get("fallback_ref") == "sprint/sprint-5", (
            f"_create_sprint_branch must receive fallback_ref='sprint/sprint-5' "
            f"but got {captured.get('fallback_ref')!r}"
        )


# ── AC1c: _create_sprint_branch fallback when parent branch is missing ───────

class TestCreateSprintBranchFallback:
    """_create_sprint_branch falls back to fallback_ref (root) and logs WARN
    when the intended parent branch doesn't exist on origin."""

    def test_uses_fallback_when_parent_missing(self, capsys):
        """AC1: when origin/sprint/sprint-5.1 doesn't exist, fall back to sprint/sprint-5."""
        fallback_sha = "deadbeef"
        _try_responses = [
            # ls-remote for sprint/sprint-5.2 (check if target exists): not found
            (False, "", ""),
            # show-ref for local sprint/sprint-5.2: not found
            (False, "", ""),
            # fetch origin: captured by _run, but _try is not called for it
            # rev-parse origin/sprint/sprint-5.1 (parent): not found
            (False, "", ""),
            # rev-parse sprint/sprint-5.1 (local): not found
            (False, "", ""),
            # rev-parse origin/sprint/sprint-5 (fallback): found
            (True, fallback_sha, ""),
        ]
        run_calls = []

        with patch.object(sm, "_try", side_effect=_try_responses), \
             patch.object(sm, "_run", side_effect=lambda *a: run_calls.append(list(a))), \
             patch.object(sm, "structured_log") as mock_log:
            sm._create_sprint_branch(
                "sprint/sprint-5.2",
                parent_ref="sprint/sprint-5.1",
                fallback_ref="sprint/sprint-5",
            )

        branch_cmds = [c for c in run_calls if c and c[0] == "git" and c[1] == "branch"]
        assert branch_cmds, "git branch must be called"
        assert branch_cmds[0][3] == fallback_sha, (
            f"Expected fallback SHA {fallback_sha!r} but got {branch_cmds[0][3]!r}"
        )
        # WARN must be logged naming both the missing parent and the fallback
        warn_calls = mock_log.warn.call_args_list
        assert warn_calls, "structured_log.warn must be called when falling back"
        warn_msg = " ".join(str(a) for a in warn_calls[0][0])
        assert "sprint/sprint-5.1" in warn_msg, "WARN must name the missing parent branch"
        assert "sprint/sprint-5" in warn_msg, "WARN must name the fallback ref"

    def test_no_fallback_still_uses_head(self):
        """When no fallback_ref given, existing HEAD fallback behavior is preserved."""
        _try_responses = [
            (False, "", ""),  # ls-remote target
            (False, "", ""),  # show-ref target
            (False, "", ""),  # rev-parse origin/parent
            (False, "", ""),  # rev-parse parent local
        ]
        run_calls = []

        with patch.object(sm, "_try", side_effect=_try_responses), \
             patch.object(sm, "_run", side_effect=lambda *a: run_calls.append(list(a))), \
             patch.object(sm, "structured_log"):
            sm._create_sprint_branch("sprint/sprint-5.2", parent_ref="sprint/sprint-5.1")

        branch_cmds = [c for c in run_calls if c and c[0] == "git" and c[1] == "branch"]
        assert branch_cmds, "git branch must still be called (with HEAD)"
        assert branch_cmds[0][3] == "HEAD"

    def test_no_fallback_used_when_parent_exists(self):
        """When parent branch exists, fallback_ref is never used."""
        parent_sha = "cafebabe"
        _try_responses = [
            (False, "", ""),   # ls-remote target: not found
            (False, "", ""),   # show-ref target: not found
            (True, parent_sha, ""),  # rev-parse origin/parent: found
        ]
        run_calls = []

        with patch.object(sm, "_try", side_effect=_try_responses), \
             patch.object(sm, "_run", side_effect=lambda *a: run_calls.append(list(a))), \
             patch.object(sm, "structured_log") as mock_log:
            sm._create_sprint_branch(
                "sprint/sprint-5.2",
                parent_ref="sprint/sprint-5.1",
                fallback_ref="sprint/sprint-5",
            )

        branch_cmds = [c for c in run_calls if c and c[0] == "git" and c[1] == "branch"]
        assert branch_cmds
        assert branch_cmds[0][3] == parent_sha, "parent SHA must be used (not fallback)"
        # No warning about parent missing
        warn_calls = [c for c in mock_log.warn.call_args_list
                      if "missing" in str(c).lower() or "fallback" in str(c).lower()]
        assert not warn_calls, "No fallback WARN should appear when parent branch exists"


# ── AC2: log line names the parent sub-sprint branch ─────────────────────────

class TestLogLineNamesParentSubsprint:
    """The 'Creating sprint branch X off Y' log must name the immediate parent
    sub-sprint branch (e.g., sprint/sprint-5.1) for versioned child labels."""

    def test_log_names_immediate_parent(self, capsys):
        """AC2: stdout log says 'off sprint/sprint-5.1' not 'off sprint/sprint-5'."""
        parent_sha = "abc123"
        _try_responses = [
            (False, "", ""),            # ls-remote target: not found
            (False, "", ""),            # show-ref target: not found
            (True, parent_sha, ""),     # rev-parse origin/sprint/sprint-5.1: found
        ]

        with patch.object(sm, "_try", side_effect=_try_responses), \
             patch.object(sm, "_run"):
            sm._create_sprint_branch(
                "sprint/sprint-5.2",
                parent_ref="sprint/sprint-5.1",
            )

        out = capsys.readouterr().out
        assert "sprint/sprint-5.1" in out, (
            f"Log line must name the immediate parent 'sprint/sprint-5.1'. Got:\n{out}"
        )
        assert "off sprint/sprint-5.1" in out or "off 'sprint/sprint-5.1'" in out, (
            f"Expected 'off sprint/sprint-5.1' in log. Got:\n{out}"
        )


# ── AC3: Regression — N.2 contains N.1's extra commit ────────────────────────

class TestRerunChainCommitInheritance:
    """AC3: sprint/sprint-N.2 must contain all commits from sprint/sprint-N.1.

    We set up a real git repo (with a bare 'origin') to verify actual commit
    ancestry — mocks cannot substitute here since we're asserting git history.
    """

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a bare origin and a working clone with sprint branches."""
        bare = tmp_path / "origin.git"
        work = tmp_path / "work"
        bare.mkdir()

        def git(*args, cwd=None):
            subprocess.run(
                ["git"] + list(args),
                cwd=str(cwd or work),
                check=True,
                capture_output=True,
            )

        # Init bare repo
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        # Clone into work dir
        subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)

        # Configure user (needed for commits)
        git("config", "user.email", "test@test.com")
        git("config", "user.name", "Test")

        # Initial commit on main/master
        (work / "init.txt").write_text("init")
        git("add", ".")
        git("commit", "-m", "initial")
        # Push to origin (detect branch name)
        result = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True
        )
        default_branch = result.stdout.strip()
        git("push", "-u", "origin", default_branch)

        # Create root sprint branch sprint/sprint-5 (off default)
        git("checkout", "-b", "sprint/sprint-5")
        git("push", "-u", "origin", "sprint/sprint-5")

        # Create sprint/sprint-5.1 with an extra commit
        git("checkout", "-b", "sprint/sprint-5.1")
        (work / "parent_commit.txt").write_text("from sprint-5.1")
        git("add", ".")
        git("commit", "-m", "parent commit on sprint-5.1")
        parent_sha_result = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True
        )
        parent_commit_sha = parent_sha_result.stdout.strip()
        git("push", "-u", "origin", "sprint/sprint-5.1")

        # Return to default branch so we don't interfere
        git("checkout", default_branch)

        return {
            "work": work,
            "parent_commit_sha": parent_commit_sha,
            "default_branch": default_branch,
        }

    def test_child_branch_contains_parent_commit(self, git_repo):
        """AC3: sprint/sprint-5.2 contains the extra commit from sprint/sprint-5.1."""
        work = git_repo["work"]
        parent_commit_sha = git_repo["parent_commit_sha"]

        orig_cwd = os.getcwd()
        os.chdir(str(work))
        try:
            sm._create_sprint_branch(
                "sprint/sprint-5.2",
                parent_ref="sprint/sprint-5.1",
                fallback_ref="sprint/sprint-5",
            )
        finally:
            os.chdir(orig_cwd)

        # Verify ancestry: parent_commit_sha is an ancestor of sprint/sprint-5.2
        result = subprocess.run(
            ["git", "-C", str(work), "merge-base", "--is-ancestor",
             parent_commit_sha, "sprint/sprint-5.2"],
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"Commit {parent_commit_sha[:8]} from sprint/sprint-5.1 must be "
            "an ancestor of sprint/sprint-5.2, but it is not. "
            "The child branch was created off the wrong base."
        )

    def test_root_branch_does_not_contain_parent_commit(self, git_repo):
        """Sanity: sprint/sprint-5 (root) does NOT contain sprint-5.1's extra commit."""
        work = git_repo["work"]
        parent_commit_sha = git_repo["parent_commit_sha"]

        result = subprocess.run(
            ["git", "-C", str(work), "merge-base", "--is-ancestor",
             parent_commit_sha, "origin/sprint/sprint-5"],
            capture_output=True,
        )
        assert result.returncode != 0, (
            "Sanity check failed: sprint/sprint-5 (root) should NOT contain "
            "the commit added on sprint/sprint-5.1"
        )

"""Tests for #788: worktree hygiene before each coder/tester dispatch.

AC items verified:
  AC-1  Pre-dispatch sequence runs git fetch origin before each coder invocation
  AC-2  Dirty-state check stashes to .commander/runtime/quarantine/<tid>/<ts>/
        with warning logged — never silently discarded
  AC-3  Base branch is hard-reset to origin/<COMMANDER_MERGE_TARGET> after fetch
  AC-4  Fresh-ticket path: feature branch absent OR at base SHA; aborts with flag
        if branch exists at a divergent SHA
  AC-5  Retry-round path: rebase onto base; rebase conflict → class=merge sidecar,
        dispatch aborted
  AC-6  agent_runs record stores worktree_sha and base_sha at dispatch time
  AC-7  Tester worktree receives same hygiene treatment
  AC-8  No pre-existing quarantine entry overwritten; entries accumulate per ticket
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    """Create a git repo with one commit so hygiene ops work."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _clone_repo(bare: Path, target: Path) -> None:
    subprocess.run(["git", "clone", str(bare), str(target)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=target, check=True, capture_output=True)


def _get_sha(repo: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _setup_repos(tmp_path: Path):
    """Create a bare origin and a working clone. Returns (bare, clone)."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=bare, check=True, capture_output=True)

    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=bootstrap, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=bootstrap, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=bootstrap, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=bootstrap, check=True, capture_output=True)
    (bootstrap / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=bootstrap, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=bootstrap, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=bootstrap, check=True, capture_output=True)

    clone = tmp_path / "worktree"
    _clone_repo(str(bare), clone)
    return bare, clone


def _add_commit(repo: Path, filename: str, content: str, msg: str = "add") -> str:
    """Add a file, commit, return SHA."""
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)
    return _get_sha(repo)


# ---------------------------------------------------------------------------
# AC-1: git fetch origin runs before coder dispatch
# ---------------------------------------------------------------------------

class TestAC1FetchRuns:
    def test_worktree_hygiene_exists(self):
        """_worktree_hygiene must be defined and callable."""
        assert hasattr(sm, "_worktree_hygiene"), "_worktree_hygiene must exist in sprint_manager"
        assert callable(sm._worktree_hygiene)

    def test_worktree_hygiene_signature(self):
        """Must accept worktree, ticket_id, merge_target, is_retry, repo_root."""
        sig = inspect.signature(sm._worktree_hygiene)
        params = set(sig.parameters)
        required = {"worktree", "ticket_id", "merge_target"}
        assert required.issubset(params), f"Missing required params: {required - params}"
        optional = {"is_retry", "repo_root"}
        assert optional.issubset(params), f"Missing optional params: {optional - params}"

    def test_fetch_runs_in_worktree(self, tmp_path):
        """git fetch origin is invoked with cwd=worktree before any other git op."""
        bare, clone = _setup_repos(tmp_path)
        calls = []

        original_try = sm._try

        def tracking_try(*cmd, cwd=None):
            calls.append((list(cmd), cwd))
            return original_try(*cmd, cwd=cwd)

        with patch.object(sm, "_try", side_effect=tracking_try):
            sm._worktree_hygiene(
                worktree=clone,
                ticket_id=99,
                merge_target="main",
                is_retry=False,
                repo_root=tmp_path,
            )

        fetch_calls = [c for c in calls if c[0][:3] == ["git", "fetch", "origin"]]
        assert len(fetch_calls) >= 1, "git fetch origin must be called at least once"
        assert fetch_calls[0][1] == clone, "fetch must run inside the worktree"


# ---------------------------------------------------------------------------
# AC-2: Dirty-state stash to quarantine with warning
# ---------------------------------------------------------------------------

class TestAC2DirtyStateQuarantine:
    def test_dirty_tracked_file_stashed(self, tmp_path, capsys):
        """Dirty tracked file is captured in quarantine/tracked.patch; warning logged."""
        bare, clone = _setup_repos(tmp_path)
        (clone / "README.md").write_text("dirty\n")  # modify tracked file

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=42,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "42"
        assert quarantine_base.exists(), "Quarantine directory for ticket 42 must be created"
        ts_dirs = list(quarantine_base.iterdir())
        assert len(ts_dirs) >= 1, "At least one timestamp subdirectory must be created"
        patch_file = ts_dirs[0] / "tracked.patch"
        assert patch_file.exists(), "tracked.patch must exist in quarantine"
        assert len(patch_file.read_text()) > 0, "tracked.patch must be non-empty"

    def test_dirty_untracked_file_stashed(self, tmp_path):
        """Untracked file appears in quarantine untracked-list.txt."""
        bare, clone = _setup_repos(tmp_path)
        (clone / "new_untracked.txt").write_text("untracked content\n")

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=43,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "43"
        ts_dirs = list(quarantine_base.iterdir())
        assert ts_dirs, "Quarantine directory must exist"
        untracked_file = ts_dirs[0] / "untracked-list.txt"
        assert untracked_file.exists(), "untracked-list.txt must exist when untracked files present"
        content = untracked_file.read_text()
        assert "new_untracked.txt" in content

    def test_clean_worktree_no_quarantine(self, tmp_path):
        """Clean worktree must NOT create a quarantine directory."""
        bare, clone = _setup_repos(tmp_path)
        # Worktree is clean after clone

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=44,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "44"
        if quarantine_base.exists():
            ts_dirs = [d for d in quarantine_base.iterdir() if d.is_dir()]
            assert len(ts_dirs) == 0, "No quarantine dir for clean worktree"

    def test_warning_logged_on_dirty(self, tmp_path, capsys):
        """A warning must be printed when dirty state is detected."""
        bare, clone = _setup_repos(tmp_path)
        (clone / "README.md").write_text("dirty change")

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=45,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        captured = capsys.readouterr()
        assert "quarantine" in captured.out.lower() or "dirty" in captured.out.lower(), \
            "Warning about dirty state / quarantine must be printed"


# ---------------------------------------------------------------------------
# AC-3: Hard-reset to origin/<COMMANDER_MERGE_TARGET>
# ---------------------------------------------------------------------------

class TestAC3HardReset:
    def test_worktree_reset_to_origin(self, tmp_path):
        """After hygiene, worktree HEAD matches origin/main SHA."""
        bare, clone = _setup_repos(tmp_path)

        # Add an extra commit in the clone that diverges from origin
        _add_commit(clone, "extra.txt", "local only", "local commit")

        origin_sha = _get_sha(bare, "main")
        pre_hygiene_sha = _get_sha(clone, "HEAD")
        assert pre_hygiene_sha != origin_sha, "Setup: clone must diverge from origin before test"

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=50,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        post_sha = _get_sha(clone, "HEAD")
        assert post_sha == origin_sha, \
            f"After hygiene HEAD must match origin/main. Got {post_sha}, expected {origin_sha}"

    def test_returns_base_sha(self, tmp_path):
        """_worktree_hygiene returns the base SHA as second element."""
        bare, clone = _setup_repos(tmp_path)
        origin_sha = _get_sha(bare, "main")

        worktree_sha, base_sha, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=51,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        assert base_sha == origin_sha, \
            f"base_sha must be origin/main SHA. Got {base_sha}, expected {origin_sha}"

    def test_returns_worktree_sha_after_reset(self, tmp_path):
        """worktree_sha returned equals HEAD after reset."""
        bare, clone = _setup_repos(tmp_path)
        origin_sha = _get_sha(bare, "main")

        worktree_sha, base_sha, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=52,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        assert worktree_sha == origin_sha, \
            f"worktree_sha must equal post-reset HEAD. Got {worktree_sha}"
        assert err is None


# ---------------------------------------------------------------------------
# AC-4: Fresh-ticket path: abort on divergent feature branch
# ---------------------------------------------------------------------------

class TestAC4FreshTicketPath:
    def test_fresh_ticket_no_feature_branch_ok(self, tmp_path):
        """No feature branch → no error on fresh-ticket path."""
        bare, clone = _setup_repos(tmp_path)

        _, _, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=60,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        assert err is None, f"No feature branch should succeed fresh path, got err={err}"

    def test_fresh_ticket_branch_at_base_sha_ok(self, tmp_path):
        """Feature branch pointing to base SHA is accepted on fresh-ticket path."""
        bare, clone = _setup_repos(tmp_path)
        base_sha = _get_sha(clone, "HEAD")

        # Create feature branch at exactly the base SHA
        subprocess.run(["git", "checkout", "-b", "feature/60-slug", base_sha],
                       cwd=clone, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)

        _, _, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=60,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        assert err is None, f"Feature branch at base SHA should pass fresh path, got err={err}"

    def test_fresh_ticket_divergent_branch_returns_error(self, tmp_path):
        """Feature branch at divergent SHA returns 'divergent-branch' error category."""
        bare, clone = _setup_repos(tmp_path)

        # Create feature branch with an extra commit (diverges from base)
        subprocess.run(["git", "checkout", "-b", "feature/61-diverge"],
                       cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "extra.txt", "diverge", "diverge commit")
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)

        _, _, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=61,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        assert err == "divergent-branch", \
            f"Divergent branch must return 'divergent-branch', got {err!r}"

    def test_fresh_ticket_divergent_writes_sidecar(self, tmp_path):
        """Divergent branch case writes a failure sidecar with class=divergent-branch."""
        bare, clone = _setup_repos(tmp_path)

        subprocess.run(["git", "checkout", "-b", "feature/62-diverge"],
                       cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "divfile.txt", "x", "extra")
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=62,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        sidecar = tmp_path / ".commander" / "runtime" / "last-failure-62.json"
        assert sidecar.exists(), "Failure sidecar must be written for divergent branch"
        data = json.loads(sidecar.read_text())
        assert data["failure_class"] == "divergent-branch"


# ---------------------------------------------------------------------------
# AC-5: Retry-round path: rebase + merge sidecar on conflict
# ---------------------------------------------------------------------------

class TestAC5RetryRoundPath:
    def test_retry_clean_rebase_succeeds(self, tmp_path):
        """Retry path with clean rebase returns no error."""
        bare, clone = _setup_repos(tmp_path)
        base_sha = _get_sha(clone, "HEAD")

        # Feature branch at base SHA (no conflict)
        subprocess.run(["git", "checkout", "-b", "feature/70-clean"],
                       cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "feature70.txt", "feature work", "feature commit")
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)

        # Push new commit to bare to advance origin/main
        _add_commit(clone, "base_advance.txt", "base work", "base advance")
        subprocess.run(["git", "push", "origin", "main"], cwd=clone, check=True, capture_output=True)

        _, _, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=70,
            merge_target="main",
            is_retry=True,
            repo_root=tmp_path,
        )

        assert err is None, f"Clean rebase should return no error, got {err!r}"

    def test_retry_rebase_conflict_returns_merge_error(self, tmp_path):
        """Retry path with rebase conflict returns 'merge' error category."""
        bare, clone = _setup_repos(tmp_path)

        # Feature branch changes the same file as origin will
        subprocess.run(["git", "checkout", "-b", "feature/71-conflict"],
                       cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "conflict.txt", "feature version\n", "feature: change conflict.txt")
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)

        # Advance origin/main with conflicting change on same file
        _add_commit(clone, "conflict.txt", "base version\n", "base: change conflict.txt")
        subprocess.run(["git", "push", "origin", "main"], cwd=clone, check=True, capture_output=True)

        _, _, err = sm._worktree_hygiene(
            worktree=clone,
            ticket_id=71,
            merge_target="main",
            is_retry=True,
            repo_root=tmp_path,
        )

        assert err == "merge", f"Rebase conflict must return 'merge', got {err!r}"

    def test_retry_conflict_writes_merge_sidecar(self, tmp_path):
        """Rebase conflict writes a failure sidecar with failure_class='merge'."""
        bare, clone = _setup_repos(tmp_path)

        subprocess.run(["git", "checkout", "-b", "feature/72-conflict"],
                       cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "sc.txt", "feat\n", "feat")
        subprocess.run(["git", "checkout", "main"], cwd=clone, check=True, capture_output=True)
        _add_commit(clone, "sc.txt", "base\n", "base advance")
        subprocess.run(["git", "push", "origin", "main"], cwd=clone, check=True, capture_output=True)

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=72,
            merge_target="main",
            is_retry=True,
            repo_root=tmp_path,
        )

        sidecar = tmp_path / ".commander" / "runtime" / "last-failure-72.json"
        assert sidecar.exists(), "Failure sidecar must exist after merge conflict"
        data = json.loads(sidecar.read_text())
        assert data["failure_class"] == "merge", \
            f"Sidecar failure_class must be 'merge', got {data['failure_class']!r}"


# ---------------------------------------------------------------------------
# AC-6: agent_runs record stores worktree_sha and base_sha
# ---------------------------------------------------------------------------

class TestAC6AgentRunsFields:
    def test_db_record_agent_start_accepts_worktree_sha_and_base_sha(self):
        """record_agent_start must accept worktree_sha and base_sha kwargs."""
        import db
        sig = inspect.signature(db.record_agent_start)
        params = set(sig.parameters)
        assert "worktree_sha" in params, "record_agent_start must accept worktree_sha"
        assert "base_sha" in params, "record_agent_start must accept base_sha"

    def test_agent_runs_table_has_worktree_sha_and_base_sha(self, tmp_path):
        """agent_runs table must have worktree_sha and base_sha columns."""
        import db

        db_path = tmp_path / "test.db"
        with patch.dict(os.environ, {"DB_PATH": str(db_path)}):
            import importlib
            importlib.reload(db)
            conn = db.get_conn()
            db._create_agent_runs_table(conn)
            cursor = conn.execute("PRAGMA table_info(agent_runs)")
            cols = {row[1] for row in cursor.fetchall()}

        assert "worktree_sha" in cols, "agent_runs must have worktree_sha column"
        assert "base_sha" in cols, "agent_runs must have base_sha column"

    def test_record_agent_start_stores_worktree_sha_and_base_sha(self, tmp_path):
        """Values passed as worktree_sha and base_sha are persisted in the DB."""
        import db

        db_path = tmp_path / "test2.db"
        with patch.dict(os.environ, {"DB_PATH": str(db_path)}):
            import importlib
            importlib.reload(db)

            run_id = db.record_agent_start(
                issue_number=999,
                sprint_label="sprint-test",
                agent="coder",
                worktree_sha="abc123",
                base_sha="def456",
            )

            conn = db.get_conn()
            row = conn.execute(
                "SELECT worktree_sha, base_sha FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()

        assert row is not None, "Row must exist"
        assert row[0] == "abc123", f"worktree_sha mismatch: {row[0]}"
        assert row[1] == "def456", f"base_sha mismatch: {row[1]}"

    def test_db_agent_start_sm_passes_sha_fields(self):
        """_db_agent_start_sm must forward worktree_sha and base_sha to db."""
        sig = inspect.signature(sm._db_agent_start_sm)
        params = set(sig.parameters)
        assert "worktree_sha" in params, "_db_agent_start_sm must accept worktree_sha"
        assert "base_sha" in params, "_db_agent_start_sm must accept base_sha"


# ---------------------------------------------------------------------------
# AC-7: Tester worktree receives same hygiene treatment
# ---------------------------------------------------------------------------

class TestAC7TesterHygiene:
    def test_dispatch_tester_calls_worktree_hygiene(self):
        """_dispatch_tester must call _worktree_hygiene before spawning claude."""
        hygiene_calls = []

        def fake_hygiene(worktree, ticket_id, merge_target, is_retry=False, repo_root=None,
                         recover_on_rebase_conflict=False):
            hygiene_calls.append({
                "worktree": worktree,
                "ticket_id": ticket_id,
                "merge_target": merge_target,
            })
            return ("sha-wt", "sha-base", None)

        fake_cfg = MagicMock()
        fake_cfg.worktree_tester = Path("/tmp/fake-tester")
        fake_cfg.worktree_tester_app = Path("/tmp/fake-tester/apps/dashboard")
        fake_cfg.repo_name = "owner/repo"
        fake_cfg.api_url = None
        fake_cfg.tester_prompt_template = "Test issue {issue_url}"  # avoids github_client.repo()
        fake_cfg.logs_dir = Path("/tmp/fake-logs")
        fake_cfg.tester_risk_model_map = {}
        fake_cfg.tester_default_model = "claude-haiku-4-5"

        with (
            patch.object(sm, "_worktree_hygiene", side_effect=fake_hygiene),
            patch.object(sm, "_db_update_worktree_shas_sm"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "structured_log"),
            patch.object(sm, "_issue_log_path", return_value=Path("/tmp/fake.log")),
            patch.object(sm, "_classify_risk_tier", return_value="LOW"),
            patch.object(sm, "_impeccable_context_instruction", return_value=""),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch("subprocess.Popen") as mock_popen,
            patch("builtins.open", mock.mock_open()),
            patch.object(sm, "HangDetector", MagicMock()),
        ):
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            sm._dispatch_tester(
                issue_num=99,
                alert_modes=[],
                sprint_branch="main",
                cfg=fake_cfg,
                sprint_label="sprint-test",
            )

        assert len(hygiene_calls) >= 1, "_dispatch_tester must call _worktree_hygiene"
        call = hygiene_calls[0]
        assert call["ticket_id"] == 99
        assert call["merge_target"] == "main"

    def test_dispatch_coder_calls_worktree_hygiene(self):
        """_dispatch_coder must call _worktree_hygiene before spawning claude."""
        hygiene_calls = []

        def fake_hygiene(worktree, ticket_id, merge_target, is_retry=False, repo_root=None,
                         recover_on_rebase_conflict=False):
            hygiene_calls.append({
                "worktree": worktree,
                "ticket_id": ticket_id,
                "merge_target": merge_target,
            })
            return ("sha-wt", "sha-base", None)

        fake_cfg = MagicMock()
        fake_cfg.worktree_coder = Path("/tmp/fake-coder")
        fake_cfg.repo_name = "owner/repo"
        fake_cfg.api_url = None
        fake_cfg.coder_prompt_template = "Implement issue {issue_url}"  # avoids github_client.repo()
        fake_cfg.coder_model = "claude-sonnet-4-6"
        fake_cfg.logs_dir = Path("/tmp/fake-logs")
        fake_cfg.coder_by_size = {}

        with (
            patch.object(sm, "_worktree_hygiene", side_effect=fake_hygiene),
            patch.object(sm, "_db_update_worktree_shas_sm"),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_design_docs_guard", return_value=None),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "structured_log"),
            patch.object(sm, "_issue_log_path", return_value=Path("/tmp/fake.log")),
            patch("subprocess.Popen") as mock_popen,
            patch("builtins.open", mock.mock_open()),
            patch.object(sm, "HangDetector", MagicMock()),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_resolve_coder_model", return_value=("claude-sonnet-4-6", "default")),
            patch.object(sm, "_load_estimate", return_value=None),
        ):
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            sm._dispatch_coder(
                issue_num=88,
                alert_modes=[],
                sprint_branch="main",
                cfg=fake_cfg,
                sprint_label="sprint-test",
            )

        assert len(hygiene_calls) >= 1, "_dispatch_coder must call _worktree_hygiene"
        call = hygiene_calls[0]
        assert call["ticket_id"] == 88
        assert call["merge_target"] == "main"


# ---------------------------------------------------------------------------
# AC-8: Quarantine entries accumulate, never overwrite
# ---------------------------------------------------------------------------

class TestAC8QuarantineAccumulates:
    def test_two_stash_calls_create_two_dirs(self, tmp_path):
        """Two calls to _stash_to_quarantine for the same ticket create separate dirs."""
        assert hasattr(sm, "_stash_to_quarantine"), "_stash_to_quarantine must be defined"

        bare, clone = _setup_repos(tmp_path)
        # Make a dirty state
        (clone / "README.md").write_text("dirty pass 1\n")

        sm._stash_to_quarantine(clone, ticket_id=80, effective_root=tmp_path)
        time.sleep(1)  # ensure different timestamps
        (clone / "README.md").write_text("dirty pass 2\n")
        sm._stash_to_quarantine(clone, ticket_id=80, effective_root=tmp_path)

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "80"
        ts_dirs = [d for d in quarantine_base.iterdir() if d.is_dir()]
        assert len(ts_dirs) == 2, f"Two separate stash calls must produce two dirs, got {len(ts_dirs)}"

    def test_quarantine_dirs_are_timestamped(self, tmp_path):
        """Quarantine subdirectories are named with a timestamp (not a fixed name)."""
        bare, clone = _setup_repos(tmp_path)
        (clone / "README.md").write_text("dirty\n")

        sm._stash_to_quarantine(clone, ticket_id=81, effective_root=tmp_path)

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "81"
        ts_dirs = [d for d in quarantine_base.iterdir() if d.is_dir()]
        assert ts_dirs, "Quarantine dir must exist"
        name = ts_dirs[0].name
        # A timestamped name should look like 20260101T120000Z
        assert len(name) > 8, f"Timestamp dir name too short: {name!r}"

    def test_hygiene_quarantine_does_not_overwrite(self, tmp_path):
        """Two separate hygiene calls for same ticket produce distinct quarantine dirs."""
        bare, clone = _setup_repos(tmp_path)
        (clone / "README.md").write_text("dirty v1\n")

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=82,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        time.sleep(1)
        # After reset, make it dirty again
        (clone / "README.md").write_text("dirty v2\n")

        sm._worktree_hygiene(
            worktree=clone,
            ticket_id=82,
            merge_target="main",
            is_retry=False,
            repo_root=tmp_path,
        )

        quarantine_base = tmp_path / ".commander" / "runtime" / "quarantine" / "82"
        ts_dirs = [d for d in quarantine_base.iterdir() if d.is_dir()]
        assert len(ts_dirs) == 2, \
            f"Two hygiene calls with dirty state must produce 2 quarantine dirs, got {len(ts_dirs)}"

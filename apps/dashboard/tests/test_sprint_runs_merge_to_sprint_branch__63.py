"""Tests for issue #63: fix sprint runs must merge to sprint branch, not develop.

Risk: MEDIUM — 1-2 tests per AC criterion.

AC1 — Sprint manager creates sprint/sprint-N branch at sprint start
AC2 — Sprint manager passes COMMANDER_MERGE_TARGET=sprint/sprint-N to coder/tester
AC3 — Coder creates feature branches from sprint branch when in sprint mode
AC4 — Tester merges feature branches into sprint branch when in sprint mode
AC5 — develop is NOT touched during a sprint run
AC6 — At sprint end, PR is auto-created (sprint/sprint-N → develop) with sprint summary
AC7 — PR URL is printed in sprint summary output
AC8 — Manual triggers (no COMMANDER_MERGE_TARGET) still merge to develop as before
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR    = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT  = SCRIPTS_DIR / "sprint_manager.py"
FINISH_SCRIPT  = SCRIPTS_DIR / "finish_feature.py"
START_SCRIPT   = SCRIPTS_DIR / "start_feature.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py with stubbed dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    gc_stub.get_issue     = lambda *a, **kw: {"title": "test issue"}
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_63_import"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC1 — Sprint manager creates sprint/sprint-N branch at sprint start
# ---------------------------------------------------------------------------

class TestAC1SprintBranchCreation:
    def test_create_sprint_branch_function_exists(self):
        """_create_sprint_branch function must be defined in sprint_manager.py."""
        sm = _import_sprint_manager()
        assert hasattr(sm, "_create_sprint_branch"), \
            "_create_sprint_branch must be defined in sprint_manager.py"

    def test_run_sprint_calls_create_sprint_branch(self, tmp_path):
        """run_sprint() must call _create_sprint_branch when target is the sprint branch.

        Note: _create_sprint_branch is only called after backlog issues are loaded.
        We return a dummy issue so run_sprint does not exit early.
        """
        sm = _import_sprint_manager()

        created_branches = []

        def fake_create_sprint_branch(branch):
            created_branches.append(branch)

        def fake_list_backlog(label, repo_name=None):
            # Return one issue so run_sprint does not early-exit
            return [{"number": 1, "title": "dummy issue"}]

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, f"done", None

        def fake_post_sprint_status(state, api_url=None):
            pass

        with mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "list_backlog_issues", fake_list_backlog), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", fake_post_sprint_status):
            sm.run_sprint(
                label="sprint-5",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert "sprint/sprint-5" in created_branches, \
            "run_sprint must call _create_sprint_branch('sprint/sprint-5')"

    def test_sprint_branch_name_derived_from_label(self):
        """Sprint branch name must follow sprint/<label> pattern."""
        sm = _import_sprint_manager()
        content = SPRINT_SCRIPT.read_text()
        assert "sprint_branch = f\"sprint/{" in content or "sprint/{label}" in content or \
               "sprint/sprint-" in content or "sprint/{args.label}" in content, \
            "sprint_manager must derive sprint branch name from label"


# ---------------------------------------------------------------------------
# AC2 — Sprint manager passes COMMANDER_MERGE_TARGET to coder/tester
# ---------------------------------------------------------------------------

class TestAC2CommanderMergeTargetEnvVar:
    def test_commander_merge_target_set_in_coder_env(self):
        """_dispatch_coder must set COMMANDER_MERGE_TARGET in subprocess env
        when sprint_branch is not 'develop'."""
        sm = _import_sprint_manager()

        captured_envs = []

        original_popen = subprocess.Popen

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env", {}))
            raise FileNotFoundError("claude not found")  # stub: no real claude

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=99,
                alert_modes=[],
                sprint_branch="sprint/sprint-5",
            )

        assert captured_envs, "subprocess.Popen should have been called"
        env = captured_envs[0]
        assert "COMMANDER_MERGE_TARGET" in env, \
            "COMMANDER_MERGE_TARGET must be set in coder subprocess env"
        assert env["COMMANDER_MERGE_TARGET"] == "sprint/sprint-5", \
            "COMMANDER_MERGE_TARGET must equal the sprint branch name"

    def test_commander_merge_target_set_in_tester_env(self):
        """_dispatch_tester must set COMMANDER_MERGE_TARGET in subprocess env
        when sprint_branch is not 'develop'."""
        sm = _import_sprint_manager()

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env", {}))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_tester(
                issue_num=99,
                alert_modes=[],
                sprint_branch="sprint/sprint-5",
            )

        assert captured_envs, "subprocess.Popen should have been called"
        env = captured_envs[0]
        assert "COMMANDER_MERGE_TARGET" in env, \
            "COMMANDER_MERGE_TARGET must be set in tester subprocess env"
        assert env["COMMANDER_MERGE_TARGET"] == "sprint/sprint-5", \
            "COMMANDER_MERGE_TARGET must equal the sprint branch name"


# ---------------------------------------------------------------------------
# AC3 — Coder creates feature branches from sprint branch (via env var)
# ---------------------------------------------------------------------------

class TestAC3CoderSprintBranchMode:
    def test_coder_env_not_set_when_develop_mode(self):
        """COMMANDER_MERGE_TARGET must NOT be set when sprint_branch is 'develop'."""
        sm = _import_sprint_manager()

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env", {}))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=99,
                alert_modes=[],
                sprint_branch="develop",
            )

        if captured_envs:
            env = captured_envs[0]
            # COMMANDER_MERGE_TARGET must NOT be set in manual/develop mode
            assert "COMMANDER_MERGE_TARGET" not in env, \
                "COMMANDER_MERGE_TARGET must NOT be set when sprint_branch='develop'"

    def test_start_feature_has_base_branch_param(self):
        """start_feature.py must accept --base-branch flag so coder can pass the sprint branch."""
        content = START_SCRIPT.read_text()
        assert "--base-branch" in content, \
            "start_feature.py must accept --base-branch flag"


# ---------------------------------------------------------------------------
# AC4 — Tester merges feature branches into sprint branch (via env var)
# ---------------------------------------------------------------------------

class TestAC4TesterSprintBranchMode:
    def test_finish_feature_has_target_branch_param(self):
        """finish_feature.py must accept --target-branch flag so tester can merge to sprint branch."""
        content = FINISH_SCRIPT.read_text()
        assert "--target-branch" in content, \
            "finish_feature.py must accept --target-branch flag"

    def test_dispatch_tester_signature_has_sprint_branch(self):
        """_dispatch_tester must accept sprint_branch parameter."""
        sm = _import_sprint_manager()
        import inspect
        sig = inspect.signature(sm._dispatch_tester)
        assert "sprint_branch" in sig.parameters, \
            "_dispatch_tester must accept sprint_branch parameter"


# ---------------------------------------------------------------------------
# AC5 — develop is NOT touched during a sprint run
# ---------------------------------------------------------------------------

class TestAC5DevelopNotTouched:
    def test_default_target_is_sprint_branch_not_develop(self, tmp_path):
        """run_sprint() must default target_branch to sprint/<label>, not 'develop'.

        Returns a dummy issue so run_sprint does not early-exit before creating the branch.
        """
        sm = _import_sprint_manager()

        used_target_branches = []

        def fake_create(branch):
            used_target_branches.append(branch)

        def fake_list_backlog(label, repo_name=None):
            return [{"number": 1, "title": "dummy issue"}]

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        def fake_post_sprint_status(state, api_url=None):
            pass

        with mock.patch.object(sm, "_create_sprint_branch", fake_create), \
             mock.patch.object(sm, "list_backlog_issues", fake_list_backlog), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", fake_post_sprint_status):
            summary, state = sm.run_sprint(
                label="sprint-7",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        # Sprint branch should be created (not develop)
        assert "sprint/sprint-7" in used_target_branches, \
            "Sprint mode must use sprint/sprint-7, not develop"
        assert "develop" not in used_target_branches, \
            "develop must not be directly created/targeted in sprint mode"

    def test_run_sprint_passes_sprint_target_to_tester(self):
        """The target_branch passed to handle_post_tester must be the sprint branch."""
        sm = _import_sprint_manager()

        post_tester_calls = []

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            post_tester_calls.append(target_branch)
            return True, f"Issue #{issue_num}: merged into {target_branch}", None

        def fake_list_backlog(label, repo_name=None):
            return [{"number": 99, "title": "test issue"}]

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None):
            return 0, None

        def fake_create_sprint_branch(branch):
            pass

        def fake_post_sprint_status(state, api_url=None):
            pass

        with mock.patch.object(sm, "list_backlog_issues", fake_list_backlog), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "_post_sprint_status", fake_post_sprint_status):
            sm.run_sprint(
                label="sprint-5",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert post_tester_calls, "handle_post_tester must have been called"
        assert post_tester_calls[0] == "sprint/sprint-5", \
            f"handle_post_tester must receive target_branch='sprint/sprint-5', got {post_tester_calls[0]!r}"


# ---------------------------------------------------------------------------
# AC6 — PR auto-created at sprint end: sprint/sprint-N → develop
# ---------------------------------------------------------------------------

class TestAC6SprintPRCreation:
    def test_create_sprint_pr_function_exists(self):
        """_create_sprint_pr function must be defined."""
        sm = _import_sprint_manager()
        assert hasattr(sm, "_create_sprint_pr"), \
            "_create_sprint_pr must be defined in sprint_manager.py"

    def test_create_sprint_pr_calls_gh_pr_create(self):
        """_create_sprint_pr must call 'gh pr create' with --base develop --head sprint/sprint-N."""
        sm = _import_sprint_manager()

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = "https://github.com/zealchaiwut/commander/pull/100\n"
            result.stderr = ""
            return result

        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = []

        with mock.patch("subprocess.run", side_effect=fake_run):
            url = sm._create_sprint_pr(
                sprint_branch="sprint/sprint-5",
                sprint_label="sprint-5",
                sprint_number=5,
                state=state,
                repo_name="zealchaiwut/commander",
            )

        assert url is not None, "_create_sprint_pr must return a URL on success"
        assert "https://github.com" in url, "PR URL must be a GitHub URL"

        # Verify gh pr create was called with correct args
        pr_create_calls = [c for c in captured_cmds if "pr" in c and "create" in c]
        assert pr_create_calls, "gh pr create must be called"
        call = pr_create_calls[0]
        assert "--base" in call and "develop" in call, \
            "PR must target develop as base branch"
        assert "--head" in call and "sprint/sprint-5" in call, \
            "PR head must be the sprint branch"

    def test_create_sprint_pr_body_includes_shipped_tickets(self):
        """PR body must include a list of shipped ticket numbers."""
        sm = _import_sprint_manager()

        captured_bodies = []

        def fake_run(cmd, **kwargs):
            # Extract body from cmd
            if "--body" in cmd:
                body_idx = cmd.index("--body")
                captured_bodies.append(cmd[body_idx + 1])
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = "https://github.com/zealchaiwut/commander/pull/101\n"
            result.stderr = ""
            return result

        issue = sm.IssueState(number=42, title="some feature", status="done")
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [issue]

        with mock.patch("subprocess.run", side_effect=fake_run):
            sm._create_sprint_pr(
                sprint_branch="sprint/sprint-5",
                sprint_label="sprint-5",
                sprint_number=5,
                state=state,
                repo_name="zealchaiwut/commander",
            )

        assert captured_bodies, "PR body must be passed to gh pr create"
        body = captured_bodies[0]
        assert "#42" in body, "Shipped ticket #42 must appear in PR body"


# ---------------------------------------------------------------------------
# AC7 — PR URL is printed in sprint summary output
# ---------------------------------------------------------------------------

class TestAC7PRUrlPrinted:
    def test_sprint_pr_url_printed_in_main_output(self):
        """sprint_manager.py main() must print 'Sprint PR: <url>' when PR is created."""
        content = SPRINT_SCRIPT.read_text()
        assert "sprint_pr_url" in content, \
            "sprint_pr_url variable must be used in sprint_manager.py"
        assert "Sprint PR:" in content, \
            "main() must print 'Sprint PR:' when a PR is created"

    def test_create_sprint_pr_called_in_main(self):
        """main() code must call _create_sprint_pr (not develop merge) at sprint end."""
        content = SPRINT_SCRIPT.read_text()
        assert "_create_sprint_pr(" in content, \
            "_create_sprint_pr must be called in sprint_manager.py"


# ---------------------------------------------------------------------------
# AC8 — Manual triggers (no COMMANDER_MERGE_TARGET) still merge to develop
# ---------------------------------------------------------------------------

class TestAC8ManualTriggersDevelop:
    def test_develop_target_skips_sprint_branch_creation(self, tmp_path):
        """When target_branch='develop' is explicitly passed, sprint branch creation is skipped."""
        sm = _import_sprint_manager()

        created_branches = []

        def fake_create_sprint_branch(branch):
            created_branches.append(branch)

        def fake_list_backlog(label, repo_name=None):
            return []

        with mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "list_backlog_issues", fake_list_backlog):
            sm.run_sprint(
                label="sprint-5",
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                target_branch="develop",  # explicit override
            )

        assert "sprint/sprint-5" not in created_branches, \
            "Sprint branch must not be created when target_branch='develop'"

    def test_coder_no_merge_target_when_develop(self):
        """When sprint_branch='develop', COMMANDER_MERGE_TARGET must not be set in coder env."""
        sm = _import_sprint_manager()

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env") or {})
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=42,
                alert_modes=[],
                sprint_branch="develop",
            )

        if captured_envs:
            env = captured_envs[0]
            assert "COMMANDER_MERGE_TARGET" not in env, \
                "COMMANDER_MERGE_TARGET must not be injected in manual/develop mode"

    def test_finish_feature_default_target_is_develop(self):
        """finish_feature.py must default --target-branch to 'develop' for backward compat."""
        content = FINISH_SCRIPT.read_text()
        # Default value for target_branch arg must be develop
        assert 'default="develop"' in content or "default='develop'" in content, \
            "finish_feature.py --target-branch must default to 'develop'"

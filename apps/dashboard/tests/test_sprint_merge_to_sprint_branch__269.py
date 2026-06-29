"""Tests for issue #269: Fix sprint tickets to merge into sprint branch, not develop.

Acceptance Criteria:
  AC-1: sprint branch created at start; log does NOT show "sprint branch creation
        skipped" unless --target-branch was explicitly passed.
  AC-2: every per-ticket finish_feature.py call in dispatch uses
        --target-branch sprint/sprint-N, not --target-branch develop.
  AC-3: develop receives no new commits during the sprint run.
  AC-4: at sprint end, PR sprint/sprint-N -> develop is created successfully.
  AC-5: passing --target-branch develop explicitly still works as a deliberate
        override (silent config/default path no longer triggers this override).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).parent.parent.parent.parent  # .../coder or .../tester
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
FINISH_FEATURE_PATH = REPO_ROOT / "scripts" / "finish_feature.py"
START_FEATURE_PATH  = REPO_ROOT / "scripts" / "start_feature.py"


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py with stubbed heavy dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    gc_stub.get_issue     = lambda *a, **kw: {"title": "test issue"}
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_269_import"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    assert SPRINT_MANAGER_PATH.exists(), \
        f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}"

    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_MANAGER_PATH)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1: sprint branch created at start; no "sprint branch creation skipped"
#       unless --target-branch is explicitly passed
# ---------------------------------------------------------------------------

class TestAC1SprintBranchCreatedAtStart:
    def test_run_sprint_creates_sprint_branch_by_default(self, capsys, tmp_path):
        """When no target_branch is given, run_sprint must call _create_sprint_branch
        with 'sprint/sprint-N' and NOT print 'sprint branch creation skipped'."""
        sm = _import_sprint_manager()

        created_branches: list[str] = []

        def fake_create_sprint_branch(branch: str) -> None:
            created_branches.append(branch)

        def fake_list_backlog(label, repo_name=None):
            return [{"number": 1, "title": "dummy"}]

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None,
                                rate_limit_events=None, on_running=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None,
                                 rate_limit_events=None, on_running=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        def fake_post_sprint_status(state, api_url=None, project=None):
            pass

        with mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "list_backlog_issues", fake_list_backlog), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", fake_post_sprint_status), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert "sprint/sprint-18" in created_branches, (
            "run_sprint must call _create_sprint_branch('sprint/sprint-18') "
            "when no target_branch is passed"
        )

        captured = capsys.readouterr()
        assert "sprint branch creation skipped" not in captured.out, (
            "Log must NOT contain 'sprint branch creation skipped' when no "
            "--target-branch override is in effect"
        )

    def test_run_sprint_no_sprint_branch_when_develop_explicit(self, tmp_path):
        """When target_branch='develop' is explicitly passed, sprint branch creation
        must be skipped (deliberate override — AC-5)."""
        sm = _import_sprint_manager()

        created_branches: list[str] = []

        def fake_create_sprint_branch(branch: str) -> None:
            created_branches.append(branch)

        def fake_list_backlog(label, repo_name=None):
            return []

        with mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "list_backlog_issues", fake_list_backlog), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                target_branch="develop",  # explicit override
            )

        assert "sprint/sprint-18" not in created_branches, (
            "sprint branch must NOT be created when target_branch='develop' is "
            "explicitly passed (deliberate develop-override)"
        )


# ---------------------------------------------------------------------------
# AC-2: target_branch=sprint/sprint-N reaches coder and tester dispatchers
# ---------------------------------------------------------------------------

class TestAC2SprintBranchPassedToDispatchers:
    def test_dispatch_coder_receives_sprint_branch_by_default(self, tmp_path):
        """When no target_branch is given, _dispatch_coder must receive
        sprint_branch='sprint/sprint-N' so COMMANDER_MERGE_TARGET is set."""
        sm = _import_sprint_manager()

        coder_sprint_branches: list[str] = []

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None,
                                rate_limit_events=None, on_running=None):
            coder_sprint_branches.append(sprint_branch)
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None,
                                 rate_limit_events=None, on_running=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        with mock.patch.object(sm, "_create_sprint_branch", lambda b: None), \
             mock.patch.object(sm, "list_backlog_issues",
                               lambda label, repo_name=None: [{"number": 5, "title": "t"}]), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert coder_sprint_branches, "_dispatch_coder must have been called"
        assert coder_sprint_branches[0] == "sprint/sprint-18", (
            f"_dispatch_coder must receive sprint_branch='sprint/sprint-18', "
            f"got {coder_sprint_branches[0]!r}"
        )

    def test_dispatch_tester_receives_sprint_branch_by_default(self, tmp_path):
        """When no target_branch is given, _dispatch_tester must receive
        sprint_branch='sprint/sprint-N' so COMMANDER_MERGE_TARGET is set for tester."""
        sm = _import_sprint_manager()

        tester_sprint_branches: list[str] = []

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None,
                                rate_limit_events=None, on_running=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None,
                                 rate_limit_events=None, on_running=None):
            tester_sprint_branches.append(sprint_branch)
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        with mock.patch.object(sm, "_create_sprint_branch", lambda b: None), \
             mock.patch.object(sm, "list_backlog_issues",
                               lambda label, repo_name=None: [{"number": 5, "title": "t"}]), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert tester_sprint_branches, "_dispatch_tester must have been called"
        assert tester_sprint_branches[0] == "sprint/sprint-18", (
            f"_dispatch_tester must receive sprint_branch='sprint/sprint-18', "
            f"got {tester_sprint_branches[0]!r}"
        )


# ---------------------------------------------------------------------------
# AC-3: develop receives no new commits — target_branch in handle_post_tester
#       must be sprint/sprint-N, not develop
# ---------------------------------------------------------------------------

class TestAC3DevelopNotTouched:
    def test_handle_post_tester_receives_sprint_branch_not_develop(self, tmp_path):
        """When no target_branch is given, handle_post_tester must receive
        target_branch='sprint/sprint-N' so merges go to sprint branch."""
        sm = _import_sprint_manager()

        post_tester_targets: list[str] = []

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            post_tester_targets.append(target_branch)
            return True, f"done with {target_branch}", None

        with mock.patch.object(sm, "_create_sprint_branch", lambda b: None), \
             mock.patch.object(sm, "list_backlog_issues",
                               lambda label, repo_name=None: [{"number": 7, "title": "t"}]), \
             mock.patch.object(sm, "_dispatch_coder",
                               lambda *a, **kw: (True, None)), \
             mock.patch.object(sm, "_dispatch_tester",
                               lambda *a, **kw: (0, None)), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
            )

        assert post_tester_targets, "handle_post_tester must have been called"
        assert post_tester_targets[0] == "sprint/sprint-18", (
            f"handle_post_tester must receive target_branch='sprint/sprint-18' "
            f"to prevent merges into develop; got {post_tester_targets[0]!r}"
        )


# ---------------------------------------------------------------------------
# AC-4: sprint PR created at end (sprint/sprint-N -> develop)
# ---------------------------------------------------------------------------

class TestAC4SprintPRCreated:
    def test_sprint_pr_function_exists(self):
        """_create_sprint_pr must be defined in sprint_manager.py."""
        sm = _import_sprint_manager()
        assert hasattr(sm, "_create_sprint_pr"), \
            "_create_sprint_pr must be defined in sprint_manager.py"

    def test_sprint_manager_calls_create_sprint_pr_in_main(self):
        """sprint_manager.py main() must call _create_sprint_pr when
        sprint-branch mode is active (not a 'develop' override)."""
        content = SPRINT_MANAGER_PATH.read_text()
        assert "_create_sprint_pr(" in content, \
            "_create_sprint_pr must be called in sprint_manager.py"

    def test_create_sprint_pr_targets_develop_as_base(self):
        """_create_sprint_pr must call 'gh pr create' with --base develop."""
        sm = _import_sprint_manager()

        captured_cmds: list[list] = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = "https://github.com/zealchaiwut/commander/pull/200\n"
            result.stderr = ""
            return result

        state = sm.SprintState(sprint_label="sprint-18", sprint_number=18)
        state.issues = []

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            url = sm._create_sprint_pr(
                sprint_branch="sprint/sprint-18",
                sprint_label="sprint-18",
                sprint_number=18,
                state=state,
                repo_name="zealchaiwut/commander",
            )

        assert url is not None, "_create_sprint_pr must return a URL on success"
        pr_calls = [c for c in captured_cmds if "pr" in c and "create" in c]
        assert pr_calls, "gh pr create must have been called"
        call = pr_calls[0]
        assert "--base" in call and "develop" in call, \
            "PR must target develop as base branch"
        assert "--head" in call and "sprint/sprint-18" in call, \
            "PR head must be the sprint branch"


# ---------------------------------------------------------------------------
# AC-5: passing --target-branch develop explicitly still works as override
# ---------------------------------------------------------------------------

class TestAC5ExplicitDevelopOverride:
    def test_explicit_develop_target_skips_sprint_branch_creation(self, capsys):
        """When target_branch='develop' is passed, sprint branch creation is skipped
        AND the log mentions 'custom target branch' or 'skipped'.

        We provide one issue so run_sprint does not exit early before printing
        the sprint-branch decision message.
        """
        sm = _import_sprint_manager()

        created_branches: list[str] = []

        def fake_create_sprint_branch(branch: str) -> None:
            created_branches.append(branch)

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None,
                                rate_limit_events=None, on_running=None):
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None,
                                 rate_limit_events=None, on_running=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        with mock.patch.object(sm, "_create_sprint_branch", fake_create_sprint_branch), \
             mock.patch.object(sm, "list_backlog_issues",
                               lambda label, repo_name=None: [{"number": 9, "title": "x"}]), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                target_branch="develop",
            )

        assert "sprint/sprint-18" not in created_branches, (
            "Sprint branch must NOT be created when target_branch='develop' "
            "is explicitly passed"
        )
        captured = capsys.readouterr()
        assert "skipped" in captured.out.lower() or "custom target" in captured.out.lower(), (
            "Log must note that sprint branch creation was skipped for the "
            f"deliberate 'develop' override. Output was:\n{captured.out}"
        )

    def test_default_path_no_longer_produces_develop_as_target(self, tmp_path):
        """The default (no --target-branch arg) must not result in target_branch='develop'
        reaching _dispatch_coder. This is the root bug AC-5 guards against."""
        sm = _import_sprint_manager()

        coder_branches: list[str] = []

        def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                                repo_name=None, cfg=None, chosen_port=None,
                                rate_limit_events=None, on_running=None):
            coder_branches.append(sprint_branch)
            return True, None

        def fake_dispatch_tester(issue_num, alert_modes, sprint_branch="develop",
                                 repo_name=None, cfg=None, chosen_port=None,
                                 rate_limit_events=None, on_running=None):
            return 0, None

        def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                    gate_pytest, gate_lint, gate_merge_preview,
                                    target_branch="develop", **kwargs):
            return True, "done", None

        with mock.patch.object(sm, "_create_sprint_branch", lambda b: None), \
             mock.patch.object(sm, "list_backlog_issues",
                               lambda label, repo_name=None: [{"number": 3, "title": "x"}]), \
             mock.patch.object(sm, "_dispatch_coder", fake_dispatch_coder), \
             mock.patch.object(sm, "_dispatch_tester", fake_dispatch_tester), \
             mock.patch.object(sm, "handle_post_tester", fake_handle_post_tester), \
             mock.patch.object(sm, "_post_sprint_status", lambda s, **kw: None), \
             mock.patch.object(sm, "_warn_file_conflicts", lambda issues: None), \
             mock.patch.object(sm, "_setup_pid_file", lambda n: None), \
             mock.patch.object(sm, "_transition_safe", lambda *a, **k: None), \
             mock.patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"):
            sm.run_sprint(
                label="sprint-18",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                # No target_branch — this is the default path
            )

        assert coder_branches, "_dispatch_coder must have been called"
        assert coder_branches[0] != "develop", (
            "The silent default path must NOT pass 'develop' to _dispatch_coder. "
            f"Got: {coder_branches[0]!r}. This is the root bug that caused tickets "
            "to merge into develop instead of sprint/sprint-N."
        )

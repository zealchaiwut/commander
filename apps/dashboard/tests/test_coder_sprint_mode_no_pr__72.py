"""Tests for issue #72: coder agent respects COMMANDER_MERGE_TARGET — no PR in sprint mode.

Risk: MEDIUM — 1-2 tests per AC criterion.

AC1 — Coder reads COMMANDER_MERGE_TARGET env var
AC2 — Coder creates feature branch off that target when set
AC3 — Coder pushes feature branch but does NOT open a PR in sprint mode
AC4 — Coder agent prompt template makes "no PR creation in sprint mode" explicit
AC5 — finish_feature.py / sprint manager handles all PR creation
AC6 — Manual /coder command can still open PR to develop (backward compat)
AC7 — Coder output mentions which target branch the feature was based off
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

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
START_SCRIPT  = SCRIPTS_DIR / "start_feature.py"
FINISH_SCRIPT = SCRIPTS_DIR / "finish_feature.py"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"

AGENTS_DIR    = Path(__file__).parent.parent / ".claude" / "agents"
CODER_AGENT   = AGENTS_DIR / "coder.md"
COMMANDS_DIR  = Path(__file__).parent.parent / ".claude" / "commands"
CODER_CMD     = COMMANDS_DIR / "coder.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py with stubbed dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo        = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    gc_stub.get_issue     = lambda *a, **kw: {"title": "test issue"}
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_72_import"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC1 — Coder reads COMMANDER_MERGE_TARGET env var
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_stub_success")
class TestAC1CoderReadsMergeTarget:
    def test_sprint_manager_sets_commander_merge_target(self):
        """_dispatch_coder must set COMMANDER_MERGE_TARGET when sprint_branch is not develop."""
        sm = _import_sprint_manager()

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env", {}))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=72,
                alert_modes=[],
                sprint_branch="sprint/sprint-5",
            )

        assert captured_envs, "subprocess.Popen must have been called"
        env = captured_envs[0]
        assert "COMMANDER_MERGE_TARGET" in env, \
            "COMMANDER_MERGE_TARGET must be set in coder subprocess env in sprint mode"
        assert env["COMMANDER_MERGE_TARGET"] == "sprint/sprint-5", \
            "COMMANDER_MERGE_TARGET must equal the sprint branch"

    def test_start_feature_accepts_base_branch_flag(self):
        """start_feature.py must accept --base-branch flag to receive COMMANDER_MERGE_TARGET."""
        content = START_SCRIPT.read_text()
        assert "--base-branch" in content, \
            "start_feature.py must accept --base-branch flag"


# ---------------------------------------------------------------------------
# AC2 — Coder creates feature branch off that target when set
# ---------------------------------------------------------------------------

class TestAC2FeatureBranchOffTarget:
    def test_start_feature_uses_base_branch_arg(self):
        """start_feature.py must use the --base-branch value when branching."""
        content = START_SCRIPT.read_text()
        # Must reference the base arg (not hardcode develop)
        assert "args.base_branch" in content or "base = args.base_branch" in content, \
            "start_feature.py must use args.base_branch as the base for the feature branch"

    def test_sprint_manager_prompt_includes_base_branch_arg(self):
        """Sprint manager injected prompt must tell coder to pass --base-branch to start_feature.py."""
        content = SPRINT_SCRIPT.read_text()
        assert "--base-branch" in content, \
            "sprint_manager.py injected prompt must include --base-branch flag"


# ---------------------------------------------------------------------------
# AC3 — Coder does NOT open a PR in sprint mode
# ---------------------------------------------------------------------------

class TestAC3NoPRInSprintMode:
    def test_sprint_manager_injected_prompt_says_no_pr(self):
        """Sprint manager must inject 'no PR' instruction into coder prompt in sprint mode."""
        content = SPRINT_SCRIPT.read_text()
        # Must contain instruction to not open a PR
        assert "do NOT open a PR" in content or "no PR" in content or "do not open a PR" in content.lower(), \
            "sprint_manager.py must inject 'do NOT open a PR' instruction in sprint mode coder prompt"

    def test_commander_merge_target_not_set_in_develop_mode(self):
        """COMMANDER_MERGE_TARGET must NOT be set when sprint_branch='develop'."""
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
            assert "COMMANDER_MERGE_TARGET" not in env, \
                "COMMANDER_MERGE_TARGET must NOT be set when sprint_branch='develop' (manual mode)"


# ---------------------------------------------------------------------------
# AC4 — Coder agent prompt template makes "no PR creation in sprint mode" explicit
# ---------------------------------------------------------------------------

class TestAC4AgentPromptNoPR:
    def test_coder_agent_md_exists(self):
        """Coder agent definition file must exist."""
        assert CODER_AGENT.exists(), f"Coder agent file not found at {CODER_AGENT}"

    def test_coder_agent_references_commander_merge_target(self):
        """Coder agent prompt must reference COMMANDER_MERGE_TARGET."""
        if not CODER_AGENT.exists():
            pytest.skip("Coder agent file not found")
        content = CODER_AGENT.read_text()
        assert "COMMANDER_MERGE_TARGET" in content, \
            "Coder agent prompt must reference COMMANDER_MERGE_TARGET env var"

    def test_coder_agent_mentions_no_pr_sprint_mode(self):
        """Coder agent prompt must explicitly state 'no PR creation in sprint mode'."""
        if not CODER_AGENT.exists():
            pytest.skip("Coder agent file not found")
        content = CODER_AGENT.read_text()
        has_no_pr = (
            "do NOT open a PR" in content
            or "no PR" in content
            or "sprint mode" in content.lower()
        )
        assert has_no_pr, \
            "Coder agent prompt must mention no PR creation in sprint mode"

    def test_coder_agent_description_mentions_sprint_mode(self):
        """Coder agent frontmatter description must mention sprint mode or COMMANDER_MERGE_TARGET."""
        if not CODER_AGENT.exists():
            pytest.skip("Coder agent file not found")
        content = CODER_AGENT.read_text()
        # The frontmatter description (first ~10 lines) must mention sprint mode
        frontmatter_area = content[:800]
        has_sprint_info = (
            "COMMANDER_MERGE_TARGET" in frontmatter_area
            or "sprint" in frontmatter_area.lower()
            or "no PR" in frontmatter_area
        )
        assert has_sprint_info, \
            "Coder agent frontmatter description must mention sprint mode or COMMANDER_MERGE_TARGET"


# ---------------------------------------------------------------------------
# AC5 — finish_feature.py / sprint manager handles all PR creation
# ---------------------------------------------------------------------------

class TestAC5SprintManagerHandlesPR:
    def test_create_sprint_pr_function_exists(self):
        """Sprint manager must define _create_sprint_pr for PR creation at sprint end."""
        sm = _import_sprint_manager()
        assert hasattr(sm, "_create_sprint_pr"), \
            "_create_sprint_pr function must exist in sprint_manager.py"

    def test_finish_feature_does_not_call_gh_pr_create(self):
        """finish_feature.py must NOT call 'gh pr create' — it only merges branches."""
        content = FINISH_SCRIPT.read_text()
        assert "gh pr create" not in content, \
            "finish_feature.py must not open PRs — that is sprint manager's job"


# ---------------------------------------------------------------------------
# AC6 — Manual /coder command can still open PR to develop (backward compat)
# ---------------------------------------------------------------------------

class TestAC6ManualModeOpensPR:
    def test_coder_agent_mentions_manual_mode_pr(self):
        """Coder agent prompt must describe opening PR to develop in manual mode."""
        if not CODER_AGENT.exists():
            pytest.skip("Coder agent file not found")
        content = CODER_AGENT.read_text()
        has_manual_pr = (
            "gh pr create" in content
            or "manual mode" in content.lower()
            or "open a PR" in content
        )
        assert has_manual_pr, \
            "Coder agent prompt must describe PR creation in manual (non-sprint) mode"

    def test_commander_merge_target_absent_means_manual_mode(self):
        """When COMMANDER_MERGE_TARGET is absent, coder subprocess env must not have it."""
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
                "In manual mode (sprint_branch=develop) COMMANDER_MERGE_TARGET must not be set"


# ---------------------------------------------------------------------------
# AC7 — Coder output mentions which target branch the feature was based off
# ---------------------------------------------------------------------------

class TestAC7OutputMentionsBaseBranch:
    def test_start_feature_prints_base_branch(self):
        """start_feature.py output must mention the base branch ('based off')."""
        content = START_SCRIPT.read_text()
        assert "based off" in content, \
            "start_feature.py must print 'based off <base>' in its output"

    def test_start_feature_prints_base_in_success_message(self):
        """The success message in start_feature.py must include the base branch variable."""
        content = START_SCRIPT.read_text()
        # Check that the success print includes the base variable (not hardcoded)
        assert "based off {base}" in content or "based off \" + base" in content or \
               "based off {base}" in content, \
            "start_feature.py success message must dynamically include the base branch name"

    def test_start_feature_prints_base_header(self):
        """start_feature.py must print 'Base: <base>' to stderr/stdout before branching."""
        content = START_SCRIPT.read_text()
        assert 'print(f"Base:' in content or "Base:" in content, \
            "start_feature.py must print the base branch name before creating the feature branch"


# ---------------------------------------------------------------------------
# Regression — issue #72: sprint hints injected even when coder_prompt_template set
# ---------------------------------------------------------------------------

class TestIssue72RegressionCustomTemplate:
    """Regression tests for the bug where a custom coder_prompt_template caused
    sprint-mode instructions to be skipped (issue #72)."""

    def _make_cfg(self, sm, template: str):
        """Build a minimal SprintConfig-like object with a custom prompt template."""
        # SprintConfig is a dataclass; instantiate it normally then override fields.
        cfg = sm.SprintConfig(
            repo_name             = "zealchaiwut/commander",
            coder_prompt_template = template,
            tester_prompt_template= "Tester: {issue_url}",
        )
        return cfg

    def test_coder_sprint_hint_injected_with_custom_template(self):
        """When cfg.coder_prompt_template is set AND sprint_branch != develop,
        the dispatched prompt must still contain COMMANDER_MERGE_TARGET and --base-branch.
        """
        sm = _import_sprint_manager()
        cfg = self._make_cfg(sm, "Coder: please fix {issue_url}")

        captured_cmds = []

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=72,
                alert_modes=[],
                sprint_branch="sprint/sprint-5",
                cfg=cfg,
            )

        assert captured_cmds, "subprocess.Popen must have been called"
        prompt_arg = captured_cmds[0][-1]
        assert "COMMANDER_MERGE_TARGET" in prompt_arg, (
            "Sprint-mode hint with COMMANDER_MERGE_TARGET must be appended to coder prompt "
            "even when cfg.coder_prompt_template is set (issue #72 regression)"
        )
        assert "--base-branch" in prompt_arg, (
            "--base-branch instruction must appear in coder prompt "
            "even when cfg.coder_prompt_template is set (issue #72 regression)"
        )
        assert "sprint/sprint-5" in prompt_arg, (
            "Sprint branch name must appear in injected coder prompt"
        )

    def test_coder_sprint_hint_still_present_after_template_formatting(self):
        """The template base text and the sprint hint must both appear in the final prompt."""
        sm = _import_sprint_manager()
        cfg = self._make_cfg(sm, "Custom coder prompt for {issue_url}")

        captured_cmds = []

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_coder(
                issue_num=72,
                alert_modes=[],
                sprint_branch="sprint/alpha",
                cfg=cfg,
            )

        prompt_arg = captured_cmds[0][-1]
        # Original template content must still be there
        assert "Custom coder prompt" in prompt_arg, \
            "The custom template body must be preserved in the final prompt"
        # Sprint hint must be appended on top
        assert "do NOT open a PR" in prompt_arg, \
            "'do NOT open a PR' sprint instruction must be appended even with custom template"

    def test_tester_sprint_hint_injected_with_custom_template(self):
        """When cfg.tester_prompt_template is set AND sprint_branch != develop,
        the dispatched tester prompt must still contain COMMANDER_MERGE_TARGET and --target-branch.
        """
        sm = _import_sprint_manager()
        cfg = self._make_cfg(sm, "Coder: {issue_url}")
        cfg.tester_prompt_template = "Tester: custom {issue_url}"

        captured_cmds = []

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            raise FileNotFoundError("claude not found")

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            sm._dispatch_tester(
                issue_num=72,
                alert_modes=[],
                sprint_branch="sprint/sprint-5",
                cfg=cfg,
            )

        assert captured_cmds, "subprocess.Popen must have been called"
        prompt_arg = captured_cmds[0][-1]
        assert "COMMANDER_MERGE_TARGET" in prompt_arg, (
            "Sprint-mode hint with COMMANDER_MERGE_TARGET must be appended to tester prompt "
            "even when cfg.tester_prompt_template is set (issue #72 regression)"
        )
        assert "--target-branch" in prompt_arg, (
            "--target-branch instruction must appear in tester prompt "
            "even when cfg.tester_prompt_template is set (issue #72 regression)"
        )
        assert "Tester: custom" in prompt_arg, \
            "The custom tester template body must be preserved in the final prompt"

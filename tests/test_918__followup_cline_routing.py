"""Tests for issue #918: Route follow-up coder dispatches to Cline on sprint opt-in.

AC1 — Reviewer agent applies `follow-up` label when filing follow-up (label in reviewer prompt)
AC2 — Router selects `cline` iff role==coder AND ticket has `follow-up` label AND
       sprint.use_cline_followups == True
AC3 — All other coder dispatches (non-follow-up, or opted-out sprint) use `claude-code`
AC4 — Tester dispatch is never routed to Cline regardless of ticket type or sprint flag
AC5 — If .clinerules absent or Cline backend unavailable, router falls back to
       `claude-code` and logs a warning — sprint never blocked
AC6 — A follow-up ticket in a non-opted-in sprint dispatches coder via Claude Code
"""
from __future__ import annotations

import textwrap
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(
    tmp_path: Path,
    backend: str = "claude-code",
    use_cline_followups: bool = False,
) -> MagicMock:
    """Minimal SprintConfig stub for dispatch tests."""
    (tmp_path / "PRODUCT.md").write_text("# Product")
    (tmp_path / "DESIGN.md").write_text("# Design")
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock()
    cfg.coder_backend = backend
    cfg.use_cline_followups = use_cline_followups
    cfg.coder_model = "claude-sonnet-4-6"
    cfg.coder_by_size = {
        "S": "claude-haiku-4-5",
        "M": "claude-sonnet-4-6",
        "L": "claude-sonnet-4-6",
        "XL": "claude-sonnet-4-6",
    }
    cfg.coder_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_coder = tmp_path
    cfg.logs_dir = logs
    return cfg


def _run_dispatch(
    cfg: MagicMock,
    issue_num: int = 1,
    sprint_branch: str = "develop",
    issue_labels: set | None = None,
) -> tuple[bool, object, list[str], dict]:
    """Run _dispatch_coder with all I/O mocked. Returns (ok, category, cmd, env)."""
    captured_cmd: list[str] = []
    captured_env: list[dict] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        captured_env.append(dict(kwargs.get("env") or {}))
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    _labels = issue_labels if issue_labels is not None else set()

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_dispatch_doctor", return_value=None),
        patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
        patch.object(sm, "_crg_update_worktree"),
        patch.object(sm, "_load_agent_persona", return_value=None),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_build_failure_suffix", return_value=""),
        patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        patch.object(sm, "_get_issue_labels", return_value=_labels),
    ):
        ok, cat = sm._dispatch_coder(
            issue_num, [], sprint_branch=sprint_branch, cfg=cfg,
        )

    return ok, cat, captured_cmd, captured_env[0] if captured_env else {}


def _run_tester_dispatch(
    cfg: MagicMock,
    issue_num: int = 1,
) -> tuple[int, object, list[str]]:
    """Run _dispatch_tester with all I/O mocked. Returns (rc, category, claude_cmd).

    Returns only the elements of the LAST Popen call (the actual agent dispatch),
    skipping any earlier Popen calls from internal availability probes.
    """
    all_calls: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        all_calls.append(list(cmd))
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
        patch.object(sm, "_crg_update_worktree"),
        patch.object(sm, "_load_agent_persona", return_value=None),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_build_failure_suffix", return_value=""),
        patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        patch.object(sm, "_resolve_uat_env_for_tester", return_value=(None, None)),
        patch.object(sm, "_get_issue_labels", return_value=set()),
        patch.object(sm.agent_browser_runner, "is_available", return_value=False),
    ):
        rc, cat = sm._dispatch_tester(issue_num, [], cfg=cfg)

    # Return the last captured command — that's the real agent dispatch (claude/cline)
    last_cmd = all_calls[-1] if all_calls else []
    return rc, cat, last_cmd


# ---------------------------------------------------------------------------
# AC1 — Reviewer prompt includes `follow-up` in label vocabulary
# ---------------------------------------------------------------------------

class TestAC1ReviewerFollowUpLabel:
    def test_reviewer_prompt_contains_follow_up_label(self):
        """DEFAULT_REVIEWER_PROMPT must instruct applying `follow-up` label on follow-up tickets."""
        prompt = sm.DEFAULT_REVIEWER_PROMPT
        assert "follow-up" in prompt, (
            "DEFAULT_REVIEWER_PROMPT must include 'follow-up' in label instructions"
        )

    def test_reviewer_prompt_lists_follow_up_as_required_label(self):
        """DEFAULT_REVIEWER_PROMPT must explicitly list `follow-up` as a required label to always apply."""
        prompt = sm.DEFAULT_REVIEWER_PROMPT
        assert "Always apply" in prompt and "follow-up" in prompt, (
            "DEFAULT_REVIEWER_PROMPT must have 'Always apply' instruction that includes 'follow-up'"
        )


# ---------------------------------------------------------------------------
# AC2 — Router selects `cline` when all three conditions met
# ---------------------------------------------------------------------------

class TestAC2ClineRoutingTripleCondition:
    def test_cline_selected_when_all_conditions_met(self, tmp_path):
        """Backend must be `cline` when: role=coder, ticket has follow-up label,
        use_cline_followups=True, .clinerules present."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        (tmp_path / ".clinerules").write_text("# Cline rules")
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up", "enhancement"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "cline", (
            f"Must use cline backend when all 3 conditions are met, got {cmd[0]!r}"
        )

    def test_select_coder_backend_function_exists(self):
        """_select_coder_backend function must exist in sprint_manager."""
        assert hasattr(sm, "_select_coder_backend"), (
            "_select_coder_backend function must exist in sprint_manager"
        )

    def test_select_coder_backend_returns_cline_for_followup(self, tmp_path):
        """_select_coder_backend must return 'cline' for follow-up ticket in opted-in sprint
        when .clinerules is present."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        (tmp_path / ".clinerules").write_text("# Cline rules")
        with patch.object(sm, "_get_issue_labels", return_value={"follow-up", "enhancement"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "cline", (
            f"_select_coder_backend must return 'cline' for follow-up in opted-in sprint, "
            f"got {backend!r}"
        )

    def test_sprint_config_has_use_cline_followups_field(self):
        """SprintConfig must have use_cline_followups attribute."""
        cfg = sm.SprintConfig()
        assert hasattr(cfg, "use_cline_followups"), (
            "SprintConfig must have use_cline_followups field"
        )

    def test_sprint_config_use_cline_followups_defaults_false(self):
        """SprintConfig.use_cline_followups must default to False."""
        cfg = sm.SprintConfig()
        assert cfg.use_cline_followups is False, (
            f"use_cline_followups must default to False, got {cfg.use_cline_followups!r}"
        )

    def test_load_config_parses_use_cline_followups_true(self, tmp_path):
        """load_config must parse agent_config.use_cline_followups from sprint.yaml."""
        yaml_text = textwrap.dedent("""
            repo_name: test/repo
            worktrees:
              coder: {coder}
              tester: {tester}
            agent_config:
              use_cline_followups: true
        """).format(coder=str(tmp_path / "coder"), tester=str(tmp_path / "tester"))

        for subdir in ("coder", "tester"):
            (tmp_path / subdir).mkdir(parents=True, exist_ok=True)

        yaml_path = tmp_path / "sprint.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        cfg = sm.load_config(yaml_path)
        assert cfg.use_cline_followups is True, (
            f"load_config must parse use_cline_followups=true, got {cfg.use_cline_followups!r}"
        )

    def test_load_config_defaults_use_cline_followups_false_when_absent(self, tmp_path):
        """load_config must default use_cline_followups to False when not in YAML."""
        yaml_text = textwrap.dedent("""
            repo_name: test/repo
            worktrees:
              coder: {coder}
              tester: {tester}
        """).format(coder=str(tmp_path / "coder"), tester=str(tmp_path / "tester"))

        for subdir in ("coder", "tester"):
            (tmp_path / subdir).mkdir(parents=True, exist_ok=True)

        yaml_path = tmp_path / "sprint.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        cfg = sm.load_config(yaml_path)
        assert cfg.use_cline_followups is False, (
            f"use_cline_followups must default to False when absent, got {cfg.use_cline_followups!r}"
        )


# ---------------------------------------------------------------------------
# AC3 — Non-follow-up or opted-out sprint uses claude-code
# ---------------------------------------------------------------------------

class TestAC3NonFollowUpUsesClaudeCode:
    def test_normal_ticket_uses_claude_code_even_when_sprint_opted_in(self, tmp_path):
        """Non-follow-up ticket in opted-in sprint must still use claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"enhancement", "backlog"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Non-follow-up ticket must use claude (not cline), got {cmd[0]!r}"
        )

    def test_follow_up_in_opted_out_sprint_uses_claude_code(self, tmp_path):
        """Follow-up ticket in non-opted-in sprint must use claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=False)
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up", "enhancement"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Follow-up in non-opted-in sprint must use claude-code, got {cmd[0]!r}"
        )

    def test_select_coder_backend_returns_claude_code_for_normal_ticket(self, tmp_path):
        """_select_coder_backend must return cfg.coder_backend for non-follow-up tickets."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        with patch.object(sm, "_get_issue_labels", return_value={"enhancement"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "claude-code", (
            f"Normal ticket must keep cfg.coder_backend='claude-code', got {backend!r}"
        )

    def test_select_coder_backend_returns_claude_code_when_sprint_not_opted_in(self, tmp_path):
        """_select_coder_backend must return 'claude-code' when use_cline_followups is False."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=False)
        with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "claude-code", (
            f"Follow-up with use_cline_followups=False must return 'claude-code', got {backend!r}"
        )


# ---------------------------------------------------------------------------
# AC4 — Tester dispatch is never routed to Cline
# ---------------------------------------------------------------------------

class TestAC4TesterNeverCline:
    def test_tester_dispatch_uses_claude_not_cline(self, tmp_path):
        """_dispatch_tester must always use 'claude' binary, never 'cline'."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        logs = tmp_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)

        cfg = MagicMock()
        cfg.coder_backend = "cline"
        cfg.use_cline_followups = True
        cfg.tester_model = "claude-haiku-4-5"
        cfg.tester_by_risk = {"LOW": "claude-haiku-4-5", "MEDIUM": "claude-haiku-4-5", "HIGH": "claude-sonnet-4-6"}
        cfg.tester_prompt_template = None
        cfg.repo_name = "test/repo"
        cfg.api_url = None
        cfg.worktree_tester_app = tmp_path
        cfg.worktree_tester = tmp_path
        cfg.logs_dir = logs

        _, _, cmd = _run_tester_dispatch(cfg, issue_num=1)
        assert cmd, "subprocess.Popen must have been called for tester"
        assert cmd[0] != "cline", (
            f"Tester must never use 'cline' binary, got {cmd[0]!r}"
        )
        assert cmd[0] == "claude", (
            f"Tester must always use 'claude' binary, got {cmd[0]!r}"
        )

    def test_tester_not_affected_by_use_cline_followups_flag(self, tmp_path):
        """use_cline_followups=True in sprint config must not affect tester dispatch."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        logs = tmp_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)

        cfg = MagicMock()
        cfg.use_cline_followups = True
        cfg.tester_model = "claude-haiku-4-5"
        cfg.tester_by_risk = {"LOW": "claude-haiku-4-5", "MEDIUM": "claude-haiku-4-5", "HIGH": "claude-sonnet-4-6"}
        cfg.tester_prompt_template = None
        cfg.repo_name = "test/repo"
        cfg.api_url = None
        cfg.worktree_tester_app = tmp_path
        cfg.worktree_tester = tmp_path
        cfg.logs_dir = logs

        _, _, cmd = _run_tester_dispatch(cfg, issue_num=1)
        assert cmd, "subprocess.Popen must have been called for tester"
        assert cmd[0] == "claude", (
            f"Tester must use claude even when use_cline_followups=True, got {cmd[0]!r}"
        )


# ---------------------------------------------------------------------------
# AC5 — Fallback to claude-code when .clinerules absent or Cline unavailable
# ---------------------------------------------------------------------------

class TestAC5FallbackOnMissingClinerules:
    def test_fallback_to_claude_code_when_clinerules_absent(self, tmp_path):
        """When .clinerules is absent and use_cline_followups=True, fall back to claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        # No .clinerules in tmp_path
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up", "enhancement"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Must fall back to claude-code when .clinerules is absent, got {cmd[0]!r}"
        )

    def test_warning_logged_when_fallback_occurs(self, tmp_path):
        """When falling back from cline to claude-code, a warning must be logged."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        # No .clinerules in tmp_path

        warn_calls: list = []

        def fake_warn(event, msg, **kwargs):
            warn_calls.append((event, msg))

        with (
            patch("subprocess.Popen") as mock_popen,
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_crg_update_worktree"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_load_estimate", return_value=None),
            patch.object(sm, "_build_failure_suffix", return_value=""),
            patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
            patch.object(sm, "_get_issue_labels", return_value={"follow-up"}),
            patch.object(sm.structured_log, "warn", side_effect=fake_warn),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        cline_warns = [
            (ev, msg) for ev, msg in warn_calls
            if "cline" in ev.lower() or "cline" in msg.lower()
            or "clinerules" in ev.lower() or "clinerules" in msg.lower()
        ]
        assert cline_warns, (
            "A warning must be logged when falling back from cline to claude-code "
            "due to missing .clinerules"
        )

    def test_sprint_continues_after_fallback(self, tmp_path):
        """Sprint must not be blocked when .clinerules is absent and fallback occurs."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        ok, cat, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up", "enhancement"})
        assert ok is True, (
            "Sprint must continue (ok=True) when falling back to claude-code"
        )
        assert cat is None, (
            "Fallback must not produce a failure category"
        )

    def test_cline_used_when_clinerules_present(self, tmp_path):
        """When .clinerules IS present, follow-up in opted-in sprint uses cline."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        (tmp_path / ".clinerules").write_text("# Cline rules")

        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up", "enhancement"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "cline", (
            f"Must use cline when .clinerules is present and all conditions met, got {cmd[0]!r}"
        )

    def test_select_coder_backend_falls_back_when_no_clinerules(self, tmp_path):
        """_select_coder_backend must return 'claude-code' when .clinerules absent."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "claude-code", (
            f"Must fall back to 'claude-code' when .clinerules is absent, got {backend!r}"
        )

    def test_select_coder_backend_returns_cline_when_clinerules_present(self, tmp_path):
        """_select_coder_backend must return 'cline' when .clinerules is present."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=True)
        (tmp_path / ".clinerules").write_text("# Cline rules")
        with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "cline", (
            f"Must return 'cline' when .clinerules is present and conditions met, got {backend!r}"
        )


# ---------------------------------------------------------------------------
# AC6 — Follow-up in non-opted-in sprint → claude-code
# ---------------------------------------------------------------------------

class TestAC6FollowUpInNonOptedInSprint:
    def test_follow_up_no_flag_uses_claude_code(self, tmp_path):
        """Follow-up ticket in sprint without use_cline_followups flag uses claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=False)
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"follow-up"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Follow-up in non-opted-in sprint must use claude, got {cmd[0]!r}"
        )

    def test_follow_up_with_flag_false_uses_claude_code(self, tmp_path):
        """use_cline_followups=False explicit must still dispatch follow-up via claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=False)
        with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
            backend = sm._select_coder_backend(1, cfg, "test/repo")
        assert backend == "claude-code", (
            f"Explicit use_cline_followups=False must return 'claude-code', got {backend!r}"
        )

    def test_load_config_false_use_cline_followups(self, tmp_path):
        """load_config parses explicit use_cline_followups: false correctly."""
        yaml_text = textwrap.dedent("""
            repo_name: test/repo
            worktrees:
              coder: {coder}
              tester: {tester}
            agent_config:
              use_cline_followups: false
        """).format(coder=str(tmp_path / "coder"), tester=str(tmp_path / "tester"))

        for subdir in ("coder", "tester"):
            (tmp_path / subdir).mkdir(parents=True, exist_ok=True)

        yaml_path = tmp_path / "sprint.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        cfg = sm.load_config(yaml_path)
        assert cfg.use_cline_followups is False, (
            f"Explicit use_cline_followups=false must load as False, got {cfg.use_cline_followups!r}"
        )


# ---------------------------------------------------------------------------
# Regression guard — existing coder routing is unchanged
# ---------------------------------------------------------------------------

class TestRegressionExistingCoderRouting:
    def test_explicit_cline_backend_still_works_without_followup_label(self, tmp_path):
        """Explicit coder_backend='cline' in sprint.yaml still routes to Cline
        (the old opt-in path from issue #917 remains intact)."""
        cfg = _make_cfg(tmp_path, backend="cline", use_cline_followups=False)
        (tmp_path / ".clinerules").write_text("# Cline rules")
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels={"enhancement"})
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "cline", (
            "Explicit coder_backend='cline' must still use Cline regardless of labels"
        )

    def test_default_config_uses_claude_code(self, tmp_path):
        """Default SprintConfig (no overrides) must use claude-code."""
        cfg = _make_cfg(tmp_path, backend="claude-code", use_cline_followups=False)
        _, _, cmd, _ = _run_dispatch(cfg, issue_labels=set())
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Default config must use claude, got {cmd[0]!r}"
        )

"""Tests for issue #917: Add Cline headless backend to coder dispatch.

AC1 — Dispatch layer has `backend` config value; default is `claude-code`
AC2 — When backend=cline, _dispatch_coder builds correct Cline headless CLI command
AC3 — Cline invocation uses correct prompt/input format Cline expects
AC4 — Cline runs inside same worktree the Claude coder would use
AC5 — Anthropic API key passed to Cline via its documented env var (ANTHROPIC_API_KEY)
AC6 — Cline exit/output normalized to same success/failure signal the existing path returns
AC7 — Quality gates, structured-failure sidecar, finish_feature.py operate identically
AC8 — Feature off by default; no ticket routes to Cline without explicit backend=cline opt-in
AC9 — .clinerules ticket has merged before this ships
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, backend: str = "claude-code") -> MagicMock:
    """Minimal SprintConfig stub for dispatch tests."""
    (tmp_path / "PRODUCT.md").write_text("# Product")
    (tmp_path / "DESIGN.md").write_text("# Design")
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock()
    cfg.coder_backend = backend
    cfg.use_cline_followups = False  # issue #918: disable follow-up routing by default
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
    env_override: dict | None = None,
    persona: str | None = None,
) -> tuple[bool, object, list[str], dict]:
    """
    Run _dispatch_coder with all I/O mocked.
    Returns (ok, category, captured_cmd, captured_env).
    """
    captured_cmd: list[str] = []
    captured_env: list[dict] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        captured_env.append(dict(kwargs.get("env") or {}))
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    base_env = {"SOME_VAR": "1"}
    if env_override:
        base_env.update(env_override)

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_dispatch_doctor", return_value=None),
        patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
        patch.object(sm, "_crg_update_worktree"),
        patch.object(sm, "_load_agent_persona", return_value=persona),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_build_failure_suffix", return_value=""),
        patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        mock.patch.dict("os.environ", base_env, clear=True),
    ):
        ok, cat = sm._dispatch_coder(
            issue_num, [], sprint_branch=sprint_branch, cfg=cfg,
        )

    return ok, cat, captured_cmd, captured_env[0] if captured_env else {}


# ---------------------------------------------------------------------------
# AC1 — backend config value; default is claude-code
# ---------------------------------------------------------------------------

class TestAC1BackendConfigDefault:
    def test_sprint_config_has_coder_backend_field(self):
        """SprintConfig must have coder_backend attribute."""
        cfg = sm.SprintConfig()
        assert hasattr(cfg, "coder_backend"), (
            "SprintConfig must have coder_backend field"
        )

    def test_sprint_config_coder_backend_defaults_to_claude_code(self):
        """SprintConfig.coder_backend must default to 'claude-code'."""
        cfg = sm.SprintConfig()
        assert cfg.coder_backend == "claude-code", (
            f"Default coder_backend must be 'claude-code', got {cfg.coder_backend!r}"
        )

    def test_load_config_parses_coder_backend_from_yaml(self, tmp_path):
        """load_config must parse agent_config.coder.backend from sprint.yaml."""
        yaml_text = textwrap.dedent("""
            repo_name: test/repo
            worktrees:
              coder: {coder}
              tester: {tester}
            agent_config:
              coder:
                backend: cline
        """).format(coder=str(tmp_path / "coder"), tester=str(tmp_path / "tester"))

        for subdir in ("coder", "tester"):
            (tmp_path / subdir).mkdir(parents=True, exist_ok=True)

        yaml_path = tmp_path / "sprint.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        cfg = sm.load_config(yaml_path)
        assert cfg.coder_backend == "cline", (
            f"load_config must parse coder.backend='cline', got {cfg.coder_backend!r}"
        )

    def test_load_config_defaults_to_claude_code_when_backend_absent(self, tmp_path):
        """load_config must default coder_backend to 'claude-code' when not in YAML."""
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
        assert cfg.coder_backend == "claude-code", (
            f"coder_backend must default to 'claude-code', got {cfg.coder_backend!r}"
        )


# ---------------------------------------------------------------------------
# AC2 — Cline CLI command structure
# ---------------------------------------------------------------------------

class TestAC2ClineCLICommand:
    def test_cline_backend_uses_cline_binary(self, tmp_path):
        """When backend=cline, first element of subprocess cmd must be 'cline'."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "cline", (
            f"Cline backend must use 'cline' binary, got {cmd[0]!r}"
        )

    def test_cline_backend_uses_yolo_flag(self, tmp_path):
        """-y (yolo/auto-approve) must appear in the Cline command."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert "-y" in cmd, (
            f"Cline cmd must include -y (auto-approve), got {cmd!r}"
        )

    def test_cline_backend_does_not_use_dangerously_skip_permissions(self, tmp_path):
        """--dangerously-skip-permissions is a Claude Code flag; must NOT appear for Cline."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert "--dangerously-skip-permissions" not in cmd, (
            "--dangerously-skip-permissions is Claude-Code-specific; must not be in Cline cmd"
        )

    def test_cline_backend_uses_model_flag(self, tmp_path):
        """Cline cmd must include -m <model>."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert "-m" in cmd, f"Cline cmd must include -m flag, got {cmd!r}"
        m_idx = cmd.index("-m")
        assert m_idx + 1 < len(cmd), "-m must be followed by model name"
        assert "sonnet" in cmd[m_idx + 1] or "haiku" in cmd[m_idx + 1], (
            f"Model name must follow -m, got {cmd[m_idx + 1]!r}"
        )

    def test_cline_backend_does_not_use_p_flag(self, tmp_path):
        """-p is a Claude Code flag; Cline takes prompt as a positional argument."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert "-p" not in cmd, (
            f"-p is Claude-Code-specific; Cline uses a positional prompt arg. Got {cmd!r}"
        )

    def test_cline_backend_does_not_use_append_system_prompt(self, tmp_path):
        """--append-system-prompt is Claude Code only; must NOT appear in Cline cmd."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg, persona="some persona text")
        assert "--append-system-prompt" not in cmd, (
            "--append-system-prompt is Claude-Code-only; persona must be prepended to Cline prompt"
        )


# ---------------------------------------------------------------------------
# AC3 — Cline prompt/input format
# ---------------------------------------------------------------------------

class TestAC3ClinePromptFormat:
    def test_cline_prompt_is_positional_arg(self, tmp_path):
        """Cline prompt must be passed as a positional argument (not via -p)."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg)
        # After "cline -y -m <model>", the next element is the prompt
        assert len(cmd) >= 4, f"Cline cmd must have at least 4 elements, got {cmd!r}"
        # Verify no -p flag
        assert "-p" not in cmd, "Cline uses positional prompt, not -p flag"
        # The last argument should be the prompt string (non-flag)
        last_arg = cmd[-1]
        assert not last_arg.startswith("-"), (
            f"Last arg must be the prompt (no leading dash), got {last_arg!r}"
        )

    def test_cline_persona_prepended_to_prompt(self, tmp_path):
        """When backend=cline, persona must be prepended to the prompt string."""
        cfg = _make_cfg(tmp_path, backend="cline")
        persona_text = "YOU ARE THE CODER PERSONA"
        _, _, cmd, _ = _run_dispatch(cfg, persona=persona_text)
        # Find the prompt in cmd (last positional arg after -m <model>)
        full_prompt = cmd[-1]
        assert persona_text in full_prompt, (
            f"Cline prompt must include the persona (prepended). "
            f"Got prompt starting with: {full_prompt[:80]!r}"
        )

    def test_cline_prompt_contains_issue_reference(self, tmp_path):
        """The Cline prompt must contain a reference to the GitHub issue."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _ = _run_dispatch(cfg, issue_num=917)
        full_prompt = cmd[-1]
        assert "917" in full_prompt or "github.com" in full_prompt, (
            f"Prompt must reference the issue. Got: {full_prompt[:80]!r}"
        )


# ---------------------------------------------------------------------------
# AC4 — Cline runs in same worktree
# ---------------------------------------------------------------------------

class TestAC4SameWorktree:
    def test_cline_cwd_is_worktree_coder(self, tmp_path):
        """Cline subprocess must run with cwd=worktree_coder."""
        cfg = _make_cfg(tmp_path, backend="cline")

        captured_kwargs: list[dict] = []

        def fake_popen(cmd, **kwargs):
            captured_kwargs.append(kwargs)
            proc = MagicMock()
            proc.wait.return_value = 0
            return proc

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
        ):
            sm._dispatch_coder(1, [], sprint_branch="develop", cfg=cfg)

        assert captured_kwargs, "subprocess.Popen must have been called"
        cwd = captured_kwargs[0].get("cwd")
        assert str(cwd) == str(tmp_path), (
            f"Cline subprocess cwd must be worktree_coder ({tmp_path}), got {cwd!r}"
        )

    def test_claude_code_cwd_matches_cline_cwd(self, tmp_path):
        """Both backends must use worktree_coder as subprocess cwd."""
        captured_cwds: dict[str, str] = {}

        def _make_popen(backend_name):
            def _popen(cmd, **kwargs):
                captured_cwds[backend_name] = kwargs.get("cwd", "")
                proc = MagicMock()
                proc.wait.return_value = 0
                return proc
            return _popen

        for backend in ("claude-code", "cline"):
            cfg = _make_cfg(tmp_path, backend=backend)
            with (
                patch("subprocess.Popen", side_effect=_make_popen(backend)),
                patch.object(sm, "_post_agent_event"),
                patch.object(sm, "_dispatch_doctor", return_value=None),
                patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
                patch.object(sm, "_crg_update_worktree"),
                patch.object(sm, "_load_agent_persona", return_value=None),
                patch.object(sm, "_load_estimate", return_value=None),
                patch.object(sm, "_build_failure_suffix", return_value=""),
                patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
            ):
                sm._dispatch_coder(1, [], cfg=cfg)

        assert captured_cwds.get("cline") == captured_cwds.get("claude-code"), (
            "Both backends must use the same cwd (worktree_coder)"
        )


# ---------------------------------------------------------------------------
# AC5 — Anthropic API key env var
# ---------------------------------------------------------------------------

class TestAC5AnthropicAPIKey:
    def test_cline_backend_keeps_anthropic_api_key(self, tmp_path):
        """When backend=cline, ANTHROPIC_API_KEY must be present in subprocess env."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, _, env = _run_dispatch(
            cfg, env_override={"ANTHROPIC_API_KEY": "sk-test-key"}
        )
        assert "ANTHROPIC_API_KEY" in env, (
            "Cline backend must pass ANTHROPIC_API_KEY to subprocess (metered API path)"
        )
        assert env["ANTHROPIC_API_KEY"] == "sk-test-key", (
            "ANTHROPIC_API_KEY value must be preserved for Cline"
        )

    def test_claude_code_backend_strips_anthropic_api_key(self, tmp_path):
        """When backend=claude-code, ANTHROPIC_API_KEY must NOT be in subprocess env."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, _, env = _run_dispatch(
            cfg, env_override={"ANTHROPIC_API_KEY": "sk-test-key"}
        )
        assert "ANTHROPIC_API_KEY" not in env, (
            "Claude Code backend must strip ANTHROPIC_API_KEY (uses subscription auth)"
        )


# ---------------------------------------------------------------------------
# AC6 — Normalized exit/failure signals
# ---------------------------------------------------------------------------

class TestAC6NormalizedExitSignals:
    def _run_with_exit_code(self, tmp_path: Path, backend: str, exit_code: int):
        cfg = _make_cfg(tmp_path, backend=backend)

        def fake_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.wait.return_value = exit_code
            return proc

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
            patch.object(sm, "record_failure"),
            patch.object(sm, "dispatch_alerts"),
        ):
            return sm._dispatch_coder(1, [], cfg=cfg)

    def test_cline_exit_0_returns_true(self, tmp_path):
        """Cline exit 0 must return (True, None) — same as Claude Code."""
        ok, cat = self._run_with_exit_code(tmp_path, "cline", 0)
        assert ok is True, "Exit 0 must return ok=True for Cline"
        assert cat is None, "Exit 0 must return category=None for Cline"

    def test_cline_nonzero_exit_returns_false(self, tmp_path):
        """Cline non-zero exit must return (False, failure_category) — same as Claude Code."""
        ok, cat = self._run_with_exit_code(tmp_path, "cline", 1)
        assert ok is False, "Non-zero exit must return ok=False for Cline"
        assert cat is not None, "Non-zero exit must return a failure category for Cline"

    def test_claude_code_exit_0_returns_true(self, tmp_path):
        """Claude Code exit 0 must still return (True, None) — existing behavior unchanged."""
        ok, cat = self._run_with_exit_code(tmp_path, "claude-code", 0)
        assert ok is True, "Exit 0 must return ok=True for Claude Code"
        assert cat is None, "Exit 0 must return category=None for Claude Code"

    def test_cline_and_claude_code_have_same_ok_on_success(self, tmp_path):
        """Both backends must return identical (ok, category) on exit 0."""
        cline_result = self._run_with_exit_code(tmp_path, "cline", 0)
        claude_result = self._run_with_exit_code(tmp_path, "claude-code", 0)
        assert cline_result == claude_result, (
            f"Exit 0 must yield same result for both backends. "
            f"Cline: {cline_result}, Claude Code: {claude_result}"
        )


# ---------------------------------------------------------------------------
# AC7 — Quality gates operate identically regardless of backend
# ---------------------------------------------------------------------------

class TestAC7QualityGatesIdentical:
    def test_dispatch_doctor_called_for_cline_backend(self, tmp_path):
        """_dispatch_doctor must be called when backend=cline."""
        cfg = _make_cfg(tmp_path, backend="cline")
        doctor_calls: list = []

        def fake_doctor(*args, **kwargs):
            doctor_calls.append((args, kwargs))
            return None  # healthy

        with (
            patch("subprocess.Popen") as mock_popen,
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_dispatch_doctor", side_effect=fake_doctor),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_crg_update_worktree"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_load_estimate", return_value=None),
            patch.object(sm, "_build_failure_suffix", return_value=""),
            patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        assert doctor_calls, "_dispatch_doctor must be called for Cline backend"

    def test_worktree_hygiene_called_for_cline_backend(self, tmp_path):
        """_worktree_hygiene must be called when backend=cline."""
        cfg = _make_cfg(tmp_path, backend="cline")
        hygiene_calls: list = []

        def fake_hygiene(*args, **kwargs):
            hygiene_calls.append((args, kwargs))
            return (None, None, None)

        with (
            patch("subprocess.Popen") as mock_popen,
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_worktree_hygiene", side_effect=fake_hygiene),
            patch.object(sm, "_crg_update_worktree"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_load_estimate", return_value=None),
            patch.object(sm, "_build_failure_suffix", return_value=""),
            patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        assert hygiene_calls, "_worktree_hygiene must be called for Cline backend"

    def test_dispatch_doctor_called_for_claude_code_backend(self, tmp_path):
        """_dispatch_doctor must also be called for claude-code (regression guard)."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        doctor_calls: list = []

        def fake_doctor(*args, **kwargs):
            doctor_calls.append((args, kwargs))
            return None

        with (
            patch("subprocess.Popen") as mock_popen,
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_dispatch_doctor", side_effect=fake_doctor),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_crg_update_worktree"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_load_estimate", return_value=None),
            patch.object(sm, "_build_failure_suffix", return_value=""),
            patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        assert doctor_calls, "_dispatch_doctor must still be called for claude-code backend"


# ---------------------------------------------------------------------------
# AC8 — Feature off by default
# ---------------------------------------------------------------------------

class TestAC8OffByDefault:
    def test_default_backend_uses_claude_binary(self, tmp_path):
        """Without explicit backend config, subprocess cmd must start with 'claude'."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _ = _run_dispatch(cfg)
        assert cmd, "subprocess.Popen must have been called"
        assert cmd[0] == "claude", (
            f"Default backend must use 'claude' binary, got {cmd[0]!r}"
        )

    def test_no_cfg_uses_claude_code_path(self, tmp_path):
        """When cfg=None (no config), dispatch must use Claude Code (existing default)."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")

        captured_cmd: list[str] = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            proc = MagicMock()
            proc.wait.return_value = 0
            return proc

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
            patch.object(sm, "_design_docs_guard", return_value=None),
        ):
            sm._dispatch_coder(1, [], cfg=None)

        if captured_cmd:
            assert captured_cmd[0] == "claude", (
                f"cfg=None must default to Claude Code, got {captured_cmd[0]!r}"
            )

    def test_sprint_config_default_does_not_route_to_cline(self):
        """SprintConfig() with no overrides must not select Cline backend."""
        cfg = sm.SprintConfig()
        assert cfg.coder_backend != "cline", (
            "Default SprintConfig must not select Cline backend automatically"
        )


# ---------------------------------------------------------------------------
# AC9 — .clinerules ticket has merged before this ships
# ---------------------------------------------------------------------------

class TestAC9ClinerulesPrerequisite:
    def test_clinerules_exists_in_repo_root(self):
        """.clinerules file must exist at repo root (from ticket #916).

        This test will be red until issue #916 (port Commander invariants into
        .clinerules) merges into the sprint branch. That prerequisite must land
        before this ticket ships.
        """
        clinerules = REPO_ROOT / ".clinerules"
        assert clinerules.exists(), (
            ".clinerules must exist at repo root before the Cline backend ships. "
            "Issue #916 (port Commander invariants into .clinerules) must be merged first."
        )

    def test_cline_backend_warns_when_clinerules_missing(self, tmp_path):
        """When backend=cline and .clinerules absent, a warning must be emitted.

        .clinerules is loaded automatically by Cline. Without it, Cline won't
        inherit Commander workflow invariants (no-merge, no-label rules, etc.).
        """
        cfg = _make_cfg(tmp_path, backend="cline")
        # tmp_path has no .clinerules — warning should be logged

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
            patch.object(sm.structured_log, "warn", side_effect=fake_warn),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        clinerules_warns = [
            (ev, msg) for ev, msg in warn_calls
            if "clinerules" in ev.lower() or "clinerules" in msg.lower()
        ]
        assert clinerules_warns, (
            "When backend=cline and .clinerules is absent, sprint_manager must emit "
            "a structured_log.warn about missing .clinerules"
        )

    def test_cline_backend_no_warn_when_clinerules_present(self, tmp_path):
        """When .clinerules exists, no clinerules warning should be emitted."""
        cfg = _make_cfg(tmp_path, backend="cline")
        (tmp_path / ".clinerules").write_text("# Cline rules")

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
            patch.object(sm.structured_log, "warn", side_effect=fake_warn),
        ):
            proc = MagicMock()
            proc.wait.return_value = 0
            mock_popen.return_value = proc
            sm._dispatch_coder(1, [], cfg=cfg)

        clinerules_warns = [
            (ev, msg) for ev, msg in warn_calls
            if "clinerules" in ev.lower() or "clinerules" in msg.lower()
        ]
        assert not clinerules_warns, (
            "When .clinerules exists, no clinerules warning must be emitted"
        )


# ---------------------------------------------------------------------------
# Doctor: backend-aware CLI check
# ---------------------------------------------------------------------------

class TestDoctorBackendAware:
    def test_doctor_checks_cline_when_backend_is_cline(self, tmp_path):
        """_dispatch_doctor must probe 'cline' binary (not 'claude') when backend=cline."""
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()

        cfg = MagicMock()
        cfg.worktree_coder = coder_dir
        cfg.coder_backend = "cline"

        checked_binaries: list[str] = []

        def fake_which(name):
            checked_binaries.append(name)
            return f"/usr/bin/{name}"  # pretend it's there

        with (
            patch("shutil.which", side_effect=fake_which),
            patch.object(sm, "_doctor_probe_auth", return_value=None),
            patch("shutil.disk_usage") as mock_du,
        ):
            mock_du.return_value = MagicMock(free=10 * 1024 ** 3)
            sm._dispatch_doctor(cfg, alert_modes=[])

        assert "cline" in checked_binaries, (
            f"_dispatch_doctor must check 'cline' binary for cline backend. "
            f"Checked: {checked_binaries}"
        )

    def test_doctor_checks_claude_when_backend_is_claude_code(self, tmp_path):
        """_dispatch_doctor must probe 'claude' binary (existing behavior) for claude-code."""
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()

        cfg = MagicMock()
        cfg.worktree_coder = coder_dir
        cfg.coder_backend = "claude-code"

        checked_binaries: list[str] = []

        def fake_which(name):
            checked_binaries.append(name)
            return f"/usr/bin/{name}"

        with (
            patch("shutil.which", side_effect=fake_which),
            patch.object(sm, "_doctor_probe_auth", return_value=None),
            patch("shutil.disk_usage") as mock_du,
        ):
            mock_du.return_value = MagicMock(free=10 * 1024 ** 3)
            sm._dispatch_doctor(cfg, alert_modes=[])

        assert "claude" in checked_binaries, (
            f"_dispatch_doctor must check 'claude' binary for claude-code backend. "
            f"Checked: {checked_binaries}"
        )

    def test_doctor_fails_when_cline_missing_for_cline_backend(self, tmp_path):
        """_dispatch_doctor must fail when 'cline' is not in PATH and backend=cline."""
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()

        cfg = MagicMock()
        cfg.worktree_coder = coder_dir
        cfg.coder_backend = "cline"

        with patch("shutil.which", return_value=None):
            result = sm._dispatch_doctor(cfg, alert_modes=[])

        assert result is not None, "Doctor must fail when cline CLI is missing"
        assert "cline" in result.lower(), (
            f"Error must mention 'cline', got: {result!r}"
        )

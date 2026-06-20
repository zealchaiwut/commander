"""Tests for issue #1402: Inject estimator target paths into coder dispatch prompts.

AC1 — files_likely_affected → Start here block prepended to coder -p prompt (Claude Code)
AC2 — files_touched preferred over files_likely_affected
AC3 — Exact phrasing: "Start here — do not broad-search the repo unless these paths are insufficient."
AC4 — No estimate file → prompt unchanged
AC5 — Coder persona text still appears after the injected paths block (Cline backend)
AC6 — Failure suffix still appends after persona (order: paths block → persona → failure suffix, Cline)
AC7 — Injection applied in both Claude Code and Cline backend dispatch paths
AC8 — Dispatch log explicitly shows injected paths block for estimated ticket
AC9 — Dispatch log shows no injected paths block for unestimated ticket
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm  # noqa: E402

_PATHS_HEADER = "Start here — do not broad-search the repo unless these paths are insufficient."

_SAMPLE_ESTIMATE_FLA = {
    "size": "M",
    "confidence": "high",
    "files_likely_affected": ["services/sprint_manager/sprint_manager.py", "tests/test_foo.py"],
}

_SAMPLE_ESTIMATE_FT = {
    "size": "M",
    "confidence": "high",
    "files_touched": ["apps/dashboard/server.py", "scripts/start_feature.py"],
    "files_likely_affected": ["services/sprint_manager/sprint_manager.py"],
}

_SAMPLE_ESTIMATE_EMPTY = {
    "size": "S",
    "confidence": "low",
    "files_likely_affected": [],
    "files_touched": [],
}


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
    cfg.use_cline_followups = False
    cfg.coder_model = "claude-sonnet-4-6"
    cfg.cline_model = None
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
    estimate: dict | None = None,
    persona: str | None = None,
    prior_failures: list | None = None,
    failure_suffix: str = "",
) -> tuple[bool, object, list[str], dict, str]:
    """
    Run _dispatch_coder with all I/O mocked.
    Returns (ok, category, captured_cmd, captured_env, captured_stdout).
    """
    captured_cmd: list[str] = []
    captured_env: list[dict] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        captured_env.append(dict(kwargs.get("env") or {}))
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    stdout_buf = io.StringIO()

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_dispatch_doctor", return_value=None),
        patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
        patch.object(sm, "_crg_update_worktree"),
        patch.object(sm, "_load_agent_persona", return_value=persona),
        patch.object(sm, "_load_estimate", return_value=estimate),
        patch.object(sm, "_build_failure_suffix", return_value=failure_suffix),
        patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        mock.patch.dict("os.environ", {"SOME_VAR": "1"}, clear=True),
        patch("sys.stdout", stdout_buf),
    ):
        ok, cat = sm._dispatch_coder(
            issue_num,
            [],
            sprint_branch=sprint_branch,
            cfg=cfg,
            prior_failures=prior_failures,
        )

    return ok, cat, captured_cmd, captured_env[0] if captured_env else {}, stdout_buf.getvalue()


# ---------------------------------------------------------------------------
# Direct unit tests of _build_estimate_paths_block
# ---------------------------------------------------------------------------

class TestBuildEstimatePathsBlockUnit:
    def test_none_estimate_returns_empty(self):
        assert sm._build_estimate_paths_block(None) == ""

    def test_empty_estimate_returns_empty(self):
        assert sm._build_estimate_paths_block({}) == ""

    def test_empty_files_likely_affected_returns_empty(self):
        assert sm._build_estimate_paths_block({"files_likely_affected": []}) == ""

    def test_files_likely_affected_builds_block(self):
        result = sm._build_estimate_paths_block(_SAMPLE_ESTIMATE_FLA)
        assert _PATHS_HEADER in result
        assert "services/sprint_manager/sprint_manager.py" in result
        assert "tests/test_foo.py" in result

    def test_files_touched_preferred_over_files_likely_affected(self):
        result = sm._build_estimate_paths_block(_SAMPLE_ESTIMATE_FT)
        assert "apps/dashboard/server.py" in result
        assert "scripts/start_feature.py" in result
        # files_likely_affected path NOT included when files_touched is present
        assert "services/sprint_manager/sprint_manager.py" not in result

    def test_exact_phrasing_in_header(self):
        result = sm._build_estimate_paths_block(_SAMPLE_ESTIMATE_FLA)
        assert result.startswith(_PATHS_HEADER), (
            f"Block must start with exact phrasing. Got: {result[:80]!r}"
        )


# ---------------------------------------------------------------------------
# AC1 — files_likely_affected prepended to Claude Code -p prompt
# ---------------------------------------------------------------------------

class TestAC1FilesLikelyAffectedInjectedClaudeCode:
    def test_paths_block_appears_in_p_prompt(self, tmp_path):
        """files_likely_affected paths are prepended to the -p prompt for Claude Code."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        # Find the -p arg value
        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert _PATHS_HEADER in p_value, (
            f"Claude Code -p prompt must contain the paths block header. Got start: {p_value[:120]!r}"
        )
        assert "services/sprint_manager/sprint_manager.py" in p_value
        assert "tests/test_foo.py" in p_value

    def test_paths_block_is_prepended_not_appended(self, tmp_path):
        """Paths block must appear at the start of the -p prompt, not at the end."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        header_pos = p_value.find(_PATHS_HEADER)
        read_issue_pos = p_value.find("Read the issue at")

        assert header_pos != -1, "Paths block header not found in -p prompt"
        assert read_issue_pos != -1, "Issue reference not found in -p prompt"
        assert header_pos < read_issue_pos, (
            "Paths block must appear BEFORE the main prompt body (prepended, not appended)"
        )


# ---------------------------------------------------------------------------
# AC2 — files_touched preferred over files_likely_affected
# ---------------------------------------------------------------------------

class TestAC2FilesTouchedPreferred:
    def test_files_touched_used_when_present(self, tmp_path):
        """When estimate has files_touched, those paths are used (not files_likely_affected)."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FT)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert "apps/dashboard/server.py" in p_value
        assert "scripts/start_feature.py" in p_value
        # files_likely_affected path must NOT appear
        assert "services/sprint_manager/sprint_manager.py" not in p_value, (
            "files_touched must take priority — files_likely_affected paths must NOT appear"
        )

    def test_files_likely_affected_used_when_files_touched_absent(self, tmp_path):
        """When estimate has only files_likely_affected (no files_touched), those paths are used."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        estimate_no_ft = {
            "size": "M",
            "files_likely_affected": ["mymodule/core.py"],
        }
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=estimate_no_ft)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert "mymodule/core.py" in p_value, (
            "When no files_touched, files_likely_affected paths must be injected"
        )

    def test_files_likely_affected_used_when_files_touched_empty(self, tmp_path):
        """When files_touched is empty list, fall back to files_likely_affected."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        estimate = {
            "size": "M",
            "files_touched": [],
            "files_likely_affected": ["fallback/path.py"],
        }
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=estimate)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert "fallback/path.py" in p_value, (
            "When files_touched is empty, files_likely_affected must be used"
        )


# ---------------------------------------------------------------------------
# AC3 — Exact phrasing in the paths block header
# ---------------------------------------------------------------------------

class TestAC3ExactPhrasing:
    def test_exact_phrasing_in_claude_code_prompt(self, tmp_path):
        """The injected block uses the exact required phrasing."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert _PATHS_HEADER in p_value, (
            f"Prompt must contain exact phrasing: {_PATHS_HEADER!r}"
        )

    def test_exact_phrasing_in_cline_prompt(self, tmp_path):
        """Cline backend also uses the exact required phrasing."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        full_prompt = cmd[-1]
        assert _PATHS_HEADER in full_prompt, (
            f"Cline prompt must contain exact phrasing: {_PATHS_HEADER!r}"
        )


# ---------------------------------------------------------------------------
# AC4 — No estimate file → prompt unchanged
# ---------------------------------------------------------------------------

class TestAC4NoEstimatePromptUnchanged:
    def test_no_paths_block_in_claude_code_prompt_when_no_estimate(self, tmp_path):
        """When no estimate exists, the Claude Code -p prompt has no Start here block."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=None)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert _PATHS_HEADER not in p_value, (
            "When no estimate file, the paths block must NOT appear in the -p prompt"
        )

    def test_no_paths_block_in_cline_prompt_when_no_estimate(self, tmp_path):
        """When no estimate exists, the Cline full_prompt has no Start here block."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=None)

        full_prompt = cmd[-1]
        assert _PATHS_HEADER not in full_prompt, (
            "When no estimate file, the paths block must NOT appear in the Cline prompt"
        )

    def test_no_paths_block_when_estimate_has_no_files(self, tmp_path):
        """When estimate exists but has no file paths, prompt is also unchanged."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_EMPTY)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        assert _PATHS_HEADER not in p_value, (
            "When estimate has no file paths, the paths block must NOT appear"
        )


# ---------------------------------------------------------------------------
# AC5 — Persona appears after the injected paths block (Cline)
# ---------------------------------------------------------------------------

class TestAC5PersonaAfterPathsBlock:
    def test_persona_after_paths_block_in_cline_prompt(self, tmp_path):
        """For Cline backend, persona text must appear AFTER the paths block."""
        cfg = _make_cfg(tmp_path, backend="cline")
        persona_text = "YOU ARE THE CODER PERSONA"
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA, persona=persona_text)

        full_prompt = cmd[-1]

        header_pos = full_prompt.find(_PATHS_HEADER)
        persona_pos = full_prompt.find(persona_text)

        assert header_pos != -1, "Paths block header not found in Cline prompt"
        assert persona_pos != -1, "Persona text not found in Cline prompt"
        assert header_pos < persona_pos, (
            "Paths block must appear BEFORE persona in the Cline prompt "
            f"(header@{header_pos}, persona@{persona_pos})"
        )

    def test_persona_still_present_with_paths_block(self, tmp_path):
        """Persona must not be dropped when paths block is injected (Cline)."""
        cfg = _make_cfg(tmp_path, backend="cline")
        persona_text = "CODER PERSONA TEXT HERE"
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA, persona=persona_text)

        full_prompt = cmd[-1]
        assert persona_text in full_prompt, (
            "Coder persona must still appear in the Cline prompt when paths block is injected"
        )


# ---------------------------------------------------------------------------
# AC6 — Failure suffix still appends last (Cline: paths → persona → suffix)
# ---------------------------------------------------------------------------

class TestAC6FailureSuffixOrder:
    def test_order_paths_persona_failure_in_cline_prompt(self, tmp_path):
        """For Cline: paths block → persona → failure suffix order must be preserved."""
        cfg = _make_cfg(tmp_path, backend="cline")
        persona_text = "CODER PERSONA"
        failure_str = "\n\nPrevious failure class: gate_failed."

        _, _, cmd, _, _ = _run_dispatch(
            cfg,
            estimate=_SAMPLE_ESTIMATE_FLA,
            persona=persona_text,
            failure_suffix=failure_str,
        )

        full_prompt = cmd[-1]

        header_pos = full_prompt.find(_PATHS_HEADER)
        persona_pos = full_prompt.find(persona_text)
        failure_pos = full_prompt.find("gate_failed")

        assert header_pos != -1, "Paths block not found in Cline prompt"
        assert persona_pos != -1, "Persona not found in Cline prompt"
        assert failure_pos != -1, "Failure suffix not found in Cline prompt"

        assert header_pos < persona_pos < failure_pos, (
            f"Order must be paths({header_pos}) → persona({persona_pos}) → failure({failure_pos})"
        )

    def test_failure_suffix_after_paths_in_claude_code_prompt(self, tmp_path):
        """For Claude Code: paths block appears before the failure suffix in the -p prompt."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        failure_str = "\n\nPrevious failure class: gate_failed."

        _, _, cmd, _, _ = _run_dispatch(
            cfg,
            estimate=_SAMPLE_ESTIMATE_FLA,
            failure_suffix=failure_str,
        )

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]

        header_pos = p_value.find(_PATHS_HEADER)
        failure_pos = p_value.find("gate_failed")

        assert header_pos != -1, "Paths block not found in -p prompt"
        assert failure_pos != -1, "Failure suffix not found in -p prompt"
        assert header_pos < failure_pos, (
            f"Paths block({header_pos}) must come before failure suffix({failure_pos})"
        )


# ---------------------------------------------------------------------------
# AC7 — Injection applied to both backends
# ---------------------------------------------------------------------------

class TestAC7BothBackends:
    def test_injection_in_claude_code_backend(self, tmp_path):
        """Claude Code backend receives the paths block."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        p_idx = cmd.index("-p")
        p_value = cmd[p_idx + 1]
        assert _PATHS_HEADER in p_value, "Claude Code backend must inject paths block"

    def test_injection_in_cline_backend(self, tmp_path):
        """Cline backend also receives the paths block."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        full_prompt = cmd[-1]
        assert _PATHS_HEADER in full_prompt, "Cline backend must inject paths block"

    def test_no_injection_in_claude_code_without_estimate(self, tmp_path):
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=None)
        p_idx = cmd.index("-p")
        assert _PATHS_HEADER not in cmd[p_idx + 1]

    def test_no_injection_in_cline_without_estimate(self, tmp_path):
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, cmd, _, _ = _run_dispatch(cfg, estimate=None)
        assert _PATHS_HEADER not in cmd[-1]


# ---------------------------------------------------------------------------
# AC8 — Dispatch log shows injected paths block for estimated ticket
# ---------------------------------------------------------------------------

class TestAC8DispatchLogShowsPathsBlock:
    def test_stdout_contains_paths_block_for_estimated_ticket(self, tmp_path):
        """Dispatch stdout must show the injected paths block for estimated tickets."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, _, _, stdout = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FLA)

        assert _PATHS_HEADER in stdout, (
            "Dispatch log must explicitly show the injected paths block header"
        )
        assert "services/sprint_manager/sprint_manager.py" in stdout, (
            "Dispatch log must list the actual injected file paths"
        )

    def test_stdout_shows_paths_for_cline_estimated(self, tmp_path):
        """Cline backend dispatch also logs the injected paths block."""
        cfg = _make_cfg(tmp_path, backend="cline")
        _, _, _, _, stdout = _run_dispatch(cfg, estimate=_SAMPLE_ESTIMATE_FT)

        assert _PATHS_HEADER in stdout, (
            "Cline dispatch log must show the paths block header"
        )
        assert "apps/dashboard/server.py" in stdout


# ---------------------------------------------------------------------------
# AC9 — Dispatch log shows no injected paths block for unestimated ticket
# ---------------------------------------------------------------------------

class TestAC9DispatchLogNoPathsForUnestimated:
    def test_stdout_has_no_paths_header_when_no_estimate(self, tmp_path):
        """Dispatch log must NOT contain the Start here block for unestimated tickets."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, _, _, stdout = _run_dispatch(cfg, estimate=None)

        assert _PATHS_HEADER not in stdout, (
            "Dispatch log must NOT show 'Start here' block when no estimate file exists"
        )

    def test_stdout_still_shows_size_routing_for_unestimated(self, tmp_path):
        """[size-routing] line must still appear even when no estimate paths block."""
        cfg = _make_cfg(tmp_path, backend="claude-code")
        _, _, _, _, stdout = _run_dispatch(cfg, estimate=None)

        assert "[size-routing]" in stdout, (
            "size-routing line must still appear in dispatch log regardless of estimate"
        )

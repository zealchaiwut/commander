"""Tests for #787 AC2: COMMANDER_HANG_REDISPATCH=1 → redispatch exactly once with continuation prompt."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


def _fake_cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.worktree_coder = tmp_path
    cfg.worktree_tester = tmp_path
    cfg.worktree_tester_app = tmp_path
    cfg.repo_name = None
    cfg.api_url = None
    cfg.coder_prompt_template = None
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.sprints_dir = tmp_path / "sprints"
    cfg.sprints_dir.mkdir(parents=True, exist_ok=True)
    cfg.app_default_port = None
    cfg.documentor_enabled = False
    return cfg


def _run_sprint_controlled(
    tmp_path: Path,
    coder_returns: list,  # list of (ok, category) for each coder dispatch
    *,
    hang_redispatch_env: str = "1",
) -> tuple[sm.SprintSummary, list, list]:
    """Run run_sprint for issue #1 with controlled coder dispatch returns."""
    dispatch_calls: list = []       # captures (attempt_kind,) per dispatch
    transition_calls: list = []

    coder_iter = iter(coder_returns)

    def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                   repo_name=None, cfg=None, chosen_port=None,
                   rate_limit_events=None, on_running=None, sprint_label=None,
                   prior_failures=None, hang_continuation=None, attempt_kind=None):
        if on_running:
            on_running()
        dispatch_calls.append({
            "attempt_kind": attempt_kind,
            "hang_continuation": hang_continuation,
            "prior_failures": prior_failures,
        })
        return next(coder_iter)

    def fake_transition(issue_num, target, actor=None, repo_name=None, note=None):
        transition_calls.append((issue_num, target))

    env = {k: v for k, v in os.environ.items()}
    env["COMMANDER_HANG_REDISPATCH"] = hang_redispatch_env
    env.pop("COMMANDER_MAX_FIX_ROUNDS", None)

    with (
        patch.object(sm, "_create_sprint_branch", lambda b: None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
        patch.object(sm, "handle_post_tester",
                     lambda *a, **kw: (True, "merged", None)),
        patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
        patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        patch.object(sm, "_warn_file_conflicts", lambda i: None),
        patch.object(sm, "_setup_pid_file", lambda n: None),
        patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
        patch.object(sm, "_transition_safe", fake_transition),
        patch.object(sm, "record_failure", lambda *a, **kw: None),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.object(sm, "_db_agent_start_sm", lambda *a, **kw: None),
        patch.object(sm, "_db_agent_finish_sm", lambda *a, **kw: None),
        patch.object(sm, "_emit_sprint_lifecycle_event", lambda *a, **kw: None),
        patch.object(sm, "_add_blocked_label", lambda *a, **kw: None),
        patch.dict(os.environ, env, clear=True),
    ):
        summary, _ = sm.run_sprint(
            label="sprint-99",
            skip_gates=True,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
            cfg=_fake_cfg(tmp_path),
        )
    return summary, dispatch_calls, transition_calls


class TestAC2RedispatchOnce:
    def test_single_hang_triggers_redispatch_when_enabled(self, tmp_path):
        """First hang with COMMANDER_HANG_REDISPATCH=1 must trigger exactly one redispatch."""
        # First dispatch: HANG. Second dispatch: success.
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (True, None),
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) == 2, (
            f"Expected 2 coder dispatches (initial + hang_continue), got {len(calls)}"
        )

    def test_hang_continue_dispatch_has_hang_continuation_context(self, tmp_path):
        """The redispatch after a hang must include hang_continuation context."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (True, None),
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) >= 2
        redispatch = calls[1]  # second call is the hang_continue
        assert redispatch["hang_continuation"] is not None, (
            "hang_continue dispatch must have hang_continuation context set"
        )

    def test_hang_continuation_context_has_timestamp_and_log_tail(self, tmp_path):
        """hang_continuation dict must have timestamp and log_tail fields."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (True, None),
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) >= 2
        ctx = calls[1]["hang_continuation"]
        assert ctx is not None
        assert "timestamp" in ctx, "hang_continuation must include timestamp"
        assert "log_tail" in ctx, "hang_continuation must include log_tail"

    def test_hang_continuation_prompt_includes_required_text(self, tmp_path):
        """_dispatch_coder with hang_continuation must build prompt with continuation text."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")

        captured_prompts = []

        def capture_cmd(cmd, stdout, stderr, cwd, env):
            # find the -p argument
            try:
                idx = cmd.index("-p")
                captured_prompts.append(cmd[idx + 1])
            except (ValueError, IndexError):
                pass
            proc = MagicMock()
            proc.wait.return_value = 0
            return proc

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = tmp_path / "logs"
        cfg_stub.logs_dir.mkdir(parents=True, exist_ok=True)

        fake_detector = MagicMock()
        fake_detector.killed = False

        hang_ctx = {
            "timestamp": "2026-06-11T12:00:00Z",
            "log_tail": ["last line of output"],
        }

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", return_value=None),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_design_docs_guard", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=("sha1", "sha2", None)),
            patch.object(sm, "_db_agent_start_sm"),
            patch.object(sm, "_db_update_worktree_shas_sm"),
        ):
            mock_sub.Popen.side_effect = capture_cmd
            sm._dispatch_coder(
                issue_num=42,
                alert_modes=[],
                cfg=cfg_stub,
                hang_continuation=hang_ctx,
            )

        assert captured_prompts, "Must have captured a prompt"
        prompt = captured_prompts[0]
        assert "idle-killed" in prompt or "continue" in prompt.lower(), (
            f"Hang-continue prompt must contain 'idle-killed' or 'continue'; got: {prompt[:300]!r}"
        )
        assert "2026-06-11T12:00:00Z" in prompt or "prior attempt" in prompt.lower(), (
            f"Prompt must reference the timestamp or prior attempt; got: {prompt[:300]!r}"
        )

    def test_exactly_one_redispatch_per_ticket(self, tmp_path):
        """Only one redispatch per ticket: initial hang → hang_continue → success."""
        summary, calls, _ = _run_sprint_controlled(
            tmp_path,
            coder_returns=[
                (False, sm.FailureCategory.HANG),
                (True, None),  # hang_continue succeeds
            ],
            hang_redispatch_env="1",
        )
        assert len(calls) == 2, f"Exactly 2 dispatches expected, got {len(calls)}"

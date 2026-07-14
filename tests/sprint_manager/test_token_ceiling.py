"""Tests for issue #1943 — Configurable token/cost ceiling for sprint runs.

AC coverage:
  AC-1: --token-ceiling CLI flag; SprintConfig.token_ceiling field;
        run_sprint / run_sprint_loop accept token_ceiling parameter.
  AC-2: After each ticket, cumulative tokens vs ceiling is compared.
  AC-3: Current ticket allowed to finish; no further tickets dispatched.
  AC-4: SprintState.ceiling_hit set to True on breach.
  AC-5: Clear log message emitted on breach.
  AC-6: When disabled (0/absent), ceiling_hit is False, runtime unchanged.
  AC-7: Unit tests: ceiling not set (no-op), not reached, breached mid-sprint.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

from services.sprint_manager.state import IssueState, SprintState


# ── AC-4/AC-6: ceiling_hit serialisation ─────────────────────────────────────

class TestCeilingHitSerialisation:
    """ceiling_hit round-trips through to_dict → from_dict and state.save."""

    def test_default_is_false(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        assert st.ceiling_hit is False

    def test_to_dict_includes_field(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        d = st.to_dict()
        assert "ceiling_hit" in d
        assert d["ceiling_hit"] is False

    def test_to_dict_true_when_set(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.ceiling_hit = True
        d = st.to_dict()
        assert d["ceiling_hit"] is True

    def test_from_dict_restores_ceiling_hit_true(self):
        st = SprintState.from_dict({
            "sprint_label": "sprint-1", "sprint_number": 1, "ceiling_hit": True,
        })
        assert st.ceiling_hit is True

    def test_from_dict_defaults_when_absent(self):
        """Old state JSON without ceiling_hit defaults to False."""
        st = SprintState.from_dict({"sprint_label": "sprint-1", "sprint_number": 1})
        assert st.ceiling_hit is False

    def test_save_load_preserves_ceiling_hit_true(self, tmp_path):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.ceiling_hit = True
        state_path = tmp_path / "state.json"
        st.save(state_path)
        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        assert restored.ceiling_hit is True

    def test_save_load_preserves_ceiling_hit_false(self, tmp_path):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        state_path = tmp_path / "state.json"
        st.save(state_path)
        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        assert restored.ceiling_hit is False


# ── AC-1: API surface — parameters exist ────────────────────────────────────

class TestApiSurface:
    """AC-1: run_sprint and run_sprint_loop accept token_ceiling."""

    def test_run_sprint_accepts_token_ceiling(self):
        import inspect
        import services.sprint_manager.sprint_manager as sm
        sig = inspect.signature(sm.run_sprint)
        assert "token_ceiling" in sig.parameters, (
            "run_sprint must accept a token_ceiling parameter"
        )

    def test_run_sprint_loop_accepts_token_ceiling(self):
        import inspect
        import services.sprint_manager.sprint_manager as sm
        sig = inspect.signature(sm.run_sprint_loop)
        assert "token_ceiling" in sig.parameters, (
            "run_sprint_loop must accept a token_ceiling parameter"
        )

    def test_sprint_config_has_token_ceiling(self):
        from services.sprint_manager.config import SprintConfig
        cfg = SprintConfig()
        assert hasattr(cfg, "token_ceiling"), (
            "SprintConfig must have a token_ceiling attribute"
        )
        assert cfg.token_ceiling == 0

    def test_apply_token_ceiling_helper_exists(self):
        import services.sprint_manager.sprint_manager as sm
        assert hasattr(sm, "_apply_token_ceiling"), (
            "_apply_token_ceiling helper must be defined in sprint_manager"
        )
        assert callable(sm._apply_token_ceiling)


# ── AC-7a / AC-6: ceiling disabled → no-op ───────────────────────────────────

class TestCeilingDisabled:
    """When token_ceiling is 0 (disabled), ceiling_hit stays False and no log is emitted."""

    def test_apply_zero_ceiling_returns_false(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 9_999_999
        st.total_tokens_out = 9_999_999
        result = sm._apply_token_ceiling(st, 0)
        assert result is False

    def test_ceiling_hit_stays_false_with_zero_ceiling(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 1_000_000
        sm._apply_token_ceiling(st, 0)
        assert st.ceiling_hit is False

    def test_negative_ceiling_is_treated_as_disabled(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 1_000_000
        result = sm._apply_token_ceiling(st, -1)
        assert result is False
        assert st.ceiling_hit is False


# ── AC-7b: ceiling set, not reached ──────────────────────────────────────────

class TestCeilingNotReached:
    """When spending < ceiling, ceiling_hit stays False and dispatch continues."""

    def test_apply_ceiling_not_reached(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 400
        st.total_tokens_out = 100  # 500 total, ceiling = 1000
        result = sm._apply_token_ceiling(st, 1000)
        assert result is False
        assert st.ceiling_hit is False

    def test_one_below_ceiling(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 999
        st.total_tokens_out = 0   # 999 total, ceiling = 1000
        result = sm._apply_token_ceiling(st, 1000)
        assert result is False
        assert st.ceiling_hit is False


# ── AC-7c / AC-4 / AC-5: ceiling breached ────────────────────────────────────

class TestCeilingBreached:
    """When spending >= ceiling, ceiling_hit is set and True is returned."""

    def test_apply_ceiling_breached(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 800
        st.total_tokens_out = 201  # 1001 total, ceiling = 1000
        result = sm._apply_token_ceiling(st, 1000)
        assert result is True
        assert st.ceiling_hit is True

    def test_exactly_at_ceiling(self):
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 500
        st.total_tokens_out = 500  # 1000 total, ceiling = 1000 → breach at equal
        result = sm._apply_token_ceiling(st, 1000)
        assert result is True
        assert st.ceiling_hit is True

    def test_log_message_emitted_on_breach(self, capsys):
        """AC-5: clear message indicating ceiling was hit and tokens vs limit."""
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 600
        st.total_tokens_out = 401  # 1001 total, ceiling = 1000
        sm._apply_token_ceiling(st, 1000)
        captured = capsys.readouterr()
        assert "[token-ceiling]" in captured.out
        # Must mention how many tokens were spent
        assert "1001" in captured.out or "1,001" in captured.out
        # Must mention the limit
        assert "1000" in captured.out or "1,000" in captured.out

    def test_no_duplicate_log_on_repeated_call(self, capsys):
        """Log emitted only once even when _apply_token_ceiling is called again."""
        import services.sprint_manager.sprint_manager as sm
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.total_tokens_in = 2000
        st.total_tokens_out = 0  # 2000 total, ceiling = 1000
        sm._apply_token_ceiling(st, 1000)
        sm._apply_token_ceiling(st, 1000)  # ceiling_hit already True
        captured = capsys.readouterr()
        assert captured.out.count("[token-ceiling]") == 1


# ── Behavioral dispatch tests ─────────────────────────────────────────────────
#
# These tests call run_sprint_loop with all external side effects stubbed out,
# tracking how many times _dispatch_coder is called to verify that the ceiling
# stops dispatch at the right point.

def _make_serial_pf(sm, state, summary, tmp_path):
    """Build a minimal _SprintPreflightResult for serial (non-pipeline) dispatch."""
    dispatch_levels = [[iss] for iss in state.issues]
    level_nums = [[iss.number] for iss in state.issues]
    return sm._SprintPreflightResult(
        state=state,
        state_path=tmp_path / "state.json",
        summary=summary,
        sprint_num=None,
        sprint_branch="sprint/sprint-test",
        target_branch="sprint/sprint-test",
        eff_repo="o/r",
        api_url=None,
        run_id="run-test",
        rerun_decisions={},
        eff_sprints_dir=tmp_path,
        dispatch_levels=dispatch_levels,
        level_nums_by_idx=level_nums,
        pipeline_on=False,
        start_time=0.0,
        early_exit=False,
    )


def _stub_sprint_loop(monkeypatch, sm):
    """Stub all external side effects so run_sprint_loop can run in isolation."""
    monkeypatch.setattr(sm, "_wait_if_paused", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_load_estimate", lambda num: None)
    monkeypatch.setattr(sm, "_is_issue_merged_into_target", lambda *a, **k: False)
    monkeypatch.setattr(sm, "_prune_stale_local_feature_branch", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_select_coder_backend", lambda *a, **k: "claude-code")
    monkeypatch.setattr(sm, "_effective_coder_backend", lambda *a, **k: "claude-code")
    monkeypatch.setattr(sm, "_resolve_coder_model",
                        lambda *a, **k: ("claude-sonnet-4-6", "test"))
    monkeypatch.setattr(sm, "_db_agent_start_sm", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_db_update_worktree_shas_sm", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_db_agent_finish_sm", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_emit_sprint_lifecycle_event", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_pool_acquire", lambda: None)
    monkeypatch.setattr(sm, "_pool_release", lambda s: None)
    monkeypatch.setattr(sm, "_dispatch_coder", lambda num, *a, **k: (True, None))
    monkeypatch.setattr(sm, "_dispatch_tester", lambda num, *a, **k: (0, None))
    monkeypatch.setattr(sm, "handle_post_tester", lambda **k: (True, "merged", None))
    monkeypatch.setattr(sm, "_post_sprint_status", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_neon_ticket_status", lambda *a, **k: None)
    monkeypatch.setattr(sm, "get_role_profile", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_ica_cost_from_tokens", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_emit_ticket_failed", lambda *a, **k: None)
    monkeypatch.setattr(sm, "dispatch_alerts", lambda *a, **k: None)
    monkeypatch.setattr(sm, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_publish_gate_failure_analyses", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_bump_estimate_size", lambda num: None)
    monkeypatch.setattr(sm.SprintState, "save", lambda self, p: None)
    # Each token_window_sums call returns 800+200=1000 tokens.
    # Two calls per ticket (coder + tester) → 2000 tokens total per ticket.
    monkeypatch.setattr(sm, "_token_window_sums", lambda *a, **k: (800, 200))
    monkeypatch.setattr(sm, "_token_window_sums_full", lambda *a, **k: (800, 200, 0, 0, None))
    monkeypatch.setattr(sm, "_token_window_utc_now", lambda: "2024-01-01T00:00:00Z")


def _run_loop(sm, pf, token_ceiling: int) -> None:
    sm.run_sprint_loop(
        pf,
        label="sprint-t",
        preflight_approved=None,
        dry_run=False,
        resume=False,
        retry_failed=False,
        skip_gates=False,
        gate_pytest=True,
        gate_lint=True,
        gate_merge_preview=True,
        gate_typecheck=True,
        gate_design=True,
        gate_frontend_lint=True,
        gate_monolith=True,
        gate_scope="changed",
        alert_modes=[],
        cfg=None,
        token_ceiling=token_ceiling,
    )


class TestCeilingDispatchBehavior:
    """AC-2, AC-3, AC-4: dispatch loop stops at the right point."""

    def test_ceiling_disabled_dispatches_all_tickets(self, monkeypatch, tmp_path):
        """AC-7a: token_ceiling=0 → all tickets dispatched, ceiling_hit=False."""
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        dispatched = []
        monkeypatch.setattr(sm, "_dispatch_coder",
                            lambda num, *a, **k: (dispatched.append(num), (True, None))[1])

        issues = [sm.IssueState(number=1, title="T1"), sm.IssueState(number=2, title="T2")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        _run_loop(sm, pf, token_ceiling=0)

        assert 1 in dispatched and 2 in dispatched, (
            "Both tickets must be dispatched when ceiling is disabled"
        )
        assert state.ceiling_hit is False

    def test_ceiling_not_reached_dispatches_all_tickets(self, monkeypatch, tmp_path):
        """AC-7b: ceiling=999_999_999 → all tickets dispatched, ceiling_hit=False."""
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        dispatched = []
        monkeypatch.setattr(sm, "_dispatch_coder",
                            lambda num, *a, **k: (dispatched.append(num), (True, None))[1])

        issues = [sm.IssueState(number=1, title="T1"), sm.IssueState(number=2, title="T2")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        _run_loop(sm, pf, token_ceiling=999_999_999)

        assert 1 in dispatched and 2 in dispatched, (
            "Both tickets must be dispatched when ceiling is not reached"
        )
        assert state.ceiling_hit is False

    def test_ceiling_breached_stops_after_current_ticket(self, monkeypatch, tmp_path):
        """AC-3/AC-7c: ceiling=1 (guaranteed breach) → only ticket 1 dispatched."""
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        dispatched = []
        monkeypatch.setattr(sm, "_dispatch_coder",
                            lambda num, *a, **k: (dispatched.append(num), (True, None))[1])

        issues = [sm.IssueState(number=1, title="T1"), sm.IssueState(number=2, title="T2")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        # ceiling=1 → first ticket spends 2000 tokens → guaranteed breach
        _run_loop(sm, pf, token_ceiling=1)

        assert 1 in dispatched, "First ticket must be dispatched (current is allowed to finish)"
        assert 2 not in dispatched, (
            "Second ticket must NOT be dispatched after ceiling breach"
        )

    def test_ceiling_breached_sets_ceiling_hit_flag(self, monkeypatch, tmp_path):
        """AC-4: state.ceiling_hit is True after ceiling breach."""
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        issues = [sm.IssueState(number=1, title="T1"), sm.IssueState(number=2, title="T2")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        _run_loop(sm, pf, token_ceiling=1)

        assert state.ceiling_hit is True

    def test_ceiling_breached_emits_log_message(self, monkeypatch, tmp_path, capsys):
        """AC-5: log message contains 'token-ceiling' with spend and limit info."""
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        issues = [sm.IssueState(number=1, title="T1"), sm.IssueState(number=2, title="T2")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        _run_loop(sm, pf, token_ceiling=1)

        captured = capsys.readouterr()
        assert "[token-ceiling]" in captured.out

    def test_prior_ceiling_hit_does_not_block_new_run(self, monkeypatch, tmp_path):
        """AC UAT-5: a new run with ceiling disabled ignores prior ceiling_hit.

        A resumed or re-started run with ceiling=0 proceeds normally even if
        the previous state.json had ceiling_hit=True.
        """
        import services.sprint_manager.sprint_manager as sm
        _stub_sprint_loop(monkeypatch, sm)

        dispatched = []
        monkeypatch.setattr(sm, "_dispatch_coder",
                            lambda num, *a, **k: (dispatched.append(num), (True, None))[1])

        issues = [sm.IssueState(number=3, title="T3"), sm.IssueState(number=4, title="T4")]
        state = sm.SprintState(sprint_label="sprint-t", sprint_number=999, issues=issues)
        # Simulate a prior run that hit the ceiling
        state.ceiling_hit = True
        summary = sm.SprintSummary()
        pf = _make_serial_pf(sm, state, summary, tmp_path)

        # New run with ceiling disabled — _ceiling_stop starts False
        _run_loop(sm, pf, token_ceiling=0)

        # Both tickets should dispatch (ceiling_hit from prior run doesn't block)
        assert 3 in dispatched and 4 in dispatched, (
            "Prior ceiling_hit must not block a new run with ceiling disabled"
        )

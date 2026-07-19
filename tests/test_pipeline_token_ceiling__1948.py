"""Tests for issue #1948: Enforce token ceiling in pipeline-mode dispatch.

AC items verified:
  AC-1  _run_pipeline_dispatch accepts a `token_ceiling` keyword-only param (default 0)
  AC-2  _apply_token_ceiling is importable from services.sprint_manager.state
         (moved there so pipeline.py can import it without circular dependency)
  AC-3  When token_ceiling=0 (disabled), all levels run; no premature stop
  AC-4  When ceiling is breached after a level, the next level is NOT dispatched
  AC-5  state.ceiling_hit is set to True when ceiling is breached in pipeline mode
  AC-6  The call site in run_sprint passes token_ceiling to _run_pipeline_dispatch
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

PIPELINE_PATH = REPO_ROOT / "services" / "sprint_manager" / "pipeline.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ── AC-1: _run_pipeline_dispatch signature includes token_ceiling ─────────────

def test_run_pipeline_dispatch_accepts_token_ceiling():
    """AC-1: _run_pipeline_dispatch must have a token_ceiling keyword-only param."""
    import services.sprint_manager.pipeline as pl
    sig = inspect.signature(pl._run_pipeline_dispatch)
    assert "token_ceiling" in sig.parameters, (
        "_run_pipeline_dispatch must accept token_ceiling parameter"
    )
    param = sig.parameters["token_ceiling"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "token_ceiling must be keyword-only (consistent with other params)"
    )
    assert param.default == 0, (
        "token_ceiling must default to 0 (disabled)"
    )


# ── AC-2: _apply_token_ceiling is importable from state.py ───────────────────

def test_apply_token_ceiling_importable_from_state():
    """AC-2: _apply_token_ceiling must be importable from services.sprint_manager.state."""
    from services.sprint_manager.state import _apply_token_ceiling
    assert callable(_apply_token_ceiling), (
        "_apply_token_ceiling must be callable when imported from state.py"
    )


def test_apply_token_ceiling_still_accessible_on_sprint_manager():
    """AC-2: sm._apply_token_ceiling must remain accessible (existing tests rely on it)."""
    import services.sprint_manager.sprint_manager as sm
    assert hasattr(sm, "_apply_token_ceiling"), (
        "_apply_token_ceiling must be accessible on the sprint_manager module"
    )
    assert callable(sm._apply_token_ceiling)


def test_apply_token_ceiling_same_object_in_state_and_sm():
    """AC-2: The function on sm and on state must be the same object (no duplication)."""
    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import _apply_token_ceiling as state_fn
    assert sm._apply_token_ceiling is state_fn, (
        "sm._apply_token_ceiling must be the same object imported from state.py, "
        "not a separate copy"
    )


# ── AC-3: token_ceiling=0 leaves all levels running ─────────────────────────

def test_pipeline_ceiling_disabled_all_levels_run(tmp_path):
    """AC-3: When token_ceiling=0, both levels dispatch regardless of token spend."""
    from services.sprint_manager.pipeline import _run_pipeline_dispatch
    from services.sprint_manager.state import IssueState, SprintState, SprintSummary

    ist0 = IssueState(number=1, title="t1", status="pending")
    ist1 = IssueState(number=2, title="t2", status="pending")

    state = SprintState(sprint_label="sprint-test", sprint_number=1)
    state.issues = [ist0, ist1]
    state.total_tokens_in = 999_999
    state.total_tokens_out = 999_999
    state.max_coder_slots = 1

    levels_run = []

    def fake_run_level(level_nums, coder_fn, tester_fn, *,
                       pipeline, on_merged=None, on_needs_rework=None,
                       on_dropped=None, max_attempts=None):
        levels_run.append(list(level_nums))

    state_path = tmp_path / "state.json"
    summary = SprintSummary()

    with patch("services.sprint_manager.pipeline._run_pipeline_level", side_effect=fake_run_level), \
         patch("services.sprint_manager.pipeline._post_sprint_status"), \
         patch("services.sprint_manager.pipeline.structured_log"):
        _run_pipeline_dispatch(
            state=state, state_path=state_path, summary=summary,
            dispatch_levels=[[ist0], [ist1]],
            level_nums_by_idx={0: [1], 1: [2]},
            label="sprint-test", sprint_num=1, eff_repo="owner/repo",
            api_url=None, target_branch="develop", sprint_branch="sprint/test",
            alert_modes=[], cfg=None, run_id="r1",
            eff_sprints_dir=str(tmp_path), rerun_decisions={},
            skip_gates=True, gate_pytest=False, gate_lint=False,
            gate_merge_preview=False, gate_typecheck=False,
            gate_design=False, gate_frontend_lint=False,
            gate_monolith=False, gate_coder_no_test_edits=False,
            gate_scope="changed", resume=False, retry_failed=False,
            token_ceiling=0,
        )

    assert len(levels_run) == 2, (
        f"token_ceiling=0 must not stop any levels; levels_run={levels_run}"
    )
    assert [1] in levels_run, "Level 0 (ticket #1) should run"
    assert [2] in levels_run, "Level 1 (ticket #2) should run"


# ── AC-4: ceiling breach stops the next level ────────────────────────────────

def test_pipeline_ceiling_stops_next_level_after_breach(tmp_path, capsys):
    """AC-4: When ceiling is exceeded after level 0, level 1 is NOT dispatched."""
    from services.sprint_manager.pipeline import _run_pipeline_dispatch
    from services.sprint_manager.state import IssueState, SprintState, SprintSummary

    ist0 = IssueState(number=1, title="t1", status="pending")
    ist1 = IssueState(number=2, title="t2", status="pending")

    state = SprintState(sprint_label="sprint-test", sprint_number=1)
    state.issues = [ist0, ist1]
    state.total_tokens_in = 800   # below ceiling of 1000
    state.total_tokens_out = 0
    state.max_coder_slots = 1

    levels_run = []

    def fake_run_level(level_nums, coder_fn, tester_fn, *,
                       pipeline, on_merged=None, on_needs_rework=None,
                       on_dropped=None, max_attempts=None):
        levels_run.append(list(level_nums))
        # Simulate spending tokens that exceed the ceiling during level 0.
        state.total_tokens_in += 300   # total now 1100, ceiling is 1000

    state_path = tmp_path / "state.json"
    summary = SprintSummary()

    with patch("services.sprint_manager.pipeline._run_pipeline_level", side_effect=fake_run_level), \
         patch("services.sprint_manager.pipeline._post_sprint_status"), \
         patch("services.sprint_manager.pipeline.structured_log"):
        _run_pipeline_dispatch(
            state=state, state_path=state_path, summary=summary,
            dispatch_levels=[[ist0], [ist1]],
            level_nums_by_idx={0: [1], 1: [2]},
            label="sprint-test", sprint_num=1, eff_repo="owner/repo",
            api_url=None, target_branch="develop", sprint_branch="sprint/test",
            alert_modes=[], cfg=None, run_id="r1",
            eff_sprints_dir=str(tmp_path), rerun_decisions={},
            skip_gates=True, gate_pytest=False, gate_lint=False,
            gate_merge_preview=False, gate_typecheck=False,
            gate_design=False, gate_frontend_lint=False,
            gate_monolith=False, gate_coder_no_test_edits=False,
            gate_scope="changed", resume=False, retry_failed=False,
            token_ceiling=1000,
        )

    assert [1] in levels_run, "Level 0 (ticket #1) must dispatch"
    assert [2] not in levels_run, (
        f"Level 1 (ticket #2) must NOT dispatch after ceiling breach; "
        f"levels_run={levels_run}"
    )


# ── AC-5: state.ceiling_hit is set after breach in pipeline mode ──────────────

def test_pipeline_ceiling_hit_flag_set_on_breach(tmp_path):
    """AC-5: state.ceiling_hit is True after token ceiling is breached in pipeline mode."""
    from services.sprint_manager.pipeline import _run_pipeline_dispatch
    from services.sprint_manager.state import IssueState, SprintState, SprintSummary

    ist0 = IssueState(number=1, title="t1", status="pending")
    ist1 = IssueState(number=2, title="t2", status="pending")

    state = SprintState(sprint_label="sprint-test", sprint_number=1)
    state.issues = [ist0, ist1]
    state.total_tokens_in = 800
    state.total_tokens_out = 0
    state.max_coder_slots = 1

    def fake_run_level(level_nums, coder_fn, tester_fn, *,
                       pipeline, on_merged=None, on_needs_rework=None,
                       on_dropped=None, max_attempts=None):
        state.total_tokens_in += 300   # push to 1100, ceiling is 1000

    state_path = tmp_path / "state.json"
    summary = SprintSummary()

    with patch("services.sprint_manager.pipeline._run_pipeline_level", side_effect=fake_run_level), \
         patch("services.sprint_manager.pipeline._post_sprint_status"), \
         patch("services.sprint_manager.pipeline.structured_log"):
        _run_pipeline_dispatch(
            state=state, state_path=state_path, summary=summary,
            dispatch_levels=[[ist0], [ist1]],
            level_nums_by_idx={0: [1], 1: [2]},
            label="sprint-test", sprint_num=1, eff_repo="owner/repo",
            api_url=None, target_branch="develop", sprint_branch="sprint/test",
            alert_modes=[], cfg=None, run_id="r1",
            eff_sprints_dir=str(tmp_path), rerun_decisions={},
            skip_gates=True, gate_pytest=False, gate_lint=False,
            gate_merge_preview=False, gate_typecheck=False,
            gate_design=False, gate_frontend_lint=False,
            gate_monolith=False, gate_coder_no_test_edits=False,
            gate_scope="changed", resume=False, retry_failed=False,
            token_ceiling=1000,
        )

    assert state.ceiling_hit is True, (
        "state.ceiling_hit must be True after breach in pipeline mode"
    )


# ── AC-6: call site in run_sprint passes token_ceiling ───────────────────────

def test_run_sprint_call_site_passes_token_ceiling_to_pipeline():
    """AC-6: run_sprint must pass token_ceiling to _run_pipeline_dispatch."""
    source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")

    # The _run_pipeline_dispatch call inside run_sprint must include token_ceiling.
    # We verify by checking the source for the keyword argument at the call site.
    # This is the call that was previously missing token_ceiling (the bug).
    assert "token_ceiling=" in source, (
        "run_sprint must pass token_ceiling= to _run_pipeline_dispatch"
    )

    # More specifically, it must pass it to _run_pipeline_dispatch (not only to
    # run_sprint_loop, which is where it already lived before this fix).
    import ast
    tree = ast.parse(source)
    pipeline_calls_with_ceiling = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_pipeline_dispatch"
        ):
            kw_names = [kw.arg for kw in node.keywords]
            if "token_ceiling" in kw_names:
                pipeline_calls_with_ceiling.append(node)

    assert pipeline_calls_with_ceiling, (
        "_run_pipeline_dispatch must be called with token_ceiling= keyword argument"
    )


# ── AC-7: ceiling not triggered when below threshold ─────────────────────────

def test_pipeline_ceiling_not_triggered_when_below_threshold(tmp_path):
    """AC-7: When spend stays below ceiling, all levels run normally."""
    from services.sprint_manager.pipeline import _run_pipeline_dispatch
    from services.sprint_manager.state import IssueState, SprintState, SprintSummary

    ist0 = IssueState(number=1, title="t1", status="pending")
    ist1 = IssueState(number=2, title="t2", status="pending")

    state = SprintState(sprint_label="sprint-test", sprint_number=1)
    state.issues = [ist0, ist1]
    state.total_tokens_in = 100
    state.total_tokens_out = 0
    state.max_coder_slots = 1

    levels_run = []

    def fake_run_level(level_nums, coder_fn, tester_fn, *,
                       pipeline, on_merged=None, on_needs_rework=None,
                       on_dropped=None, max_attempts=None):
        levels_run.append(list(level_nums))
        state.total_tokens_in += 50   # total only 150/200/... well below 1000

    state_path = tmp_path / "state.json"
    summary = SprintSummary()

    with patch("services.sprint_manager.pipeline._run_pipeline_level", side_effect=fake_run_level), \
         patch("services.sprint_manager.pipeline._post_sprint_status"), \
         patch("services.sprint_manager.pipeline.structured_log"):
        _run_pipeline_dispatch(
            state=state, state_path=state_path, summary=summary,
            dispatch_levels=[[ist0], [ist1]],
            level_nums_by_idx={0: [1], 1: [2]},
            label="sprint-test", sprint_num=1, eff_repo="owner/repo",
            api_url=None, target_branch="develop", sprint_branch="sprint/test",
            alert_modes=[], cfg=None, run_id="r1",
            eff_sprints_dir=str(tmp_path), rerun_decisions={},
            skip_gates=True, gate_pytest=False, gate_lint=False,
            gate_merge_preview=False, gate_typecheck=False,
            gate_design=False, gate_frontend_lint=False,
            gate_monolith=False, gate_coder_no_test_edits=False,
            gate_scope="changed", resume=False, retry_failed=False,
            token_ceiling=1000,
        )

    assert len(levels_run) == 2, (
        f"Both levels must run when spend stays below ceiling; levels_run={levels_run}"
    )
    assert state.ceiling_hit is False, (
        "ceiling_hit must stay False when spend never reaches ceiling"
    )

"""Tests for #708: Use configured model for gate failure analysis.

Follow-up to #701. The original `_generate_gate_failure_analysis()` hardcoded
`--model claude-haiku-4-5-20251001` in its `claude -p` subprocess call, ignoring
the per-agent model config added in #700. This broke consistency with every
other agent, which respect `sprint.yaml` model settings.

AC items verified:
  AC-1  When a cfg is supplied, the subprocess uses the configured model
        (cfg.reviewer_model), not the hardcoded value.
  AC-2  A custom configured model flows through to the --model flag verbatim.
  AC-3  When cfg is None (unavailable), it falls back to the hardcoded default
        claude-haiku-4-5-20251001 so the call still works.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm

_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


def _model_from_subprocess_args(mock_run) -> str:
    """Extract the value passed to --model in the captured subprocess call."""
    args = mock_run.call_args[0][0]
    assert "--model" in args, f"--model flag missing from call: {args}"
    return args[args.index("--model") + 1]


def _ok_result() -> MagicMock:
    res = MagicMock()
    res.returncode = 0
    res.stdout = '{"root_cause": "rc", "prevention": "pv"}'
    return res


def test_uses_configured_reviewer_model_from_cfg():
    """AC-1: configured model is used instead of the hardcoded haiku string."""
    cfg = MagicMock()
    cfg.reviewer_model = "claude-haiku-4-5"
    with patch.object(sm.subprocess, "run", return_value=_ok_result()) as mrun:
        sm._generate_gate_failure_analysis("typecheck", "boom", issue_num=1, cfg=cfg)
    assert _model_from_subprocess_args(mrun) == "claude-haiku-4-5"


def test_custom_configured_model_flows_through():
    """AC-2: any configured model value is passed through verbatim."""
    cfg = MagicMock()
    cfg.reviewer_model = "claude-sonnet-4-6"
    with patch.object(sm.subprocess, "run", return_value=_ok_result()) as mrun:
        sm._generate_gate_failure_analysis("lint", "boom", issue_num=2, cfg=cfg)
    assert _model_from_subprocess_args(mrun) == "claude-sonnet-4-6"


def test_falls_back_to_hardcoded_default_when_cfg_none():
    """AC-3: with no cfg, the hardcoded default model is still used."""
    with patch.object(sm.subprocess, "run", return_value=_ok_result()) as mrun:
        sm._generate_gate_failure_analysis("tests", "boom", issue_num=3, cfg=None)
    assert _model_from_subprocess_args(mrun) == _FALLBACK_MODEL


def test_no_longer_hardcodes_haiku_when_other_model_configured():
    """AC-1 (negative): the hardcoded value is not used when cfg differs."""
    cfg = MagicMock()
    cfg.reviewer_model = "claude-sonnet-4-6"
    with patch.object(sm.subprocess, "run", return_value=_ok_result()) as mrun:
        sm._generate_gate_failure_analysis("typecheck", "boom", issue_num=4, cfg=cfg)
    assert _model_from_subprocess_args(mrun) != _FALLBACK_MODEL

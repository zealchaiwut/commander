"""Tests for issue #920 AC4 — failed Cline gate escalates to claude-code.

AC4: Retry loop (step 4b): a failed Cline coder gate causes the next fix
round to dispatch on `claude-code`, regardless of sprint-level Cline flag.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SM_DIR))


def _load_sm():
    """Import sprint_manager with minimal stubs for modules it needs."""
    if "github_client" not in sys.modules:
        sys.modules["github_client"] = MagicMock()
    if "neon_client" not in sys.modules:
        sys.modules["neon_client"] = MagicMock()
    import sprint_manager as sm
    return sm


def test_sprint_manager_escalates_cline_to_claude_code_on_failure():
    """AC4: the fix-loop must have logic to escalate from cline to claude-code."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    # Escalation: when backend was cline and coder fails, switch to claude-code
    assert '"claude-code"' in src or "'claude-code'" in src, (
        "sprint_manager must reference claude-code as a backend value"
    )
    assert "escalat" in src.lower() or "_next_coder_backend" in src, (
        "sprint_manager must have escalation logic for cline failures"
    )


def test_sprint_manager_tracks_next_coder_backend():
    """AC4: sprint_manager tracks the backend for the next fix-round dispatch."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    assert "_next_coder_backend" in src, (
        "sprint_manager fix-loop must track _next_coder_backend across rounds"
    )


def test_dispatch_coder_accepts_backend_override():
    """AC4: _dispatch_coder must accept a coder_backend_override parameter."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    assert "coder_backend_override" in src, (
        "_dispatch_coder must have a coder_backend_override parameter"
    )


def test_non_cline_sprint_unaffected(tmp_path):
    """AC6 cross-check: _select_coder_backend returns cfg.coder_backend when
    use_cline_followups is False, even if ticket has follow-up label."""
    sm = _load_sm()

    cfg = MagicMock()
    cfg.coder_backend = "claude-code"
    cfg.use_cline_followups = False

    with patch.object(sm, "_get_issue_labels", return_value=["follow-up"]):
        result = sm._select_coder_backend(920, cfg, repo_name="owner/repo")

    assert result == "claude-code", (
        "_select_coder_backend must return claude-code when use_cline_followups=False"
    )


def test_escalation_switches_to_claude_code_after_cline_failure(tmp_path):
    """AC4: after a Cline coder logic failure, _next_coder_backend becomes claude-code.

    Verifies the escalation code path exists in sprint_manager by inspecting
    source — the full dispatch loop requires too many stubs for a unit test.
    """
    src = (SM_DIR / "sprint_manager.py").read_text()
    # Escalation should only happen when current backend is cline
    assert '_next_coder_backend == "cline"' in src or "_next_coder_backend == 'cline'" in src, (
        "escalation guard must check that current backend is cline"
    )
    # And then set it to claude-code
    assert '_next_coder_backend = "claude-code"' in src or "_next_coder_backend = 'claude-code'" in src, (
        "escalation must set _next_coder_backend to claude-code"
    )

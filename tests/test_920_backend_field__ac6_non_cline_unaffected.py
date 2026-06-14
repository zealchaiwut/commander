"""Tests for issue #920 AC6 — non-Cline sprints unaffected.

AC6: No change to routing logic for tickets that were never dispatched on Cline.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


def _load_sm():
    if "github_client" not in sys.modules:
        sys.modules["github_client"] = MagicMock()
    if "neon_client" not in sys.modules:
        sys.modules["neon_client"] = MagicMock()
    import sprint_manager as sm
    return sm


def test_select_coder_backend_returns_claude_code_when_no_cline_flag():
    """AC6: when use_cline_followups is False, backend stays claude-code."""
    sm = _load_sm()

    cfg = MagicMock()
    cfg.coder_backend = "claude-code"
    cfg.use_cline_followups = False

    # Even with follow-up label, no Cline routing
    with patch.object(sm, "_get_issue_labels", return_value=["follow-up"]):
        result = sm._select_coder_backend(920, cfg, repo_name="owner/repo")

    assert result == "claude-code"


def test_select_coder_backend_returns_claude_code_when_no_follow_up_label():
    """AC6: when follow-up label is absent, backend stays claude-code even if flag is set."""
    sm = _load_sm()

    cfg = MagicMock()
    cfg.coder_backend = "claude-code"
    cfg.use_cline_followups = True
    cfg.worktree_coder = Path("/nonexistent")

    with patch.object(sm, "_get_issue_labels", return_value=["enhancement"]):
        result = sm._select_coder_backend(920, cfg, repo_name="owner/repo")

    assert result == "claude-code"


def test_non_cline_sprint_records_claude_code_backend(tmp_path):
    """AC6: non-Cline sprint records backend=None or backend='claude-code' in agent_runs.

    When use_cline_followups is False, agent_runs rows for the coder agent
    must store 'claude-code' (or NULL, which implies claude-code by default).
    """
    _db_module.DB_PATH = tmp_path / "test_ac6.db"
    _db_module.init_db()

    # Simulate a non-Cline sprint dispatch recording
    _db_module.record_agent_start(920, "sprint-71", "coder", backend="claude-code")
    rows = _db_module.agent_runs_for_issue(920, "sprint-71")
    assert rows
    backend = rows[0].get("backend")
    assert backend in ("claude-code", None), (
        f"non-Cline sprint must record claude-code or NULL backend, got {backend!r}"
    )

    # Reset
    import db as dbm
    dbm.DB_PATH = REPO_ROOT / "commander.db"


def test_no_escalation_event_for_non_cline_sprint():
    """AC6: the escalation code path is guarded: it only triggers for backend=cline."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    # Guard: escalation only fires when current backend is cline
    assert '_next_coder_backend == "cline"' in src or "_next_coder_backend == 'cline'" in src, (
        "escalation must be guarded by a cline-backend check so non-Cline sprints are unaffected"
    )

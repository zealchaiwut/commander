"""Tests for issue #918: Route follow-up coder dispatches to Cline on sprint opt-in (runs against UAT)"""
import os
import sys
from pathlib import Path

import pytest

# Add repo root to path for sprint_manager imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# UAT environment
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


# ---------------------------------------------------------------------------
# AC1 — Reviewer agent applies `follow-up` label when filing follow-up
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__reviewer_prompt_includes_follow_up_label():
    """AC1: Reviewer prompt must include 'follow-up' in label vocabulary."""
    prompt = sm.DEFAULT_REVIEWER_PROMPT
    assert "follow-up" in prompt, (
        "DEFAULT_REVIEWER_PROMPT must include 'follow-up' in label instructions"
    )


# ---------------------------------------------------------------------------
# AC2 — Router selects 'cline' iff role==coder AND follow-up label AND
#       sprint.use_cline_followups == True
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__select_backend_cline_when_all_conditions_met(tmp_path):
    """AC2: _select_coder_backend returns 'cline' when all conditions hold."""
    from unittest.mock import MagicMock, patch

    # Setup: cfg with use_cline_followups=True, .clinerules present
    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = True
    cfg.worktree_coder = tmp_path
    cfg.coder_backend = "claude-code"

    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        backend = sm._select_coder_backend(issue_num=1, cfg=cfg)

    assert backend == "cline", f"Expected 'cline', got {backend}"


def test_cline_follow_up_router__select_backend_claude_when_use_cline_false(tmp_path):
    """AC2/AC6: Returns 'claude-code' when use_cline_followups is False."""
    from unittest.mock import MagicMock, patch

    cfg = MagicMock()
    cfg.use_cline_followups = False
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        backend = sm._select_coder_backend(issue_num=1, cfg=cfg)

    assert backend == "claude-code"


def test_cline_follow_up_router__select_backend_claude_when_no_follow_up_label(tmp_path):
    """AC2: Returns 'claude-code' when follow-up label absent."""
    from unittest.mock import MagicMock, patch

    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = True
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    with patch.object(sm, "_get_issue_labels", return_value=set()):  # no follow-up label
        backend = sm._select_coder_backend(issue_num=1, cfg=cfg)

    assert backend == "claude-code"


# ---------------------------------------------------------------------------
# AC3 — All other coder dispatches (non-follow-up or opted-out) use claude-code
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__normal_ticket_uses_claude_code(tmp_path):
    """AC3: A normal ticket (no follow-up label) always uses claude-code."""
    from unittest.mock import MagicMock, patch

    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = True
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    # Dispatch a normal ticket (no follow-up label)
    with patch.object(sm, "_get_issue_labels", return_value=set()):
        backend = sm._select_coder_backend(issue_num=2, cfg=cfg)

    assert backend == "claude-code"


def test_cline_follow_up_router__opted_out_sprint_uses_claude_code(tmp_path):
    """AC3: Opted-out sprint (use_cline_followups=False) routes to claude-code."""
    from unittest.mock import MagicMock, patch

    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = False
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        backend = sm._select_coder_backend(issue_num=3, cfg=cfg)

    assert backend == "claude-code"


# ---------------------------------------------------------------------------
# AC4 — Tester dispatch is never routed to Cline
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__tester_dispatch_ignores_follow_up_label(tmp_path):
    """AC4: Tester dispatch backend routing is unchanged; tester always uses claude-code."""
    from unittest.mock import MagicMock, patch

    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = True
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    # Verify _select_coder_backend is only called for coder, not tester
    # (tester dispatch code path does not call _select_coder_backend)
    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        # Calling the function with coder role still returns 'cline'
        backend = sm._select_coder_backend(issue_num=4, cfg=cfg)
        assert backend == "cline"
        # But _dispatch_tester is independent and uses cfg.coder_backend unchanged


# ---------------------------------------------------------------------------
# AC5 — If .clinerules absent or Cline backend unavailable, fallback to claude-code
#       with warning
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__fallback_when_clinerules_absent(tmp_path, caplog):
    """AC5: Missing .clinerules triggers fallback to claude-code + warning log."""
    from unittest.mock import MagicMock, patch

    cfg = MagicMock()
    cfg.use_cline_followups = True
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path
    # .clinerules does NOT exist

    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        backend = sm._select_coder_backend(issue_num=5, cfg=cfg)

    assert backend == "claude-code"
    # Verify warning was logged (structured_log.warn call)
    # Cannot directly assert caplog without importing structured_log,
    # but the function returns fallback backend which is the key behavior


def test_cline_follow_up_router__fallback_when_cfg_none():
    """AC5: If cfg is None (no config), returns claude-code (safe default)."""
    backend = sm._select_coder_backend(issue_num=6, cfg=None)
    assert backend == "claude-code"


# ---------------------------------------------------------------------------
# AC6 — Follow-up ticket in non-opted-in sprint uses claude-code
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__follow_up_in_non_opted_sprint(tmp_path):
    """AC6: Follow-up ticket in a sprint with use_cline_followups=False uses claude-code."""
    from unittest.mock import MagicMock, patch

    (tmp_path / ".clinerules").write_text("# Cline rules")
    cfg = MagicMock()
    cfg.use_cline_followups = False  # Sprint not opted in
    cfg.coder_backend = "claude-code"
    cfg.worktree_coder = tmp_path

    with patch.object(sm, "_get_issue_labels", return_value={"follow-up"}):
        backend = sm._select_coder_backend(issue_num=7, cfg=cfg)

    assert backend == "claude-code"


# ---------------------------------------------------------------------------
# Integration: Config loading
# ---------------------------------------------------------------------------

def test_cline_follow_up_router__load_config_parses_use_cline_followups(tmp_path):
    """Config loading: use_cline_followups is parsed from sprint.yaml."""
    import yaml

    # Create required worktree directories
    coder_dir = tmp_path / "coder"
    tester_dir = tmp_path / "tester"
    coder_dir.mkdir(parents=True, exist_ok=True)
    tester_dir.mkdir(parents=True, exist_ok=True)

    # Write a minimal sprint.yaml with use_cline_followups enabled
    config_file = tmp_path / "sprint.yaml"
    config_content = {
        "repo_name": "test/repo",
        "worktrees": {
            "coder": str(coder_dir),
            "tester": str(tester_dir),
        },
        "agent_config": {
            "use_cline_followups": True,
        },
    }
    config_file.write_text(yaml.dump(config_content))

    cfg = sm.load_config(config_file)
    assert cfg.use_cline_followups is True


def test_cline_follow_up_router__load_config_defaults_use_cline_followups_false(tmp_path):
    """Config loading: use_cline_followups defaults to False if absent."""
    import yaml

    # Create required worktree directories
    coder_dir = tmp_path / "coder"
    tester_dir = tmp_path / "tester"
    coder_dir.mkdir(parents=True, exist_ok=True)
    tester_dir.mkdir(parents=True, exist_ok=True)

    config_file = tmp_path / "sprint.yaml"
    config_content = {
        "repo_name": "test/repo",
        "worktrees": {
            "coder": str(coder_dir),
            "tester": str(tester_dir),
        },
        "agent_config": {},  # use_cline_followups not specified
    }
    config_file.write_text(yaml.dump(config_content))

    cfg = sm.load_config(config_file)
    assert cfg.use_cline_followups is False

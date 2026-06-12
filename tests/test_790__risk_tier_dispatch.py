"""Tests for issue #790: Route tester model by risk tier before dispatch.

AC items verified:
  AC1  agent_config.tester.by_risk defines model mapping:
       LOW → haiku-4-5, MEDIUM → haiku-4-5, HIGH → sonnet-4-6
  AC2  Pre-dispatch classifier derives risk from labels, diff size, and paths
       touched — runs before tester is invoked
  AC3  Pre-dispatch risk is passed into the tester; tester doc retains its
       own derivation as fallback (hint injected into prompt)
  AC4  When pre-dispatch and tester-derived risk disagree, disagreement is
       logged (non-fatal)
  AC5  agent_runs records both risk_tier and model_used for each tester
       invocation
  AC6  A ticket with a security label dispatches tester on sonnet-4-6
  AC7  A LOW-risk ticket dispatches tester on haiku-4-5
"""
from __future__ import annotations

import os
import sys
import textwrap
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import services.sprint_manager.sprint_manager as sm
import db as _db_module


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_790.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: config mapping loaded from sprint.yaml ───────────────────────────────

def test_load_config_reads_tester_by_risk(tmp_path):
    """AC1: load_config parses agent_config.tester.by_risk from YAML."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
        agent_config:
          tester:
            by_risk:
              LOW: claude-haiku-4-5
              MEDIUM: claude-haiku-4-5
              HIGH: claude-sonnet-4-6
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)

    assert hasattr(cfg, "tester_by_risk"), "SprintConfig must have tester_by_risk attribute"
    assert cfg.tester_by_risk["LOW"] == "claude-haiku-4-5"
    assert cfg.tester_by_risk["MEDIUM"] == "claude-haiku-4-5"
    assert cfg.tester_by_risk["HIGH"] == "claude-sonnet-4-6"


def test_load_config_default_tester_by_risk(tmp_path):
    """AC1: when agent_config.tester.by_risk is absent, SprintConfig uses the
    standard LOW/MEDIUM/HIGH defaults."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)

    assert hasattr(cfg, "tester_by_risk")
    assert "LOW" in cfg.tester_by_risk
    assert "MEDIUM" in cfg.tester_by_risk
    assert "HIGH" in cfg.tester_by_risk
    # Default: HIGH maps to a Sonnet variant
    assert "sonnet" in cfg.tester_by_risk["HIGH"]
    # Default: LOW maps to a Haiku variant
    assert "haiku" in cfg.tester_by_risk["LOW"].lower()


# ── AC2: pre-dispatch risk classifier ─────────────────────────────────────────

def test_classify_risk_security_label_is_high():
    """AC2: a ticket with a security label classifies as HIGH."""
    risk = sm._classify_risk_tier(labels=["security", "enhancement"])
    assert risk == "HIGH"


def test_classify_risk_security_sensitive_label_is_high():
    """AC2: security-sensitive label → HIGH."""
    risk = sm._classify_risk_tier(labels=["security-sensitive"])
    assert risk == "HIGH"


def test_classify_risk_no_elevating_labels_is_low():
    """AC2: no risk-elevating labels and small diff → LOW."""
    risk = sm._classify_risk_tier(labels=["enhancement", "size-S"])
    assert risk == "LOW"


def test_classify_risk_large_diff_is_medium_or_higher():
    """AC2: large diff size elevates risk to at least MEDIUM."""
    risk = sm._classify_risk_tier(labels=["enhancement"], diff_lines=600)
    assert risk in ("MEDIUM", "HIGH")


def test_classify_risk_security_path_is_high():
    """AC2: touching security-sensitive file paths → HIGH."""
    risk = sm._classify_risk_tier(
        labels=["enhancement"],
        paths_touched=["apps/dashboard/auth.py", "services/oauth.py"],
    )
    assert risk == "HIGH"


def test_classify_risk_auth_path_is_high():
    """AC2: touching auth paths → HIGH."""
    risk = sm._classify_risk_tier(labels=[], paths_touched=["apps/auth/handler.py"])
    assert risk == "HIGH"


def test_classify_risk_empty_inputs_is_low():
    """AC2: no labels, no diff, no paths → LOW (safe default)."""
    risk = sm._classify_risk_tier(labels=[])
    assert risk == "LOW"


# ── AC3: pre-dispatch risk injected into tester prompt ───────────────────────

def test_dispatch_tester_injects_risk_hint_into_prompt(tmp_path):
    """AC3: the tester subprocess command includes the pre-dispatch risk tier hint."""
    cfg = MagicMock()
    cfg.tester_model = "claude-haiku-4-5"
    cfg.tester_by_risk = {"LOW": "claude-haiku-4-5", "MEDIUM": "claude-haiku-4-5", "HIGH": "claude-sonnet-4-6"}
    cfg.tester_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_tester_app = tmp_path
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()

    captured_cmd: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "agent_browser_runner"), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)):
        sm._dispatch_tester(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=cfg,
            sprint_label="sprint-1", pre_dispatch_risk="LOW",
        )

    full_prompt = " ".join(captured_cmd)
    assert "risk" in full_prompt.lower() or "LOW" in full_prompt, (
        "Pre-dispatch risk tier hint must appear in the tester prompt"
    )


# ── AC4: disagreement logged non-fatally ──────────────────────────────────────

def test_disagreement_logged_when_risk_tiers_differ(tmp_path, capsys):
    """AC4: when log contains tester's derived risk that differs from pre-dispatch,
    a warning is printed; dispatch is not blocked."""
    log_file = tmp_path / "run.log"
    # Tester self-reports MEDIUM but pre-dispatch was HIGH
    log_file.write_text("[risk-tier] MEDIUM\n", encoding="utf-8")

    import io
    out = io.StringIO()
    with patch("sys.stdout", out):
        sm._check_risk_disagreement(
            pre_dispatch_risk="HIGH",
            log_path=log_file,
            issue_num=999,
        )

    output = out.getvalue()
    assert "disagree" in output.lower() or "mismatch" in output.lower() or "conflict" in output.lower(), (
        "Disagreement between pre-dispatch and tester-derived risk must be logged"
    )


def test_no_disagreement_logged_when_risk_tiers_match(tmp_path):
    """AC4: no warning when pre-dispatch and tester-derived risk agree."""
    log_file = tmp_path / "run.log"
    log_file.write_text("[risk-tier] HIGH\n", encoding="utf-8")

    import io
    out = io.StringIO()
    with patch("sys.stdout", out):
        sm._check_risk_disagreement(
            pre_dispatch_risk="HIGH",
            log_path=log_file,
            issue_num=999,
        )

    output = out.getvalue()
    # Should not log any disagreement warning
    assert "disagree" not in output.lower()
    assert "mismatch" not in output.lower()


def test_no_disagreement_when_tester_does_not_self_report(tmp_path):
    """AC4: if the tester log has no [risk-tier] marker, no disagreement is logged."""
    log_file = tmp_path / "run.log"
    log_file.write_text("Everything passed.\n", encoding="utf-8")

    import io
    out = io.StringIO()
    with patch("sys.stdout", out):
        sm._check_risk_disagreement(
            pre_dispatch_risk="HIGH",
            log_path=log_file,
            issue_num=999,
        )

    output = out.getvalue()
    assert "disagree" not in output.lower()


# ── AC5: agent_runs records risk_tier and model_used ─────────────────────────

def test_agent_runs_has_risk_tier_and_model_used_columns(fresh_db):
    """AC5: agent_runs table has risk_tier and model_used columns."""
    with fresh_db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
    assert "risk_tier" in cols, "agent_runs must have risk_tier column"
    assert "model_used" in cols, "agent_runs must have model_used column"


def test_record_agent_start_stores_risk_tier_and_model(fresh_db):
    """AC5: record_agent_start persists risk_tier and model_used for tester rows."""
    fresh_db.record_agent_start(
        790, "sprint-59", "tester",
        risk_tier="HIGH",
        model_used="claude-sonnet-4-6",
    )
    rows = fresh_db.agent_runs_for_issue(790, "sprint-59")
    assert len(rows) == 1
    r = rows[0]
    assert r["risk_tier"] == "HIGH"
    assert r["model_used"] == "claude-sonnet-4-6"


def test_record_agent_start_allows_null_risk_tier(fresh_db):
    """AC5: risk_tier / model_used are nullable — existing agents without risk
    routing still work."""
    fresh_db.record_agent_start(790, "sprint-59", "coder")
    rows = fresh_db.agent_runs_for_issue(790, "sprint-59")
    assert rows[0]["risk_tier"] is None
    assert rows[0]["model_used"] is None


# ── AC6: security label → sonnet-4-6 ──────────────────────────────────────────

def test_security_label_dispatches_sonnet(tmp_path):
    """AC6: a ticket labelled 'security' selects sonnet-4-6 for the tester."""
    cfg = MagicMock()
    cfg.tester_model = "claude-haiku-4-5"
    cfg.tester_by_risk = {
        "LOW": "claude-haiku-4-5",
        "MEDIUM": "claude-haiku-4-5",
        "HIGH": "claude-sonnet-4-6",
    }
    cfg.tester_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_tester_app = tmp_path
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()

    captured_cmd: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "agent_browser_runner"), \
         patch.object(sm, "_get_issue_labels", return_value={"security", "enhancement"}), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)):
        sm._dispatch_tester(
            42, [], sprint_branch="develop", repo_name="test/repo", cfg=cfg,
            sprint_label="sprint-1",
            # No pre_dispatch_risk — let dispatch compute it internally from labels
        )

    assert "claude-sonnet-4-6" in captured_cmd, (
        "Security-labelled ticket must dispatch tester on sonnet-4-6"
    )


# ── AC7: LOW-risk → haiku-4-5 ─────────────────────────────────────────────────

def test_low_risk_dispatches_haiku(tmp_path):
    """AC7: a LOW-risk ticket (no elevating labels) dispatches tester on haiku-4-5."""
    cfg = MagicMock()
    cfg.tester_model = "claude-haiku-4-5"
    cfg.tester_by_risk = {
        "LOW": "claude-haiku-4-5",
        "MEDIUM": "claude-haiku-4-5",
        "HIGH": "claude-sonnet-4-6",
    }
    cfg.tester_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_tester_app = tmp_path
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()

    captured_cmd: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "agent_browser_runner"), \
         patch.object(sm, "_get_issue_labels", return_value={"enhancement", "size-S"}), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)):
        sm._dispatch_tester(
            99, [], sprint_branch="develop", repo_name="test/repo", cfg=cfg,
            sprint_label="sprint-1",
        )

    assert "claude-haiku-4-5" in captured_cmd, (
        "LOW-risk ticket must dispatch tester on haiku-4-5"
    )
    assert "claude-sonnet-4-6" not in captured_cmd, (
        "LOW-risk ticket must NOT use sonnet-4-6"
    )

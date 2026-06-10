"""Tests for issue #789: Route coder model by ticket size; add pre-dispatch doctor.

AC items verified:
  AC1  sprint.yaml supports agent_config.coder.by_size mapping (S/M/L/XL → model)
       when present it overrides the flat agent_config.coder.model
  AC2  Default routing: S → claude-haiku-4-5, M/L/XL → claude-sonnet-4-6
  AC3  Unestimated ticket falls back to flat default model; routing_reason = "unestimated:default"
  AC4  Each dispatch writes model and routing_reason to agent_runs
  AC5  Doctor runs before every dispatch and checks: CLI present, auth alive,
       worktree path exists, disk space above threshold
  AC6  Doctor failure raises dispatch-blocked ntfy alert and halts dispatch;
       no worker process is spawned
  AC7  Doctor passes silently when environment is healthy
  AC8  Per-size override in sprint.yaml takes effect without code changes
"""
from __future__ import annotations

import os
import sys
import textwrap
import shutil
import sqlite3
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
    db_file = tmp_path / "test_789.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def minimal_cfg(tmp_path):
    """Minimal SprintConfig with worktree dirs that exist."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()
    tester_dir = tmp_path / "tester"
    tester_dir.mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {coder_dir}
          tester: {tester_dir}
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)
    return sm.load_config(config_path)


# ── AC1: agent_config.coder.by_size parsed from sprint.yaml ──────────────────

def test_load_config_reads_coder_by_size(tmp_path):
    """AC1: load_config parses agent_config.coder.by_size from YAML."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
        agent_config:
          coder:
            by_size:
              S: claude-haiku-4-5
              M: claude-sonnet-4-6
              L: claude-sonnet-4-6
              XL: claude-sonnet-4-6
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)

    assert hasattr(cfg, "coder_by_size"), "SprintConfig must have coder_by_size attribute"
    assert cfg.coder_by_size["S"] == "claude-haiku-4-5"
    assert cfg.coder_by_size["M"] == "claude-sonnet-4-6"
    assert cfg.coder_by_size["L"] == "claude-sonnet-4-6"
    assert cfg.coder_by_size["XL"] == "claude-sonnet-4-6"


def test_load_config_coder_by_size_overrides_flat_model(tmp_path):
    """AC1: coder.by_size overrides the flat coder_model for size-matched tickets."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
        agent_config:
          coder_model: claude-sonnet-4-6
          coder:
            by_size:
              S: claude-haiku-4-5
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)

    estimate = {"size": "S"}
    model, reason = sm._resolve_coder_model(1, cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"S ticket must use haiku, got {model!r}"
    assert reason == "size=S"


def test_load_config_default_coder_by_size_present_when_no_override(tmp_path):
    """AC1: when by_size is absent, SprintConfig still has coder_by_size defaults."""
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

    assert hasattr(cfg, "coder_by_size")
    assert "S" in cfg.coder_by_size
    assert "M" in cfg.coder_by_size
    assert "L" in cfg.coder_by_size
    assert "XL" in cfg.coder_by_size


# ── AC2: default routing S → haiku, M/L/XL → sonnet ─────────────────────────

def test_resolve_coder_model_s_uses_haiku_by_default(minimal_cfg):
    """AC2: S-sized ticket routes to haiku when no YAML override."""
    estimate = {"size": "S"}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"S must use haiku by default, got {model!r}"
    assert reason == "size=S"


def test_resolve_coder_model_m_uses_sonnet_by_default(minimal_cfg):
    """AC2: M-sized ticket routes to sonnet by default."""
    estimate = {"size": "M"}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), f"M must use sonnet by default, got {model!r}"
    assert reason == "size=M"


def test_resolve_coder_model_l_uses_sonnet_by_default(minimal_cfg):
    """AC2: L-sized ticket routes to sonnet by default."""
    estimate = {"size": "L"}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), f"L must use sonnet by default, got {model!r}"
    assert reason == "size=L"


def test_resolve_coder_model_xl_uses_sonnet_by_default(minimal_cfg):
    """AC2: XL-sized ticket routes to sonnet by default."""
    estimate = {"size": "XL"}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), f"XL must use sonnet by default, got {model!r}"
    assert reason == "size=XL"


# ── AC3: unestimated fallback ─────────────────────────────────────────────────

def test_resolve_coder_model_no_estimate_uses_flat_default(minimal_cfg):
    """AC3: ticket with no estimate falls back to flat default model."""
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=None)
    assert model == minimal_cfg.coder_model
    assert reason == "unestimated:default"


def test_resolve_coder_model_missing_size_field_uses_flat_default(minimal_cfg):
    """AC3: estimate dict without 'size' field falls back to flat default."""
    estimate = {"confidence": "low", "files_likely_affected": []}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    assert model == minimal_cfg.coder_model
    assert reason == "unestimated:default"


def test_resolve_coder_model_none_cfg_uses_hardcoded_default():
    """AC3: cfg=None falls back to hardcoded sonnet default."""
    model, reason = sm._resolve_coder_model(42, None, estimate=None)
    assert "sonnet" in model.lower(), f"cfg=None should produce sonnet default, got {model!r}"
    assert reason == "unestimated:default"


def test_resolve_coder_model_no_crash_on_unexpected_size(minimal_cfg):
    """AC3: unexpected size string falls back to flat default without crashing."""
    estimate = {"size": "GINORMOUS"}
    model, reason = sm._resolve_coder_model(42, minimal_cfg, estimate=estimate)
    # Should not crash and should return the flat default
    assert model == minimal_cfg.coder_model
    assert reason == "unestimated:default"


# ── AC4: routing_reason written to agent_runs ─────────────────────────────────

def test_record_agent_start_accepts_routing_reason(fresh_db, tmp_path):
    """AC4: record_agent_start stores routing_reason in agent_runs."""
    fresh_db.record_agent_start(
        issue_number=42,
        sprint_label="sprint-1",
        agent="coder",
        model_used="claude-haiku-4-5",
        routing_reason="size=S",
    )

    with fresh_db.get_conn() as conn:
        row = conn.execute(
            "SELECT model_used, routing_reason FROM agent_runs "
            "WHERE issue_number = 42 AND agent = 'coder'"
        ).fetchone()

    assert row is not None, "agent_runs row must exist after record_agent_start"
    assert row[0] == "claude-haiku-4-5", f"model_used mismatch: {row[0]!r}"
    assert row[1] == "size=S", f"routing_reason mismatch: {row[1]!r}"


def test_record_agent_start_routing_reason_null_for_legacy_calls(fresh_db):
    """AC4: routing_reason is NULL when not provided (backward compat)."""
    fresh_db.record_agent_start(
        issue_number=99,
        sprint_label="sprint-1",
        agent="coder",
    )

    with fresh_db.get_conn() as conn:
        row = conn.execute(
            "SELECT routing_reason FROM agent_runs WHERE issue_number = 99"
        ).fetchone()

    assert row is not None
    assert row[0] is None


def test_db_agent_start_sm_passes_routing_reason(fresh_db):
    """AC4: _db_agent_start_sm forwards routing_reason to record_agent_start."""
    with patch.object(_db_module, "record_agent_start") as mock_start:
        sm._db_agent_start_sm(
            42, "sprint-1", "coder",
            model_used="claude-haiku-4-5",
            routing_reason="size=S",
        )
    mock_start.assert_called_once()
    kwargs = mock_start.call_args.kwargs
    assert kwargs.get("routing_reason") == "size=S", (
        f"routing_reason not forwarded: {mock_start.call_args}"
    )


def test_db_agent_start_sm_called_with_model_and_routing_for_coder(fresh_db, tmp_path):
    """AC4: _db_agent_start_sm records model_used and routing_reason for a coder dispatch."""
    # Call _db_agent_start_sm directly (it is called by the sprint loop before _dispatch_coder).
    sm._db_agent_start_sm(
        42, "sprint-test", "coder",
        model_used="claude-haiku-4-5",
        routing_reason="size=S",
    )

    with fresh_db.get_conn() as conn:
        row = conn.execute(
            "SELECT model_used, routing_reason FROM agent_runs "
            "WHERE issue_number = 42 AND agent = 'coder'"
        ).fetchone()

    assert row is not None, "agent_runs row must exist"
    assert row[0] == "claude-haiku-4-5", f"model_used mismatch: {row[0]!r}"
    assert row[1] == "size=S", f"routing_reason mismatch: {row[1]!r}"


def test_dispatch_coder_resolves_model_for_s_ticket(tmp_path):
    """AC4: _dispatch_coder uses _resolve_coder_model to select haiku for S-ticket."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()
    (coder_dir / "PRODUCT.md").write_text("product")
    (coder_dir / "DESIGN.md").write_text("design")

    cfg = MagicMock()
    cfg.coder_model = "claude-sonnet-4-6"
    cfg.coder_by_size = {"S": "claude-haiku-4-5", "M": "claude-sonnet-4-6", "L": "claude-sonnet-4-6", "XL": "claude-sonnet-4-6"}
    cfg.coder_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_coder = coder_dir
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()

    captured_cmd: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    estimate = {"size": "S", "confidence": "high"}

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_load_estimate", return_value=estimate), \
         patch.object(sm, "_dispatch_doctor", return_value=None):
        sm._dispatch_coder(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=cfg,
            sprint_label="sprint-1",
        )

    # The --model flag in the cmd should be claude-haiku-4-5 for an S ticket
    model_idx = captured_cmd.index("--model") if "--model" in captured_cmd else -1
    assert model_idx >= 0, "--model flag must appear in subprocess command"
    selected_model = captured_cmd[model_idx + 1]
    assert "haiku" in selected_model.lower(), (
        f"S ticket must use haiku model, got {selected_model!r}"
    )


# ── AC5: doctor check functions exist and cover all four checks ───────────────

def test_dispatch_doctor_exists():
    """AC5: _dispatch_doctor function exists in sprint_manager."""
    assert hasattr(sm, "_dispatch_doctor"), "_dispatch_doctor must be defined in sprint_manager"
    assert callable(sm._dispatch_doctor)


def test_doctor_probe_auth_exists():
    """AC5: _doctor_probe_auth function exists for the auth alive check."""
    assert hasattr(sm, "_doctor_probe_auth"), "_doctor_probe_auth must be defined in sprint_manager"
    assert callable(sm._doctor_probe_auth)


def test_doctor_passes_silently_with_healthy_env(tmp_path):
    """AC5/AC7: doctor returns None (no error) in a healthy environment."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir

    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(sm, "_doctor_probe_auth", return_value=None), \
         patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = MagicMock(free=10 * 1024 ** 3)  # 10 GB free
        result = sm._dispatch_doctor(cfg, alert_modes=[])

    assert result is None, f"Doctor must return None when healthy, got {result!r}"


def test_doctor_fails_when_cli_missing(tmp_path):
    """AC5: doctor returns error string when claude CLI is not found."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir

    with patch("shutil.which", return_value=None):
        result = sm._dispatch_doctor(cfg, alert_modes=[])

    assert result is not None, "Doctor must return error when CLI is missing"
    assert "claude" in result.lower() or "cli" in result.lower(), (
        f"Error must mention claude CLI, got {result!r}"
    )


def test_doctor_fails_when_auth_probe_fails(tmp_path):
    """AC5: doctor returns error when auth probe fails."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir

    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(sm, "_doctor_probe_auth", return_value="auth failed: bad credentials"):
        result = sm._dispatch_doctor(cfg, alert_modes=[])

    assert result is not None, "Doctor must return error when auth probe fails"
    assert "auth" in result.lower(), f"Error must mention auth, got {result!r}"


def test_doctor_fails_when_worktree_missing(tmp_path):
    """AC5: doctor returns error when coder worktree path does not exist."""
    missing_dir = tmp_path / "nonexistent_coder"

    cfg = MagicMock()
    cfg.worktree_coder = missing_dir

    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(sm, "_doctor_probe_auth", return_value=None):
        result = sm._dispatch_doctor(cfg, alert_modes=[])

    assert result is not None, "Doctor must return error when worktree is missing"
    assert "worktree" in result.lower() or "path" in result.lower() or str(missing_dir) in result, (
        f"Error must mention the missing worktree, got {result!r}"
    )


def test_doctor_fails_when_disk_space_low(tmp_path):
    """AC5: doctor returns error when free disk space is below threshold."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir

    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(sm, "_doctor_probe_auth", return_value=None), \
         patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = MagicMock(free=100 * 1024 * 1024)  # only 100 MB free
        result = sm._dispatch_doctor(cfg, alert_modes=[])

    assert result is not None, "Doctor must return error when disk space is too low"
    assert "disk" in result.lower() or "space" in result.lower(), (
        f"Error must mention disk space, got {result!r}"
    )


# ── AC6: doctor failure fires dispatch-blocked alert; no worker spawned ───────

def test_dispatch_blocked_alert_fires_on_cli_missing(tmp_path):
    """AC6: when CLI is missing, dispatch-blocked alert is fired."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir
    cfg.api_url = None
    cfg.alerts_dir = tmp_path / "alerts"

    alert_calls: list = []

    def fake_dispatch_alerts(modes, title, body, **kwargs):
        alert_calls.append({"title": title, "body": body, "kwargs": kwargs})

    with patch("shutil.which", return_value=None), \
         patch.object(sm, "dispatch_alerts", side_effect=fake_dispatch_alerts):
        sm._dispatch_doctor(cfg, alert_modes=["ntfy"])

    assert len(alert_calls) > 0, "An alert must be dispatched when doctor fails"
    assert any(
        "dispatch-blocked" in (c["title"] + c["body"]).lower()
        or "dispatch_blocked" in (c["title"] + c["body"]).lower()
        for c in alert_calls
    ), f"Alert title/body must mention dispatch-blocked, got {alert_calls}"


def test_dispatch_coder_halted_when_doctor_fails(tmp_path):
    """AC6: _dispatch_coder returns False without spawning subprocess when doctor fails."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()
    (coder_dir / "PRODUCT.md").write_text("product")
    (coder_dir / "DESIGN.md").write_text("design")

    cfg = MagicMock()
    cfg.coder_model = "claude-sonnet-4-6"
    cfg.coder_by_size = {}
    cfg.coder_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_coder = coder_dir
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()

    with patch.object(sm, "_dispatch_doctor", return_value="dispatch-blocked: CLI missing"), \
         patch.object(sm, "_post_agent_event"), \
         patch("subprocess.Popen") as mock_popen:
        ok, category = sm._dispatch_coder(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=cfg,
            sprint_label="sprint-1",
        )

    assert ok is False, "Dispatch must fail when doctor returns an error"
    mock_popen.assert_not_called(), "subprocess.Popen must NOT be called when doctor fails"


# ── AC7: doctor passes silently (no output, no alert) on healthy env ──────────

def test_doctor_no_alert_on_healthy_env(tmp_path):
    """AC7: no alert is dispatched when all doctor checks pass."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()

    cfg = MagicMock()
    cfg.worktree_coder = coder_dir

    alert_calls: list = []

    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(sm, "_doctor_probe_auth", return_value=None), \
         patch("shutil.disk_usage") as mock_du, \
         patch.object(sm, "dispatch_alerts", side_effect=lambda *a, **kw: alert_calls.append(1)):
        mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
        result = sm._dispatch_doctor(cfg, alert_modes=["ntfy"])

    assert result is None
    assert len(alert_calls) == 0, "Doctor must not fire any alert when environment is healthy"


# ── AC8: per-size YAML override takes effect ──────────────────────────────────

def test_yaml_by_size_override_s_to_sonnet(tmp_path):
    """AC8: sprint.yaml override of S → sonnet is respected without code changes."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
        agent_config:
          coder:
            by_size:
              S: claude-sonnet-4-6
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)
    estimate = {"size": "S"}
    model, reason = sm._resolve_coder_model(1, cfg, estimate=estimate)

    assert "sonnet" in model.lower(), (
        f"YAML override S → sonnet must be respected, got {model!r}"
    )
    assert reason == "size=S"


def test_yaml_by_size_partial_override_only_overrides_specified_keys(tmp_path):
    """AC8: partial YAML override only changes the specified sizes; others keep defaults."""
    (tmp_path / "coder").mkdir()
    (tmp_path / "tester").mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {tmp_path}/coder
          tester: {tmp_path}/tester
        agent_config:
          coder:
            by_size:
              S: claude-sonnet-4-6
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)

    cfg = sm.load_config(config_path)

    # S overridden to sonnet
    model_s, _ = sm._resolve_coder_model(1, cfg, estimate={"size": "S"})
    assert "sonnet" in model_s.lower()

    # L keeps its default (sonnet, which is unchanged; but the key is it's still set)
    model_l, _ = sm._resolve_coder_model(1, cfg, estimate={"size": "L"})
    assert model_l is not None, "L size must still resolve to a model after partial override"

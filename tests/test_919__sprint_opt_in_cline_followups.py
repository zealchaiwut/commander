"""Tests for issue #919: Sprint opt-in for Cline follow-up coder fixes.

AC1 — use_cline_followups boolean in sprint record, defaults to false
AC2 — Run-sprint Board shows checkbox for Cline opt-in (frontend-only, no backend test)
AC3 — Checkbox persists use_cline_followups: true to sprint record only
AC4 — When flag=true, follow-up coder dispatches in that sprint route to Cline
AC5 — When flag=false, all coder dispatches use Claude Code
AC6 — Running view coder nodes display CLINE or CLAUDE badge (IssueState serialization)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, backend: str = "claude-code") -> MagicMock:
    """Minimal SprintConfig stub for dispatch tests."""
    (tmp_path / "PRODUCT.md").write_text("# Product")
    (tmp_path / "DESIGN.md").write_text("# Design")
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock()
    cfg.coder_backend = backend
    cfg.coder_model = "claude-sonnet-4-6"
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
    cfg.sprints_dir = sprints_dir
    return cfg


def _write_plan_json(sprints_dir: Path, label: str, data: dict) -> None:
    """Write a plan.json for the given sprint label."""
    path = sprints_dir / f"{label}-plan.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_dispatch(
    cfg: MagicMock,
    issue_num: int = 1,
    sprint_label: str = "sprint-1",
    prior_failures=None,
) -> tuple[bool, object, list[str]]:
    """Run _dispatch_coder with all I/O mocked. Returns (ok, category, cmd)."""
    captured_cmd: list[str] = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_dispatch_doctor", return_value=None),
        patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
        patch.object(sm, "_crg_update_worktree"),
        patch.object(sm, "_load_agent_persona", return_value=None),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_build_failure_suffix", return_value=""),
        patch.object(sm, "HangDetector", return_value=MagicMock(killed=False)),
        patch.dict("os.environ", {}, clear=True),
    ):
        ok, cat = sm._dispatch_coder(
            issue_num, [], sprint_branch="develop", cfg=cfg,
            sprint_label=sprint_label, prior_failures=prior_failures,
        )

    return ok, cat, captured_cmd


# ---------------------------------------------------------------------------
# AC1 — use_cline_followups in sprint record, defaults to false
# ---------------------------------------------------------------------------

class TestAC1UseClindFollowupsDefault:
    def test_sprint_mgmt_run_body_accepts_use_cline_followups(self):
        """SprintMgmtRunBody must accept use_cline_followups field."""
        import server  # noqa: PLC0415
        body = server.SprintMgmtRunBody(
            project="test/repo",
            sprint_label="sprint-1",
            use_cline_followups=True,
        )
        assert body.use_cline_followups is True

    def test_sprint_mgmt_run_body_defaults_to_false(self):
        """SprintMgmtRunBody.use_cline_followups must default to False."""
        import server  # noqa: PLC0415
        body = server.SprintMgmtRunBody(
            project="test/repo",
            sprint_label="sprint-1",
        )
        assert body.use_cline_followups is False, (
            f"use_cline_followups must default to False, got {body.use_cline_followups!r}"
        )


# ---------------------------------------------------------------------------
# AC3 — plan.json persistence
# ---------------------------------------------------------------------------

class TestAC3PlanJsonPersistence:
    def test_plan_json_use_cline_followups_reads_true(self, tmp_path):
        """_plan_json_use_cline_followups returns True when plan.json has the flag."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-5", {"use_cline_followups": True, "state": "running"})

        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir

        result = sm._plan_json_use_cline_followups("sprint-5", cfg)
        assert result is True, f"Expected True, got {result!r}"

    def test_plan_json_use_cline_followups_reads_false(self, tmp_path):
        """_plan_json_use_cline_followups returns False when plan.json has flag=false."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-5", {"use_cline_followups": False, "state": "running"})

        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir

        result = sm._plan_json_use_cline_followups("sprint-5", cfg)
        assert result is False

    def test_plan_json_use_cline_followups_absent_returns_false(self, tmp_path):
        """_plan_json_use_cline_followups returns False when field is absent."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-5", {"state": "running"})

        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir

        result = sm._plan_json_use_cline_followups("sprint-5", cfg)
        assert result is False

    def test_plan_json_use_cline_followups_missing_file_returns_false(self, tmp_path):
        """_plan_json_use_cline_followups returns False when plan.json doesn't exist."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)

        cfg = MagicMock()
        cfg.sprints_dir = sprints_dir

        result = sm._plan_json_use_cline_followups("sprint-99", cfg)
        assert result is False


# ---------------------------------------------------------------------------
# AC4 — When flag=true, follow-up dispatches route to Cline
# ---------------------------------------------------------------------------

class TestAC4FollowUpRoutesToCline:
    def test_effective_backend_followup_with_flag_returns_cline(self, tmp_path):
        """_effective_coder_backend with prior_failures + flag=true returns 'cline'."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-1", {"use_cline_followups": True})

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.sprints_dir = sprints_dir

        result = sm._effective_coder_backend(
            sprint_label="sprint-1",
            cfg=cfg,
            prior_failures=[{"attempt": 1, "category": "crash"}],
        )
        assert result == "cline", (
            f"Expected 'cline' for follow-up with use_cline_followups=True, got {result!r}"
        )

    def test_dispatch_coder_followup_uses_cline_binary(self, tmp_path):
        """_dispatch_coder routes to cline binary for follow-up when use_cline_followups=true."""
        cfg = _make_cfg(tmp_path)
        _write_plan_json(cfg.sprints_dir, "sprint-1", {"use_cline_followups": True})

        ok, cat, cmd = _run_dispatch(
            cfg, sprint_label="sprint-1",
            prior_failures=[{"attempt": 1, "category": "crash"}],
        )

        assert ok is True
        assert cmd[0] == "cline", (
            f"Expected 'cline' as first cmd element for follow-up dispatch, got {cmd[0]!r}"
        )

    def test_dispatch_coder_followup_uses_yolo_flag(self, tmp_path):
        """_dispatch_coder follow-up via Cline includes -y (yolo) flag."""
        cfg = _make_cfg(tmp_path)
        _write_plan_json(cfg.sprints_dir, "sprint-1", {"use_cline_followups": True})

        ok, cat, cmd = _run_dispatch(
            cfg, sprint_label="sprint-1",
            prior_failures=[{"attempt": 1, "category": "crash"}],
        )

        assert "-y" in cmd, f"Expected '-y' in cmd, got {cmd}"


# ---------------------------------------------------------------------------
# AC5 — When flag=false, all dispatches use Claude Code
# ---------------------------------------------------------------------------

class TestAC5AllDispatchesUseClaudeWhenFlagFalse:
    def test_effective_backend_initial_dispatch_uses_claude_even_with_flag(self, tmp_path):
        """Initial dispatch (no prior_failures) always uses cfg.coder_backend, not Cline."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-1", {"use_cline_followups": True})

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.sprints_dir = sprints_dir

        result = sm._effective_coder_backend(
            sprint_label="sprint-1",
            cfg=cfg,
            prior_failures=None,
        )
        assert result == "claude-code", (
            f"Initial dispatch must use claude-code regardless of flag, got {result!r}"
        )

    def test_effective_backend_empty_prior_failures_uses_claude(self, tmp_path):
        """Empty list prior_failures is treated as no prior failures → claude-code."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-1", {"use_cline_followups": True})

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.sprints_dir = sprints_dir

        result = sm._effective_coder_backend(
            sprint_label="sprint-1",
            cfg=cfg,
            prior_failures=[],
        )
        assert result == "claude-code"

    def test_effective_backend_followup_without_flag_uses_claude(self, tmp_path):
        """Follow-up dispatch without use_cline_followups in plan.json uses claude-code."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        _write_plan_json(sprints_dir, "sprint-1", {"use_cline_followups": False})

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.sprints_dir = sprints_dir

        result = sm._effective_coder_backend(
            sprint_label="sprint-1",
            cfg=cfg,
            prior_failures=[{"attempt": 1, "category": "crash"}],
        )
        assert result == "claude-code", (
            f"Expected 'claude-code' when flag=false, got {result!r}"
        )

    def test_dispatch_coder_initial_uses_claude_binary(self, tmp_path):
        """Initial _dispatch_coder (no prior_failures) uses 'claude' binary."""
        cfg = _make_cfg(tmp_path)
        _write_plan_json(cfg.sprints_dir, "sprint-1", {"use_cline_followups": True})

        ok, cat, cmd = _run_dispatch(
            cfg, sprint_label="sprint-1",
            prior_failures=None,
        )

        assert ok is True
        assert cmd[0] == "claude", (
            f"Expected 'claude' for initial dispatch, got {cmd[0]!r}"
        )

    def test_dispatch_coder_followup_without_flag_uses_claude_binary(self, tmp_path):
        """Follow-up dispatch without flag set uses 'claude' binary."""
        cfg = _make_cfg(tmp_path)
        _write_plan_json(cfg.sprints_dir, "sprint-1", {"use_cline_followups": False})

        ok, cat, cmd = _run_dispatch(
            cfg, sprint_label="sprint-1",
            prior_failures=[{"attempt": 1, "category": "crash"}],
        )

        assert ok is True
        assert cmd[0] == "claude", (
            f"Expected 'claude' when flag=false, got {cmd[0]!r}"
        )


# ---------------------------------------------------------------------------
# AC6 — IssueState serialization for CLINE/CLAUDE badge
# ---------------------------------------------------------------------------

class TestAC6IssueStateBackendField:
    def test_issue_state_has_coder_backend_field(self):
        """IssueState must have a coder_backend attribute."""
        ist = sm.IssueState(number=1, title="Test")
        assert hasattr(ist, "coder_backend"), "IssueState must have coder_backend field"

    def test_issue_state_coder_backend_defaults_to_none(self):
        """IssueState.coder_backend defaults to None."""
        ist = sm.IssueState(number=1, title="Test")
        assert ist.coder_backend is None

    def test_issue_state_to_dict_includes_coder_backend(self):
        """to_dict() must include coder_backend so it persists to state.json."""
        ist = sm.IssueState(number=1, title="Test")
        ist.coder_backend = "cline"
        d = ist.to_dict()
        assert "coder_backend" in d, f"to_dict() must include 'coder_backend', got keys: {list(d.keys())}"
        assert d["coder_backend"] == "cline"

    def test_issue_state_to_dict_coder_backend_null_when_not_set(self):
        """to_dict() includes coder_backend=None when not dispatched yet."""
        ist = sm.IssueState(number=1, title="Test")
        d = ist.to_dict()
        assert "coder_backend" in d
        assert d["coder_backend"] is None

    def test_issue_state_from_dict_reads_coder_backend(self):
        """from_dict() must read coder_backend from stored dict."""
        d = {
            "number": 1, "title": "Test", "status": "done",
            "coder_backend": "cline",
        }
        ist = sm.IssueState.from_dict(d)
        assert ist.coder_backend == "cline", (
            f"from_dict() must read coder_backend, got {ist.coder_backend!r}"
        )

    def test_issue_state_from_dict_coder_backend_none_when_absent(self):
        """from_dict() with no coder_backend key defaults to None."""
        d = {"number": 1, "title": "Test", "status": "pending"}
        ist = sm.IssueState.from_dict(d)
        assert ist.coder_backend is None

    def test_issue_state_from_dict_reads_claude_code_backend(self):
        """from_dict() reads 'claude-code' value correctly."""
        d = {
            "number": 2, "title": "Other", "status": "done",
            "coder_backend": "claude-code",
        }
        ist = sm.IssueState.from_dict(d)
        assert ist.coder_backend == "claude-code"

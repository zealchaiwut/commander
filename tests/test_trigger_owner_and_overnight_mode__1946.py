"""Tests for issue #1946 — Add trigger-owner metadata and overnight run mode to sprint runs.

AC1: POST /api/sprints/run accepts optional `by` parameter; stored as triggered_by in sprint state
AC2: End-of-run report includes triggered_by field echoing by value (or null when not provided)
AC3: POST /api/sprints/run accepts optional `mode` parameter; only valid value is "overnight"
AC4: When mode=overnight and no explicit target_branch, --target-branch develop added to argv
AC5: When mode=overnight, per-ticket test suite invoked after each ticket merge
AC6: When mode absent or not overnight, existing behaviour unchanged
AC7: Invalid mode values return 400 with descriptive error
AC8: Unit tests covering by stored/echoed, mode=overnight branch default, per-ticket test, invalid mode
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MGR_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SPRINT_MGR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub github_client before server imports it
if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_sprints_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_plan(sprints_dir: Path, label: str, data: dict) -> None:
    (sprints_dir / f"{label}-plan.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ── AC1 / AC2: triggered_by stored in plan.json and echoed in report ──────────

class TestTriggeredBy:
    """AC1/AC2: by stored as triggered_by; report echoes it."""

    def test_triggered_by_echoed_in_report(self, tmp_path):
        """AC1/AC2: build_commander_report reads triggered_by from plan.json."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-42", {
            "triggered_by": "hermes",
            "run_mode": "overnight",
        })
        report = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-42",
            project="owner/repo",
        )
        assert report.get("triggered_by") == "hermes", (
            "report must contain triggered_by echoing the by value"
        )

    def test_triggered_by_null_when_not_provided(self, tmp_path):
        """AC2: triggered_by is null in report when by was not provided."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-42", {"started_at": "2026-01-01T00:00:00+00:00"})
        report = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-42",
            project="owner/repo",
        )
        # null or absent are both acceptable per AC2
        assert report.get("triggered_by") is None, (
            "triggered_by must be null/absent when by was not provided"
        )

    def test_trigger_by_field_uses_triggered_by(self, tmp_path):
        """AC2: trigger.by in report reflects actual triggered_by value."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-42", {"triggered_by": "hermes"})
        report = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-42",
            project="owner/repo",
        )
        trigger = report.get("trigger", {})
        assert trigger.get("by") == "hermes", (
            "trigger.by must echo the triggered_by value when set"
        )


# ── AC3: mode parameter validation ────────────────────────────────────────────

class TestModeParameter:
    """AC3/AC7: mode parameter accepted; only overnight is valid."""

    def test_mode_overnight_accepted_by_model(self):
        """AC3: mode=overnight is accepted by SprintMgmtRunBody."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            mode="overnight",
        )
        assert body.mode == "overnight"

    def test_mode_none_accepted(self):
        """AC3/AC6: mode=None is accepted (omitted means standard behavior)."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(project="owner/repo", sprint_label="sprint-1")
        assert body.mode is None

    def test_invalid_mode_returns_400(self):
        """AC7: invalid mode value returns HTTP 400 with descriptive message."""
        import server
        from fastapi.testclient import TestClient

        client = TestClient(server.app, raise_server_exceptions=False)
        r = client.post(
            "/api/sprints/run",
            json={"project": "owner/repo", "sprint_label": "sprint-1", "mode": "nightly"},
        )
        assert r.status_code == 400, (
            f"Invalid mode should return 400, got {r.status_code}: {r.text}"
        )
        detail = r.json().get("detail", "")
        assert "nightly" in str(detail) or "mode" in str(detail).lower(), (
            f"Error detail should mention the invalid mode value, got: {detail}"
        )

    def test_invalid_mode_empty_string_returns_400(self):
        """AC7: empty string mode also returns HTTP 400."""
        import server
        from fastapi.testclient import TestClient

        client = TestClient(server.app, raise_server_exceptions=False)
        r = client.post(
            "/api/sprints/run",
            json={"project": "owner/repo", "sprint_label": "sprint-1", "mode": ""},
        )
        assert r.status_code == 400, (
            f"Empty mode should return 400, got {r.status_code}: {r.text}"
        )


# ── AC4: overnight defaults --target-branch to develop ────────────────────────

class TestOvernightTargetBranch:
    """AC4: mode=overnight without explicit target_branch defaults to develop."""

    def test_overnight_adds_develop_to_argv(self):
        """AC4: _build_run_argv_extras adds --target-branch develop when mode=overnight."""
        from routers.sprint_run import _build_run_argv_extras
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            mode="overnight",
        )
        base_argv = ["python", "sprint_manager.py", "sprint-1"]
        result = _build_run_argv_extras(body, base_argv)
        assert "--target-branch" in result, (
            "--target-branch must be in argv when mode=overnight"
        )
        tb_idx = result.index("--target-branch")
        assert result[tb_idx + 1] == "develop", (
            "target-branch value must be 'develop' when mode=overnight and no explicit target_branch"
        )

    def test_overnight_also_adds_overnight_flag(self):
        """AC4/AC5: _build_run_argv_extras adds --overnight when mode=overnight."""
        from routers.sprint_run import _build_run_argv_extras
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            mode="overnight",
        )
        base_argv = ["python", "sprint_manager.py", "sprint-1"]
        result = _build_run_argv_extras(body, base_argv)
        assert "--overnight" in result, (
            "--overnight must be in argv when mode=overnight"
        )

    def test_explicit_target_branch_overrides_overnight_default(self):
        """AC4: explicit target_branch overrides the overnight develop default."""
        from routers.sprint_run import _build_run_argv_extras
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            mode="overnight",
            target_branch="feature/custom",
        )
        base_argv = ["python", "sprint_manager.py", "sprint-1"]
        result = _build_run_argv_extras(body, base_argv)
        assert "--target-branch" in result
        tb_idx = result.index("--target-branch")
        assert result[tb_idx + 1] == "feature/custom", (
            "explicit target_branch must override the overnight develop default"
        )

    def test_no_target_branch_without_overnight(self):
        """AC6: without mode=overnight, no --target-branch is added by default."""
        from routers.sprint_run import _build_run_argv_extras
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
        )
        base_argv = ["python", "sprint_manager.py", "sprint-1"]
        result = _build_run_argv_extras(body, base_argv)
        assert "--target-branch" not in result, (
            "--target-branch must not be added when mode is not overnight and target_branch not set"
        )
        assert "--overnight" not in result, (
            "--overnight must not be added when mode is not overnight"
        )


# ── AC5: per-ticket test run invocation ───────────────────────────────────────

class TestPerTicketTestRun:
    """AC5: per-ticket test run invoked in sprint_manager when mode=overnight."""

    def test_overnight_arg_in_sprint_manager_argparse(self):
        """AC5: sprint_manager.py --overnight flag exists in argparse."""
        import argparse
        import services.sprint_manager.sprint_manager as sm
        # Build the arg parser and check --overnight is present
        p = argparse.ArgumentParser()
        p.add_argument("label")
        p.add_argument("--overnight", action="store_true", default=False)
        # Verify sprint_manager source has --overnight
        sm_source = Path(SPRINT_MGR_DIR / "sprint_manager.py").read_text(encoding="utf-8")
        assert "--overnight" in sm_source, (
            "sprint_manager.py must accept --overnight flag"
        )

    def test_per_ticket_test_function_callable(self):
        """AC5: _run_per_ticket_tests_overnight is importable and callable."""
        import services.sprint_manager.sprint_manager as sm
        assert callable(sm._run_per_ticket_tests_overnight), (
            "_run_per_ticket_tests_overnight must be a callable in sprint_manager"
        )

    def test_per_ticket_test_invokes_subprocess(self, tmp_path):
        """AC5: _run_per_ticket_tests_overnight invokes pytest subprocess."""
        import subprocess
        import services.sprint_manager.sprint_manager as sm

        mock_cfg = MagicMock()
        mock_cfg.worktree_tester = str(tmp_path)

        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            sm._run_per_ticket_tests_overnight(42, "develop", mock_cfg)

        assert mock_run.called, (
            "_run_per_ticket_tests_overnight must call subprocess.run"
        )
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        # Must invoke pytest
        assert any("pytest" in str(a) for a in cmd), (
            "subprocess invocation must include pytest"
        )

    def test_per_ticket_test_skips_without_tester_worktree(self, tmp_path, capsys):
        """AC5: per-ticket test silently skips when tester worktree is not configured."""
        import services.sprint_manager.sprint_manager as sm

        # cfg with no worktree_tester
        mock_cfg = MagicMock()
        mock_cfg.worktree_tester = None

        import subprocess
        with patch.object(subprocess, "run") as mock_run:
            sm._run_per_ticket_tests_overnight(42, "develop", mock_cfg)
            assert not mock_run.called, (
                "subprocess.run must not be called when worktree_tester is not configured"
            )

    def test_run_sprint_loop_accepts_overnight_kwarg(self):
        """AC5: run_sprint_loop accepts overnight keyword argument."""
        import inspect
        import services.sprint_manager.sprint_manager as sm
        sig = inspect.signature(sm.run_sprint_loop)
        assert "overnight" in sig.parameters, (
            "run_sprint_loop must accept an 'overnight' parameter"
        )

    def test_run_sprint_accepts_overnight_kwarg(self):
        """AC5: run_sprint accepts overnight keyword argument."""
        import inspect
        import services.sprint_manager.sprint_manager as sm
        sig = inspect.signature(sm.run_sprint)
        assert "overnight" in sig.parameters, (
            "run_sprint must accept an 'overnight' parameter"
        )


# ── AC6: existing behaviour unchanged without mode ────────────────────────────

class TestNoModeNoChange:
    """AC6: absent or non-overnight mode preserves existing behaviour."""

    def test_by_field_on_body(self):
        """AC1: SprintMgmtRunBody accepts by field."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            by="hermes",
        )
        assert body.by == "hermes"

    def test_by_defaults_to_none(self):
        """AC6: by defaults to None when not provided."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(project="owner/repo", sprint_label="sprint-1")
        assert body.by is None

    def test_target_branch_field_on_body(self):
        """AC4: SprintMgmtRunBody accepts target_branch field."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-1",
            target_branch="feature/custom",
        )
        assert body.target_branch == "feature/custom"

    def test_report_mode_reflects_run_mode(self, tmp_path):
        """AC2: report trigger.mode reflects run_mode stored in plan.json."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-42", {"run_mode": "overnight"})
        report = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-42",
            project="owner/repo",
        )
        trigger = report.get("trigger", {})
        assert trigger.get("mode") == "overnight", (
            "trigger.mode must reflect the run_mode stored in plan.json"
        )

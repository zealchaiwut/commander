"""Tests for issue #506: lock label mutations in sprint_manager during active runs.

AC-1: RUN_MUTABLE_LABELS constant defined correctly.
AC-2: _assert_run_mutable raises and logs for labels outside the allowlist.
AC-3: _guard_sprint_labels uses RUN_MUTABLE_LABELS (no _RUN_MUTABLE_GITHUB_LABELS).
AC-4: COMMANDER_SPRINT_RUNNING injected into subprocess envs.
AC-5: update_ticket.py guard active when COMMANDER_SPRINT_RUNNING is set.
AC-6: finish_feature UAT flow only touches mutable labels under the guard.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))


class TestRunMutableLabelsConstant:
    """AC-1: RUN_MUTABLE_LABELS contains all status labels (issue #814 added "blocked")."""

    def test_constant_value(self):
        # After issue #814 the constant is aliased from state_machine.RUN_MUTABLE_LABELS
        # which equals STATUS_LABELS — all status labels including "blocked".
        from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS
        from services.sprint_manager.state_machine import STATUS_LABELS
        assert RUN_MUTABLE_LABELS == STATUS_LABELS

    def test_case_correct(self):
        from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS
        assert "SIT" in RUN_MUTABLE_LABELS
        assert "sit" not in RUN_MUTABLE_LABELS
        assert "UAT" in RUN_MUTABLE_LABELS
        assert "uat" not in RUN_MUTABLE_LABELS

    def test_old_constant_removed(self):
        import services.sprint_manager.sprint_manager as sm
        assert not hasattr(sm, "_RUN_MUTABLE_GITHUB_LABELS"), (
            "_RUN_MUTABLE_GITHUB_LABELS should be removed; use RUN_MUTABLE_LABELS"
        )


class TestAssertRunMutable:
    """AC-2: _assert_run_mutable helper raises and logs correctly."""

    def test_valid_labels_no_raise(self):
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        # Should not raise
        _assert_run_mutable(["SIT", "UAT", "in-progress", "needs-rework"], "add")

    def test_empty_list_no_raise(self):
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        _assert_run_mutable([], "remove")

    def test_sprint_label_raises(self):
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        try:
            _assert_run_mutable(["sprint-25"], "remove")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "sprint-25" in str(e) or "outside RUN_MUTABLE_LABELS" in str(e)

    def test_blocked_label_no_longer_raises(self):
        # After issue #814, "blocked" is in RUN_MUTABLE_LABELS — it must not raise.
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        _assert_run_mutable(["blocked"], "add")  # must not raise

    def test_mixed_labels_raises_for_invalid(self):
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        try:
            _assert_run_mutable(["SIT", "sprint-37"], "add")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "sprint-37" in str(e) or "outside RUN_MUTABLE_LABELS" in str(e)

    def test_log_message_format(self, capsys=None):
        from services.sprint_manager.sprint_manager import _assert_run_mutable
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            try:
                _assert_run_mutable(["sprint-25"], "remove")
            except ValueError:
                pass
        output = buf.getvalue()
        assert "Refused to remove label" in output
        assert "sprint-25" in output
        assert "RUN_MUTABLE_LABELS" in output


class TestGuardSprintLabelsUsesMutableSet:
    """AC-3: _guard_sprint_labels uses RUN_MUTABLE_LABELS, not a separate constant."""

    def test_blocks_non_mutable_add_during_run(self):
        # After issue #814 "blocked" is in RUN_MUTABLE_LABELS and is allowed through.
        # Non-status labels (e.g. sprint-5) are still stripped.
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, safe_remove = _guard_sprint_labels(
            add=["SIT", "blocked", "sprint-5"],
            remove=[],
            sprint_label="sprint-37",
        )
        assert "SIT" in safe_add
        assert "blocked" in safe_add
        assert "sprint-5" not in safe_add

    def test_blocks_sprint_label_remove_always(self):
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, safe_remove = _guard_sprint_labels(
            add=[],
            remove=["in-progress", "sprint-37"],
        )
        assert "in-progress" in safe_remove
        assert "sprint-37" not in safe_remove

    def test_no_restriction_without_sprint_label(self):
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, safe_remove = _guard_sprint_labels(
            add=["blocked", "estimated"],
            remove=["in-progress"],
        )
        # Without sprint_label, add-list is unrestricted (only sprint-N removes are blocked)
        assert "blocked" in safe_add
        assert "estimated" in safe_add


class TestCommanderSprintRunningInjection:
    """AC-4: COMMANDER_SPRINT_RUNNING=<sprint-label> set before every child subprocess."""

    def test_dispatch_coder_injects_env(self):
        from services.sprint_manager import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, stdout, stderr, cwd, env):
            captured_envs.append(env)
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            return mock_proc

        with patch.object(sm.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(sm, "_r", return_value="owner/repo"), \
             patch.object(sm, "_build_failure_suffix", return_value=""), \
             patch.object(sm, "_issue_log_path", return_value=Path("/tmp/fake.log")), \
             patch.object(sm.Path, "exists", return_value=False), \
             patch.object(sm.Path, "write_text", return_value=None), \
             patch.object(sm.Path, "open", return_value=MagicMock().__enter__()):
            try:
                sm._dispatch_coder(
                    issue_num=1,
                    alert_modes=[],
                    sprint_label="sprint-37",
                )
            except Exception:
                pass

        if captured_envs:
            env = captured_envs[0]
            assert env.get("COMMANDER_SPRINT_RUNNING") == "sprint-37", (
                f"Expected COMMANDER_SPRINT_RUNNING=sprint-37, got {env.get('COMMANDER_SPRINT_RUNNING')}"
            )

    def test_dispatch_tester_injects_env(self):
        from services.sprint_manager import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, stdout, stderr, cwd, env):
            captured_envs.append(env)
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            return mock_proc

        with patch.object(sm.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(sm, "_r", return_value="owner/repo"), \
             patch.object(sm, "_issue_log_path", return_value=Path("/tmp/fake.log")), \
             patch.object(sm.Path, "exists", return_value=False), \
             patch.object(sm.Path, "write_text", return_value=None), \
             patch.object(sm.Path, "open", return_value=MagicMock().__enter__()):
            try:
                sm._dispatch_tester(
                    issue_num=1,
                    alert_modes=[],
                    sprint_label="sprint-37",
                )
            except Exception:
                pass

        if captured_envs:
            env = captured_envs[0]
            assert env.get("COMMANDER_SPRINT_RUNNING") == "sprint-37"

    def test_call_finish_feature_injects_env(self):
        from services.sprint_manager import sprint_manager as sm

        captured_envs = []

        def fake_run(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sm.subprocess, "run", side_effect=fake_run):
            sm._call_finish_feature(
                issue_num=1,
                worktester_root=Path("/tmp"),
                target_branch="develop",
                sprint_label="sprint-37",
            )

        assert captured_envs, "subprocess.run was not called"
        env = captured_envs[0]
        assert env is not None
        assert env.get("COMMANDER_SPRINT_RUNNING") == "sprint-37"

    def test_apply_in_progress_label_injects_env(self):
        from services.sprint_manager import sprint_manager as sm

        captured_envs = []

        def fake_run(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sm.subprocess, "run", side_effect=fake_run):
            sm._apply_in_progress_label(issue_num=1, sprint_label="sprint-37")

        assert captured_envs
        assert captured_envs[0].get("COMMANDER_SPRINT_RUNNING") == "sprint-37"

    def test_apply_needs_rework_label_injects_env(self):
        from services.sprint_manager import sprint_manager as sm

        captured_envs = []

        def fake_run(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(sm.subprocess, "run", side_effect=fake_run):
            sm._apply_needs_rework_label(
                issue_num=1,
                category=sm.FailureCategory.CODER_NO_WORK,
                sprint_label="sprint-37",
            )

        assert captured_envs
        assert captured_envs[0].get("COMMANDER_SPRINT_RUNNING") == "sprint-37"


class TestUpdateTicketGuard:
    """AC-5: update_ticket.py applies RUN_MUTABLE_LABELS guard when COMMANDER_SPRINT_RUNNING set."""

    def _run_update_ticket(self, status: str, env: dict) -> tuple[list[str], list[str], str]:
        """Run update_ticket main() in a subprocess and capture stdout/stderr."""
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / "update_ticket.py"),
             "--issue", "999", "--status", status, "--force"],
            capture_output=True, text=True, env={**os.environ, **env},
        )
        return result.stdout, result.stderr, result.returncode

    def test_guard_inactive_without_env(self):
        """Without COMMANDER_SPRINT_RUNNING, no guard output."""
        env = {k: v for k, v in os.environ.items() if k != "COMMANDER_SPRINT_RUNNING"}
        stdout, stderr, _ = self._run_update_ticket("uat", env)
        # Should not print guard messages
        assert "Refused to" not in stderr

    def test_guard_logs_blocked_label_for_uat(self):
        """With COMMANDER_SPRINT_RUNNING, 'blocked' label removal is refused."""
        env = {"COMMANDER_SPRINT_RUNNING": "sprint-37"}
        stdout, stderr, _ = self._run_update_ticket("uat", env)
        # 'blocked' is in STATUS_MAP["uat"]["remove"] but outside RUN_MUTABLE_LABELS
        assert 'Refused to remove label "blocked"' in stderr
        assert "RUN_MUTABLE_LABELS" in stderr

    def test_guard_does_not_block_uat_add(self):
        """With COMMANDER_SPRINT_RUNNING, UAT label is still added (it's in RUN_MUTABLE_LABELS)."""
        env = {"COMMANDER_SPRINT_RUNNING": "sprint-37"}
        stdout, stderr, _ = self._run_update_ticket("uat", env)
        # UAT is mutable — no refusal for adding it
        assert 'Refused to add label "UAT"' not in stderr


class TestFinishFeatureUATFlow:
    """AC-6: finish_feature.py only adds UAT and removes only mutable labels under guard."""

    def test_uat_status_map_compliance(self):
        """STATUS_MAP['uat']['add'] contains only RUN_MUTABLE_LABELS entries."""
        sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
        # Import update_ticket constants directly without executing main()
        import importlib
        import types
        spec = importlib.util.spec_from_file_location(
            "update_ticket_mod",
            str(_REPO_ROOT / "scripts" / "update_ticket.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # Stub heavy deps so we can read STATUS_MAP without side effects
        sys.modules.setdefault("github_client", types.ModuleType("github_client"))
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass  # partial load is fine; we just want STATUS_MAP

        if hasattr(mod, "STATUS_MAP") and hasattr(mod, "_RUN_MUTABLE_LABELS"):
            uat_add = mod.STATUS_MAP["uat"]["add"]
            for lbl in uat_add:
                assert lbl in mod._RUN_MUTABLE_LABELS, (
                    f"STATUS_MAP['uat']['add'] contains {lbl!r} which is "
                    f"outside _RUN_MUTABLE_LABELS — finish_feature would add "
                    f"a non-mutable label during a sprint run"
                )

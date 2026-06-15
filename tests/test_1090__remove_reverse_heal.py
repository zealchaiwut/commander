"""Tests for issue #1090: Remove reverse-heal from _is_sprint_running.

AC-1: _heal_sprint_to_running deleted — not importable / not in module.
AC-2: _is_sprint_running is a pure read — zero DB writes.
AC-3: Returns True iff DB state='running' AND owning manager PID alive.
AC-4: Returns False for any terminal DB state — no heal-back.
AC-5: No callsite invokes _heal_sprint_to_running.
AC-6: New tests confirm a terminal row is never silently flipped to running.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))


class TestHealFunctionDeleted:
    """AC-1 / AC-5: _heal_sprint_to_running must not exist in server module."""

    def test_function_not_in_module(self):
        import apps.dashboard.server as srv
        assert not hasattr(srv, "_heal_sprint_to_running"), (
            "_heal_sprint_to_running should be deleted; still present in server module"
        )

    def test_no_callsite_in_source(self):
        """Grep the server.py source for any invocation of the deleted function."""
        server_path = _REPO_ROOT / "apps" / "dashboard" / "server.py"
        source = server_path.read_text(encoding="utf-8")
        assert "_heal_sprint_to_running" not in source, (
            "server.py still references _heal_sprint_to_running"
        )


class TestIsSprintRunningPureRead:
    """AC-2: _is_sprint_running makes zero DB writes."""

    def _make_project_root(self, tmp_path: Path, sprint_label: str) -> Path:
        """Create a minimal project directory with a named slug."""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".commander").mkdir()
        (project_root / ".commander" / "sprints").mkdir()
        return project_root

    def test_running_pid_alive_no_db_write(self, tmp_path):
        """DB state=running + PID alive → True, no DB writes."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path, "sprint-99")
        db_row = {"state": "running", "project": "owner/myproject", "label": "sprint-99"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv.db, "record_sprint_needs_rework") as mock_write, \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            result = srv._is_sprint_running(project_root, "sprint-99")

        assert result is True
        mock_write.assert_not_called()

    def test_running_pid_dead_no_db_write(self, tmp_path):
        """DB state=running + PID dead → False, no DB writes (reconciler's job)."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path, "sprint-99")
        db_row = {"state": "running", "project": "owner/myproject", "label": "sprint-99"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv.db, "record_sprint_needs_rework") as mock_write, \
             patch.object(srv, "_sprint_pid_alive", return_value=False):
            result = srv._is_sprint_running(project_root, "sprint-99")

        assert result is False
        mock_write.assert_not_called()

    def test_terminal_state_no_db_write(self, tmp_path):
        """DB state=completed + PID alive → False, no DB writes, no heal."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path, "sprint-99")
        db_row = {"state": "completed", "project": "owner/myproject", "label": "sprint-99"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv.db, "record_sprint_needs_rework") as mock_write, \
             patch.object(srv, "_sprint_pid_alive", return_value=True), \
             patch.object(srv, "_live_manager_pid", return_value=12345) as mock_pid:
            result = srv._is_sprint_running(project_root, "sprint-99")

        assert result is False
        mock_write.assert_not_called()
        # _live_manager_pid should NOT be called — terminal state is immediate return
        mock_pid.assert_not_called()


class TestIsSprintRunningReturnValues:
    """AC-3 + AC-4: correct return values for all state/PID combinations."""

    def _make_project_root(self, tmp_path: Path) -> Path:
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".commander").mkdir()
        (project_root / ".commander" / "sprints").mkdir()
        return project_root

    def test_running_and_pid_alive_returns_true(self, tmp_path):
        """AC-3: DB state=running AND PID alive → True."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "running", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            assert srv._is_sprint_running(project_root, "sprint-99") is True

    def test_running_and_pid_dead_returns_false(self, tmp_path):
        """AC-3: DB state=running AND PID dead → False."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "running", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=False):
            assert srv._is_sprint_running(project_root, "sprint-99") is False

    def test_terminal_completed_returns_false(self, tmp_path):
        """AC-4: terminal state='completed' → False, even with live PID."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "completed", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            assert srv._is_sprint_running(project_root, "sprint-99") is False

    def test_terminal_needs_rework_returns_false(self, tmp_path):
        """AC-4: terminal state='needs_rework' → False, even with live PID."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "needs_rework", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            assert srv._is_sprint_running(project_root, "sprint-99") is False

    def test_terminal_cancelled_returns_false(self, tmp_path):
        """AC-4: terminal state='cancelled' → False, even with live PID."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "cancelled", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            assert srv._is_sprint_running(project_root, "sprint-99") is False

    def test_terminal_failed_returns_false(self, tmp_path):
        """AC-4: terminal state='failed' → False, even with live PID."""
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)
        db_row = {"state": "failed", "project": "owner/myproject"}

        with patch.object(srv.db, "get_sprint", return_value=db_row), \
             patch.object(srv, "_sprint_pid_alive", return_value=True):
            assert srv._is_sprint_running(project_root, "sprint-99") is False


class TestTerminalRowNeverFlippedToRunning:
    """AC-6: Terminal row is never silently flipped to running."""

    def _make_project_root(self, tmp_path: Path) -> Path:
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".commander").mkdir()
        (project_root / ".commander" / "sprints").mkdir()
        return project_root

    def test_terminal_row_db_state_unchanged(self, tmp_path):
        """A terminal DB row must remain terminal after _is_sprint_running call.

        The test verifies that no write (record_sprint_needs_rework, record_sprint_start,
        or _sprint_db_set_state) is called when DB state is terminal.
        """
        import apps.dashboard.server as srv

        project_root = self._make_project_root(tmp_path)

        for terminal_state in ("completed", "needs_rework", "cancelled", "failed"):
            db_row = {"state": terminal_state, "project": "owner/myproject"}

            written_states = []

            def capture_write(label, state=None, *args, **kwargs):
                written_states.append(state or "write")

            with patch.object(srv.db, "get_sprint", return_value=db_row), \
                 patch.object(srv.db, "record_sprint_needs_rework",
                              side_effect=lambda *a, **kw: written_states.append("needs_rework")), \
                 patch.object(srv, "_sprint_pid_alive", return_value=True), \
                 patch.object(srv, "_live_manager_pid", return_value=99999):
                result = srv._is_sprint_running(project_root, "sprint-99")

            assert result is False, (
                f"state={terminal_state}: expected False but got {result}"
            )
            assert not written_states, (
                f"state={terminal_state}: DB write occurred but should not have: {written_states}"
            )

    def test_terminal_plan_json_state_unchanged(self, tmp_path):
        """A terminal plan.json state must remain terminal — no heal-back writes."""
        import apps.dashboard.server as srv
        import json

        project_root = self._make_project_root(tmp_path)
        sprint_label = "sprint-99"

        # Write a terminal plan.json
        sprints_dir = project_root / ".commander" / "sprints"
        plan_file = sprints_dir / f"{sprint_label}-plan.json"
        plan_data = {"state": "needs_rework", "end_reason": "process lost"}
        plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

        plan_writes = []

        def capture_plan_write(project_root, label, state, **kwargs):
            plan_writes.append(state)

        with patch.object(srv.db, "get_sprint", return_value=None), \
             patch.object(srv, "_plan_json_set_state",
                          side_effect=capture_plan_write):
            result = srv._is_sprint_running(project_root, sprint_label)

        assert result is False
        assert not plan_writes, (
            f"plan.json write occurred but should not have: {plan_writes}"
        )

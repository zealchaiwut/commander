"""
Tests for issue #2245 — Extract list_backlog_issues out of the dispatch pipeline.

AC1: list_backlog_issues moved to a small planning-only module (backlog.py)
AC2: Both call sites repointed (scheduler one disappears with S2-2)
AC3: grep over apps/dashboard/ confirms zero non-subprocess references to sprint_manager
AC4: Backlog listing still returns the same issues for a given sprint
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent


class TestAC1MovedToBacklogModule:
    def test_importable_from_backlog(self):
        from services.sprint_manager.backlog import list_backlog_issues
        assert callable(list_backlog_issues)

    def test_backlog_module_is_self_contained(self):
        """backlog.py should define list_backlog_issues, not just re-export it."""
        import services.sprint_manager.backlog as bm
        assert "list_backlog_issues" in vars(bm)

    def test_still_importable_from_pipeline(self):
        from services.sprint_manager.pipeline import list_backlog_issues
        assert callable(list_backlog_issues)

    def test_still_importable_from_sprint_manager(self):
        import services.sprint_manager.sprint_manager as sm
        assert callable(sm.list_backlog_issues)


class TestAC2SprintRunCallSiteRepointed:
    def test_sprint_run_does_not_import_sprint_manager_orchestrator(self):
        src = (REPO_ROOT / "apps/dashboard/routers/sprint_run.py").read_text()
        assert "from services.sprint_manager import sprint_manager" not in src, (
            "sprint_run.py still imports sprint_manager orchestrator — repoint to backlog"
        )

    def test_sprint_run_imports_from_backlog(self):
        src = (REPO_ROOT / "apps/dashboard/routers/sprint_run.py").read_text()
        assert "sprint_manager.backlog" in src or "from services.sprint_manager.backlog" in src, (
            "sprint_run.py should import list_backlog_issues from services.sprint_manager.backlog"
        )


class TestAC3ZeroNonSubprocessRefs:
    def test_no_direct_import_sprint_manager_orchestrator_in_dashboard(self):
        result = subprocess.run(
            ["grep", "-rn",
             "from services.sprint_manager import sprint_manager",
             "apps/dashboard/"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert lines == [], (
            f"Non-subprocess import of sprint_manager orchestrator found in apps/dashboard/:\n"
            + "\n".join(lines)
        )


class TestAC4BacklogListingBehaviorUnchanged:
    def test_list_backlog_issues_returns_only_dispatchable(self):
        from services.sprint_manager.backlog import list_backlog_issues

        dispatchable = {
            "number": 1, "title": "T1",
            "labels": [{"name": "backlog"}, {"name": "sprint-99"}],
        }
        done = {
            "number": 2, "title": "T2",
            "labels": [{"name": "UAT-approved"}, {"name": "sprint-99"}],
        }
        with patch("services.sprint_manager.backlog._list_labeled_open_issues",
                   return_value=[dispatchable, done]):
            result = list_backlog_issues("sprint-99")

        assert len(result) == 1
        assert result[0]["number"] == 1

    def test_list_backlog_issues_excludes_blocked(self):
        from services.sprint_manager.backlog import list_backlog_issues

        blocked = {
            "number": 3, "title": "T3",
            "labels": [{"name": "backlog"}, {"name": "blocked"}, {"name": "sprint-99"}],
        }
        with patch("services.sprint_manager.backlog._list_labeled_open_issues",
                   return_value=[blocked]):
            result = list_backlog_issues("sprint-99")

        assert result == []

    def test_list_backlog_issues_includes_needs_rework(self):
        from services.sprint_manager.backlog import list_backlog_issues

        rework = {
            "number": 4, "title": "T4",
            "labels": [{"name": "needs-rework"}, {"name": "sprint-99"}],
        }
        with patch("services.sprint_manager.backlog._list_labeled_open_issues",
                   return_value=[rework]):
            result = list_backlog_issues("sprint-99")

        assert len(result) == 1
        assert result[0]["number"] == 4

    def test_list_backlog_issues_includes_in_progress(self):
        from services.sprint_manager.backlog import list_backlog_issues

        in_prog = {
            "number": 5, "title": "T5",
            "labels": [{"name": "in-progress"}, {"name": "sprint-99"}],
        }
        with patch("services.sprint_manager.backlog._list_labeled_open_issues",
                   return_value=[in_prog]):
            result = list_backlog_issues("sprint-99")

        assert len(result) == 1

    def test_list_backlog_issues_signature_preserved(self):
        import inspect
        from services.sprint_manager.backlog import list_backlog_issues
        params = list(inspect.signature(list_backlog_issues).parameters)
        assert params == ["label", "repo_name"], (
            f"list_backlog_issues signature changed: {params}"
        )

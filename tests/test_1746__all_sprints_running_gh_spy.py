"""Behavioral integration test for issue #1746 AC2.

Tests that _all_sprints_running() → load_projects() chain makes zero GitHub
API calls when projects.json exists on disk.

Prior tests (e.g. test_running_snapshot__1645) mocked the server object
wholesale, so the real _all_sprints_running → load_projects() path was never
exercised with a gh spy. This test runs the real chain.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_DASHBOARD = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_projects(projects_file: Path, repos: list[str]) -> None:
    projects_file.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {"repo": r, "name": r.split("/")[-1], "icon": "ti-folder", "color": "blue"}
        for r in repos
    ]
    projects_file.write_text(json.dumps(data), encoding="utf-8")


def _write_plan(sprints_dir: Path, label: str, state: str = "running") -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    plan = {"state": state, "sprint_label": label, "project": "owner/test-repo"}
    (sprints_dir / f"{label}-plan.json").write_text(json.dumps(plan), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC2: _all_sprints_running makes zero gh calls
# ---------------------------------------------------------------------------

class TestAllSprintsRunningZeroGhCalls:
    """_all_sprints_running() must not call any github_client method."""

    def test_no_gh_call_when_projects_file_exists_no_running_sprints(self, tmp_path):
        """load_projects() reads from disk; no running sprint → zero gh calls."""
        import projects as projects_module
        import github_client

        projects_file = tmp_path / "projects.json"
        _write_projects(projects_file, ["owner/test-repo"])

        # Point projects module at our fake file and _project_root_path at tmp
        with (
            patch.object(projects_module, "PROJECTS_FILE", projects_file),
            patch("startup._project_root_path", return_value=tmp_path),
            patch.object(github_client, "list_open_issues", side_effect=AssertionError("gh called")),
            patch.object(github_client, "list_open_issues_with_body", side_effect=AssertionError("gh called")),
            patch.object(github_client, "repo", side_effect=AssertionError("gh called")),
        ):
            import startup
            result = startup._all_sprints_running()

        # No running sprints (no sprint dirs)
        assert result == []

    def test_no_gh_call_when_sprint_running_read_from_plan_json(self, tmp_path):
        """When a sprint is running (plan.json=running + live PID), load_projects
        reads the projects file and _all_sprints_running reads plan.json.
        No gh CLI call must occur at any point."""
        import projects as projects_module
        import github_client

        projects_file = tmp_path / "projects.json"
        _write_projects(projects_file, ["owner/test-repo"])

        # Set up .commander/sprints/sprint-99-plan.json with state=running
        repo_root = tmp_path / "test-repo"
        sprints_dir = repo_root / ".commander" / "sprints"
        _write_plan(sprints_dir, "sprint-99", state="running")

        # Write a PID file for a live process (our own PID is guaranteed alive)
        own_pid = os.getpid()
        (sprints_dir / "sprint-99-pid").write_text(str(own_pid), encoding="utf-8")

        gh_spy = MagicMock()

        with (
            patch.object(projects_module, "PROJECTS_FILE", projects_file),
            patch("startup._project_root_path", return_value=repo_root),
            patch("startup._commander_dir", return_value=repo_root / ".commander"),
            patch("startup._get_sprint_pid", return_value=own_pid),
            patch.object(github_client, "list_open_issues", gh_spy),
            patch.object(github_client, "list_open_issues_with_body", gh_spy),
            patch.object(github_client, "list_issues", gh_spy),
            patch.object(github_client, "repo", gh_spy),
        ):
            import startup
            result = startup._all_sprints_running()

        gh_spy.assert_not_called()
        # Should find our running sprint
        assert any(r["sprint_label"] == "sprint-99" for r in result), (
            f"Expected sprint-99 in running list but got: {result}"
        )

    def test_no_gh_call_when_multiple_projects_all_on_disk(self, tmp_path):
        """Multiple projects, all with projects.json — zero gh calls even for
        projects with no running sprints."""
        import projects as projects_module
        import github_client

        projects_file = tmp_path / "projects.json"
        _write_projects(projects_file, ["owner/alpha", "owner/beta"])

        gh_methods = [
            "list_open_issues", "list_open_issues_with_body",
            "list_issues", "repo", "list_feature_branches",
        ]
        patches = {m: MagicMock() for m in gh_methods}

        ctx = [
            patch.object(projects_module, "PROJECTS_FILE", projects_file),
            patch("startup._project_root_path", return_value=tmp_path),
        ]
        for name, mock in patches.items():
            ctx.append(patch.object(github_client, name, mock))

        with (
            patch.object(projects_module, "PROJECTS_FILE", projects_file),
            patch("startup._project_root_path", return_value=tmp_path),
            patch.object(github_client, "list_open_issues", patches["list_open_issues"]),
            patch.object(github_client, "list_open_issues_with_body", patches["list_open_issues_with_body"]),
            patch.object(github_client, "list_issues", patches["list_issues"]),
            patch.object(github_client, "repo", patches["repo"]),
            patch.object(github_client, "list_feature_branches", patches["list_feature_branches"]),
        ):
            import startup
            startup._all_sprints_running()

        for name, mock in patches.items():
            mock.assert_not_called(), f"github_client.{name} was called unexpectedly"


# ---------------------------------------------------------------------------
# AC2 variant: load_projects itself makes no gh call when projects.json exists
# ---------------------------------------------------------------------------

class TestLoadProjectsNoGhCall:
    """load_projects() reads from disk when projects.json exists; no gh calls."""

    def test_reads_from_disk_not_gh(self, tmp_path):
        import projects as projects_module
        import github_client

        projects_file = tmp_path / "projects.json"
        _write_projects(projects_file, ["owner/my-project"])

        gh_repo_spy = MagicMock(side_effect=AssertionError("github_client.repo() called"))

        with (
            patch.object(projects_module, "PROJECTS_FILE", projects_file),
            patch.object(github_client, "repo", gh_repo_spy),
        ):
            result = projects_module.load_projects()

        assert len(result) == 1
        assert result[0]["repo"] == "owner/my-project"
        gh_repo_spy.assert_not_called()

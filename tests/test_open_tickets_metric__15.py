"""
Tests for issue #15 — fix: "Open Tickets" metric shows 0 for projects
without an active sprint.

AC-1: get_all_projects() has an else branch calling list_open_issues when sprint_num is None.
AC-2: No new asyncio.sleep or polling mechanism added for issue fetching (source check).
AC-3: The if sprint_num: block calling list_issues() is still present (no regression).
AC-4: The fallback call in get_all_projects() uses the same function as
      get_project_details(); also verified via HTTP GET /api/projects.
"""
import ast
import inspect
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Ensure the dashboard package is importable.
# Prefer work-tester/dashboard (feature branch worktree) if it exists,
# otherwise fall back to the main dashboard/ directory.
_WORK_TESTER_DIR = Path(__file__).parent.parent / "work-tester" / "dashboard"
_MAIN_DIR = Path(__file__).parent.parent / "dashboard"
DASHBOARD_DIR = _WORK_TESTER_DIR if _WORK_TESTER_DIR.exists() else _MAIN_DIR

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import projects as projects_mod  # noqa: E402
# Reload to make sure we get the module from the correct DASHBOARD_DIR
import importlib
importlib.reload(projects_mod)

PROJECTS_PY = DASHBOARD_DIR / "projects.py"
SOURCE = PROJECTS_PY.read_text()


# ---------------------------------------------------------------------------
# Helpers: parse the AST of get_all_projects and get_project_details
# ---------------------------------------------------------------------------

def _func_source(name: str) -> str:
    """Return the dedented source of a top-level function in projects.py."""
    obj = getattr(projects_mod, name)
    return inspect.getsource(obj)


def _parse_func(name: str) -> ast.FunctionDef:
    src = textwrap.dedent(_func_source(name))
    tree = ast.parse(src)
    # The function is always the first node after the dedent
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Could not find function {name} in parsed AST")


# ---------------------------------------------------------------------------
# AC-1 — else branch calling list_open_issues exists in get_all_projects
# ---------------------------------------------------------------------------

class TestAC1ElseBranchInGetAllProjects:
    def test_else_branch_exists(self):
        """get_all_projects() must have an if/else that handles no-sprint projects."""
        func = _parse_func("get_all_projects")

        found_if_else = False
        for node in ast.walk(func):
            if isinstance(node, ast.If) and node.orelse:
                # Check the if-body calls list_issues and else-body calls list_open_issues
                if_src = ast.dump(node)
                if "list_issues" in if_src and "list_open_issues" in if_src:
                    found_if_else = True
                    break

        assert found_if_else, (
            "Expected get_all_projects() to contain an if/else block where the "
            "if-branch calls list_issues() and the else-branch calls list_open_issues()"
        )

    def test_list_open_issues_called_in_else(self):
        """The else branch must call github_client.list_open_issues."""
        func_src = _func_source("get_all_projects")
        assert "list_open_issues" in func_src, (
            "get_all_projects() must call list_open_issues for no-sprint projects"
        )

    def test_get_all_projects_no_sprint_returns_open_count(self):
        """get_all_projects() must include open issues in total_open for no-sprint projects."""
        fake_project = {
            "repo": "owner/no-sprint-repo",
            "name": "No Sprint Repo",
            "icon": "ti-folder",
            "color": "blue",
            "active_sprints": {},  # <-- no active sprint
        }
        fake_open_issues = [
            {"number": 1, "state": "open", "labels": [], "title": "Issue 1",
             "url": "https://github.com/owner/no-sprint-repo/issues/1",
             "assignees": [], "createdAt": "2024-01-01", "updatedAt": "2024-01-02"},
            {"number": 2, "state": "open", "labels": [], "title": "Issue 2",
             "url": "https://github.com/owner/no-sprint-repo/issues/2",
             "assignees": [], "createdAt": "2024-01-01", "updatedAt": "2024-01-02"},
        ]

        with (
            patch.object(projects_mod, "load_projects", return_value=[fake_project]),
            patch("github_client.list_open_issues", return_value=fake_open_issues),
            patch("github_client.list_issues", return_value=[]),
            patch("db.get_tokens_today", return_value={"input_tokens": 0, "output_tokens": 0}),
        ):
            result = projects_mod.get_all_projects(agents=[])

        metrics = result["metrics"]
        assert metrics["open_tickets"] == 2, (
            f"Expected open_tickets=2 for a no-sprint project with 2 open issues, "
            f"got {metrics['open_tickets']}"
        )
        proj_card = result["projects"][0]
        assert proj_card["openCount"] == 2, (
            f"Expected openCount=2 in project card, got {proj_card['openCount']}"
        )


# ---------------------------------------------------------------------------
# AC-2 — No new asyncio.sleep or polling interval added for issue fetching
# ---------------------------------------------------------------------------

class TestAC2NoNewPollingMechanism:
    def test_no_asyncio_sleep_in_get_all_projects(self):
        """get_all_projects() must not contain asyncio.sleep."""
        func_src = _func_source("get_all_projects")
        assert "asyncio.sleep" not in func_src, (
            "get_all_projects() must not introduce asyncio.sleep (no new polling)"
        )

    def test_no_asyncio_sleep_in_get_project_details(self):
        """get_project_details() must not contain asyncio.sleep."""
        func_src = _func_source("get_project_details")
        assert "asyncio.sleep" not in func_src, (
            "get_project_details() must not introduce asyncio.sleep (no new polling)"
        )

    def test_no_new_interval_constant(self):
        """No new INTERVAL or POLLING constant for issue fetching should be present."""
        lower = SOURCE.lower()
        # We allow existing _CYCLE and SPRINT_RE constants but no new polling interval
        forbidden_patterns = ["issue_poll_interval", "issue_fetch_interval"]
        for pat in forbidden_patterns:
            assert pat not in lower, (
                f"Forbidden polling constant '{pat}' found in projects.py"
            )


# ---------------------------------------------------------------------------
# AC-3 — sprint path (list_issues) is still present and unchanged
# ---------------------------------------------------------------------------

class TestAC3SprintPathNotRegressed:
    def test_list_issues_still_called_for_sprint_projects(self):
        """get_all_projects() must still call list_issues() when sprint_num is set."""
        func_src = _func_source("get_all_projects")
        assert "list_issues" in func_src, (
            "get_all_projects() must still call list_issues() for sprint-based projects"
        )

    def test_if_sprint_num_guard_present(self):
        """The if sprint_num: guard must still be present."""
        func = _parse_func("get_all_projects")
        sprint_guard_found = False
        for node in ast.walk(func):
            if isinstance(node, ast.If):
                # Looking for: if sprint_num: ... list_issues(...)
                test_src = ast.dump(node.test)
                body_src = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                if "sprint_num" in test_src and "list_issues" in body_src:
                    sprint_guard_found = True
                    break
        assert sprint_guard_found, (
            "Expected 'if sprint_num:' guard calling list_issues() in get_all_projects()"
        )

    def test_get_all_projects_sprint_path_used(self):
        """When a project has an active sprint, list_issues() is called, not list_open_issues."""
        fake_project = {
            "repo": "owner/sprint-repo",
            "name": "Sprint Repo",
            "icon": "ti-folder",
            "color": "green",
            "active_sprints": {"1": {"started_at": "2024-01-01", "theme": "V1"}},
        }
        fake_sprint_issues = [
            {"number": 10, "state": "open", "labels": [{"name": "sprint-1"}],
             "title": "Sprint Issue", "url": "...", "assignees": [],
             "createdAt": "2024-01-01", "updatedAt": "2024-01-02"},
        ]

        with (
            patch.object(projects_mod, "load_projects", return_value=[fake_project]),
            patch("github_client.list_issues", return_value=fake_sprint_issues) as mock_li,
            patch("github_client.list_open_issues", return_value=[]) as mock_loi,
            patch("db.get_tokens_today", return_value={"input_tokens": 0, "output_tokens": 0}),
        ):
            result = projects_mod.get_all_projects(agents=[])

        mock_li.assert_called_once(), "list_issues() must be called for sprint projects"
        mock_loi.assert_not_called(), "list_open_issues() must NOT be called for sprint projects"
        assert result["metrics"]["open_tickets"] == 1


# ---------------------------------------------------------------------------
# AC-4 — get_project_details uses same fallback + HTTP check
# ---------------------------------------------------------------------------

class TestAC4ConsistentFallbackAndHTTP:
    def test_get_project_details_also_calls_list_open_issues(self):
        """get_project_details() must also call list_open_issues for no-sprint projects."""
        func_src = _func_source("get_project_details")
        assert "list_open_issues" in func_src, (
            "get_project_details() must call list_open_issues as the no-sprint fallback"
        )

    def test_same_function_used_in_both(self):
        """Both get_all_projects and get_project_details use github_client.list_open_issues."""
        gap_src = _func_source("get_all_projects")
        gpd_src = _func_source("get_project_details")
        assert "list_open_issues" in gap_src, "get_all_projects missing list_open_issues"
        assert "list_open_issues" in gpd_src, "get_project_details missing list_open_issues"

    def test_http_api_projects_open_count_positive(self):
        """GET /api/projects must return openCount > 0 for Commander (no sprint, has open issues)."""
        try:
            resp = httpx.get("http://localhost:8000/api/projects", timeout=10)
        except httpx.ConnectError:
            pytest.skip("Dashboard not running on localhost:8000")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()

        projects = data.get("projects", [])
        commander = next(
            (p for p in projects if "commander" in p.get("repo", "").lower()),
            None,
        )
        assert commander is not None, "Commander project not found in /api/projects response"
        open_count = commander.get("openCount", 0)
        assert open_count > 0, (
            f"Expected openCount > 0 for Commander (no sprint, has open issues), got {open_count}"
        )

    def test_http_metrics_open_tickets_positive(self):
        """GET /api/projects metrics.open_tickets must be > 0 (Commander has open issues)."""
        try:
            resp = httpx.get("http://localhost:8000/api/projects", timeout=10)
        except httpx.ConnectError:
            pytest.skip("Dashboard not running on localhost:8000")

        assert resp.status_code == 200
        data = resp.json()
        open_tickets = data.get("metrics", {}).get("open_tickets", 0)
        assert open_tickets > 0, (
            f"Expected metrics.open_tickets > 0, got {open_tickets}"
        )

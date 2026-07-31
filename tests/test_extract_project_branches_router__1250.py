"""Tests for issue #1250: Extract project branch routes to dedicated router.

AC-1:  routers/project_branches.py exists and defines an APIRouter with
       prefix /api/projects/{owner}/{repo}/branches
AC-2:  GET /api/projects/{owner}/{repo}/branches/stale handled by
       get_stale_branches in the new router
AC-3:  DELETE /api/projects/{owner}/{repo}/branches/{branch} handled by
       delete_project_branch in the new router
AC-4:  Both endpoints return identical responses (status codes, schemas)
       as before the refactor
AC-5:  server.py no longer defines either route directly; only includes
       the new router via app.include_router(...)
AC-6:  No new routes are added to server.py
AC-7:  python -m py_compile routers/project_branches.py server.py exits 0
AC-8:  All existing tests for branch endpoints pass without modification
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
SERVER_PY = DASHBOARD_DIR / "server.py"
ROUTER_FILE = ROUTERS_DIR / "project_branches.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")


def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _get_test_client():
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db", "github_client") or mod.startswith(
            "routers"
        ):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    import server as srv

    return TestClient(srv.app, raise_server_exceptions=False), srv


FAKE_PROJECT = [{"repo": "owner/testrepo", "name": "Test Repo"}]


# ── AC-1: Router file exists with correct APIRouter declaration ─────────────


class TestRouterFileExists:
    """AC-1: routers/project_branches.py exists with APIRouter
    + correct prefix."""

    def test_router_file_exists(self):
        """AC-1: routers/project_branches.py must exist."""
        assert ROUTER_FILE.exists(), (
            "routers/project_branches.py does not exist"
        )

    def test_router_file_defines_api_router(self):
        """AC-1: File must import and instantiate APIRouter."""
        source = ROUTER_FILE.read_text()
        assert "APIRouter" in source, (
            "routers/project_branches.py must import and use APIRouter"
        )

    def test_router_has_correct_prefix(self):
        """AC-1: APIRouter must have prefix
        /api/projects/{owner}/{repo}/branches."""
        source = ROUTER_FILE.read_text()
        assert "/api/projects/{owner}/{repo}/branches" in source, (
            "routers/project_branches.py must declare prefix "
            "'/api/projects/{owner}/{repo}/branches'"
        )

    def test_router_importable(self):
        """AC-1: Module must import without errors."""
        spec = importlib.util.spec_from_file_location(
            "project_branches", ROUTER_FILE
        )
        mod = importlib.util.module_from_spec(spec)
        # Should not raise
        spec.loader.exec_module(mod)
        assert hasattr(mod, "router"), (
            "routers/project_branches.py must expose a 'router' attribute"
        )


# ── AC-2: GET stale endpoint in new router ──────────────────────────────────


class TestGetStaleBranchesRoute:
    """AC-2: GET /api/projects/{owner}/{repo}/branches/stale
    handled by new router."""

    def test_get_stale_branches_function_in_router(self):
        """AC-2: get_stale_branches function defined in project_branches.py."""
        source = ROUTER_FILE.read_text()
        assert "get_stale_branches" in source, (
            "routers/project_branches.py must define get_stale_branches"
        )

    def test_get_stale_branches_returns_200(self):
        """AC-2/AC-4: GET /stale returns HTTP 200 with list."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "feature/100-foo"}])
        existing = "feature/100-foo\ndevelop\nmaster\n"
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(
                "/api/projects/owner/testrepo/branches/stale"
            )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_stale_branches_returns_matching_branches(self):
        """AC-4: Response schema matches pre-refactor behavior —
        sorted list of branch names."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "feature/100-foo"}])
        existing = "feature/100-foo\ndevelop\nmaster\n"
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(
                "/api/projects/owner/testrepo/branches/stale"
            )
        assert "feature/100-foo" in resp.json()

    def test_get_stale_branches_excludes_protected(self):
        """AC-4: Protected branches still excluded (same as before)."""
        client, srv = _get_test_client()
        merged = json.dumps([{"headRefName": "develop"}])
        existing = "develop\nmaster\n"
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(
                "/api/projects/owner/testrepo/branches/stale"
            )
        assert resp.status_code == 200
        assert "develop" not in resp.json()

    def test_get_stale_branches_returns_empty_when_none(self):
        """AC-4: Returns [] when no stale branches — same as pre-refactor."""
        client, srv = _get_test_client()
        merged = json.dumps([])
        existing = "develop\nmaster\n"
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.side_effect = [_make_proc(merged), _make_proc(existing)]
            resp = client.get(
                "/api/projects/owner/testrepo/branches/stale"
            )
        assert resp.json() == []


# ── AC-3: DELETE endpoint in new router ─────────────────────────────────────


class TestDeleteBranchRoute:
    """AC-3: DELETE /api/projects/{owner}/{repo}/branches/{branch}
    in new router."""

    def test_delete_branch_function_in_router(self):
        """AC-3: delete_project_branch function defined in
        project_branches.py."""
        source = ROUTER_FILE.read_text()
        assert "delete_project_branch" in source, (
            "routers/project_branches.py must define delete_project_branch"
        )

    def test_delete_branch_success_returns_200(self):
        """AC-3/AC-4: DELETE returns HTTP 200 on success with ok:true."""
        client, srv = _get_test_client()
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.return_value = _make_proc("", returncode=0)
            resp = client.delete(
                "/api/projects/owner/testrepo/branches/feature/100-foo"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "branch" in data

    def test_delete_protected_branch_returns_400(self):
        """AC-4: DELETE develop returns 400 — same as pre-refactor."""
        client, srv = _get_test_client()
        with patch.object(
            srv.projects_module, "load_projects", return_value=FAKE_PROJECT
        ):
            resp = client.delete(
                "/api/projects/owner/testrepo/branches/develop"
            )
        assert resp.status_code == 400

    def test_delete_master_returns_400(self):
        """AC-4: DELETE master returns 400."""
        client, srv = _get_test_client()
        with patch.object(
            srv.projects_module, "load_projects", return_value=FAKE_PROJECT
        ):
            resp = client.delete(
                "/api/projects/owner/testrepo/branches/master"
            )
        assert resp.status_code == 400

    def test_delete_non_feat_sprint_returns_400(self):
        """AC-4: DELETE hotfix/* returns 400 — same as pre-refactor."""
        client, srv = _get_test_client()
        with patch.object(
            srv.projects_module, "load_projects", return_value=FAKE_PROJECT
        ):
            resp = client.delete(
                "/api/projects/owner/testrepo/branches/hotfix/bug-123"
            )
        assert resp.status_code == 400

    def test_delete_gh_failure_returns_500(self):
        """AC-4: When gh api fails, returns 500 — same as pre-refactor."""
        client, srv = _get_test_client()
        with (
            patch("subprocess.run") as mock_run,
            patch.object(
                srv.projects_module,
                "load_projects",
                return_value=FAKE_PROJECT,
            ),
        ):
            mock_run.return_value = _make_proc(
                "", returncode=1, stderr="not found"
            )
            resp = client.delete(
                "/api/projects/owner/testrepo/branches/feature/100-foo"
            )
        assert resp.status_code == 500


# ── AC-5: server.py no longer defines routes directly ───────────────────────


class TestServerPyNoDirectRoutes:
    """AC-5/AC-6: server.py only includes the new router,
    defines no direct routes."""

    def test_server_py_has_no_stale_route_decorator(self):
        """AC-5: @app.get('.../branches/stale') not present in server.py."""
        source = SERVER_PY.read_text()
        # The route decorator for the stale endpoint must be gone
        assert (
            '@app.get("/api/projects/{owner}/{repo_name}/branches/stale")'
            not in source
        ), "server.py still defines @app.get('.../branches/stale') directly"

    def test_server_py_has_no_delete_branch_route_decorator(self):
        """AC-5: @app.delete('.../branches/{branch:path}') not present
        in server.py."""
        source = SERVER_PY.read_text()
        _deco = (
            '@app.delete("/api/projects'
            '/{owner}/{repo_name}/branches/{branch:path}"'
        )
        assert _deco not in source, (
            "server.py still defines "
            "@app.delete('.../branches/{branch:path}') directly"
        )

    def test_server_py_includes_project_branches_router(self):
        """AC-5: server.py must include the project_branches_router
        via include_router."""
        source = SERVER_PY.read_text()
        assert "project_branches_router" in source, (
            "server.py must import and include project_branches_router"
        )
        assert "include_router(project_branches_router)" in source, (
            "server.py must call app.include_router(project_branches_router)"
        )

    def test_server_py_does_not_define_get_stale_branches(self):
        """AC-5: get_stale_branches function not defined
        (decorated) in server.py."""
        source = SERVER_PY.read_text()
        # Check the function is not decorated with @app.get in server.py
        # (it may be imported, but not defined as a route handler)
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "def get_stale_branches" in line:
                # Look at the preceding line for a decorator
                if i > 0 and "@app." in lines[i - 1]:
                    pytest.fail(
                        "server.py defines get_stale_branches"
                        " as an @app route handler"
                    )

    def test_server_py_does_not_define_delete_project_branch(self):
        """AC-5: delete_project_branch not defined as
        @app route in server.py."""
        source = SERVER_PY.read_text()
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "def delete_project_branch" in line:
                if i > 0 and "@app." in lines[i - 1]:
                    pytest.fail(
                        "server.py defines delete_project_branch"
                        " as an @app route handler"
                    )


# ── AC-7: py_compile passes ─────────────────────────────────────────────────


class TestPyCompile:
    """AC-7: python -m py_compile on both files exits 0 with no output."""

    def test_py_compile_project_branches(self):
        """AC-7: routers/project_branches.py compiles clean."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROUTER_FILE)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on project_branches.py:\n{result.stderr}"
        )
        assert result.stdout == "", "py_compile produced unexpected output"
        assert result.stderr == "", f"py_compile stderr: {result.stderr}"

    def test_py_compile_server(self):
        """AC-7: server.py compiles clean after refactor."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SERVER_PY)],
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode == 0, (
            f"py_compile failed on server.py:\n{result.stderr}"
        )

    def test_py_compile_both_together(self):
        """AC-7: Both files compile together in one command."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "py_compile",
                str(ROUTER_FILE),
                str(SERVER_PY),
            ],
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode == 0, (
            f"py_compile failed:\n{result.stderr}"
        )
        assert result.stdout.strip() == "", (
            f"py_compile produced output: {result.stdout}"
        )

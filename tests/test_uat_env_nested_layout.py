"""Tests for _resolve_uat_env_for_tester nested-layout UAT clone discovery.

Regression: perf-coach sprints failed with 'UAT clone not found beside tester
worktree' because the resolver only looked for Commander (uat/apps/dashboard)
or legacy uat/{worktree_name}/ paths — not the standard sibling uat/ clone.
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


@pytest.fixture()
def sm():
    import sprint_manager as sm_mod  # noqa: WPS433
    return importlib.reload(sm_mod)


def _nested_project(tmp_path: Path, *, env_body: str) -> tuple[Path, Path]:
    """Create coder/tester/uat sibling layout (perf-coach style)."""
    tester = tmp_path / "tester"
    uat = tmp_path / "uat"
    tester.mkdir()
    uat.mkdir()
    (tester / ".git").mkdir()
    (uat / ".git").mkdir()
    (uat / ".env").write_text(env_body, encoding="utf-8")
    return tester, uat


def test_resolves_sibling_uat_clone(sm, tmp_path):
    """AC-1: nested layout with uat/.env at repo root resolves UAT env."""
    tester, uat = _nested_project(
        tmp_path,
        env_body="ENVIRONMENT=uat\nPORT=9001\n",
    )
    env, err = sm._resolve_uat_env_for_tester(None, tester)
    assert err is None
    assert env is not None
    assert env["UAT_REPO"] == str(uat)
    assert env["UAT_BASE_URL"] == "http://localhost:9001"
    assert env["UAT_PORT"] == "9001"
    assert env["COMMANDER_UAT_PREVALIDATED"] == "1"


def test_commander_dashboard_layout_still_works(sm, tmp_path):
    """AC-2: Commander uat/apps/dashboard/.env still resolves."""
    project = tmp_path / "commander"
    tester_app = project / "tester" / "apps" / "dashboard"
    uat_env = project / "uat" / "apps" / "dashboard"
    tester_app.mkdir(parents=True)
    uat_env.mkdir(parents=True)
    (project / "tester" / ".git").mkdir(parents=True)
    (project / "uat" / ".git").mkdir(parents=True)
    (uat_env / ".env").write_text("ENVIRONMENT=uat\nPORT=8001\n", encoding="utf-8")

    env, err = sm._resolve_uat_env_for_tester(None, tester_app)
    assert err is None
    assert env is not None
    assert env["UAT_REPO"] == str(project / "uat")
    assert env["UAT_BASE_URL"] == "http://localhost:8001"


def test_missing_uat_clone_returns_error(sm, tmp_path):
    """AC-3: no uat/ sibling → clear error."""
    tester = tmp_path / "tester"
    tester.mkdir()
    (tester / ".git").mkdir()

    env, err = sm._resolve_uat_env_for_tester(None, tester)
    assert env is None
    assert err is not None
    assert "UAT clone not found" in err


def test_refuses_prd_port_8000(sm, tmp_path):
    """AC-4: PORT=8000 in uat .env is rejected."""
    tester, _ = _nested_project(
        tmp_path,
        env_body="ENVIRONMENT=uat\nPORT=8000\n",
    )
    env, err = sm._resolve_uat_env_for_tester(None, tester)
    assert env is None
    assert err is not None
    assert "8000" in err

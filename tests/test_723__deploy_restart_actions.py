"""Tests for issue #723 — local deploy and restart actions for Mac-mini envs.

AC coverage:
  AC1  — POST .../deploy exists and runs under the existing middleware stack
  AC2  — deploy runs `git pull --ff-only` in working_dir; never merge/push/PR
  AC3  — deploy response returns raw git-pull stdout + new HEAD sha
  AC4  — deploy rejects when working_dir/branch absent (4xx, no shell command)
  AC5  — after a successful pull, deploy auto-triggers restart for the same env
  AC6  — POST .../restart exists and runs under the existing middleware stack
  AC7  — restart uses `launchctl kickstart -k <label>` when launchd_label set
  AC8  — restart falls back to stop+start scripts when launchd_label absent
  AC9  — restart rejects when neither launchd_label nor stop/start present (4xx)
  AC10 — dashboard self-restart: detached helper, 202 Accepted, no inline kick
  AC11 — kickstart does not unload/bootout, so KeepAlive keeps respawning
  AC12 — UI exposes Deploy/Restart actions wired to the new endpoints
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager import deploy_actions as da  # noqa: E402


# ── unit: command builders / validators ──────────────────────────────────────


def test_build_pull_command_is_ff_only():
    """AC2: pull command is fast-forward only."""
    cmd = da.build_pull_command("develop")
    assert cmd[:3] == ["git", "pull", "--ff-only"]
    assert "develop" in cmd


def test_pull_command_never_merges_pushes_or_checks_out():
    """AC2: no merge/push/PR/checkout op is ever in the command."""
    cmd = da.build_pull_command("master")
    joined = " ".join(cmd)
    for forbidden in ("merge", "push", "checkout", "switch", "reset", "rebase"):
        assert forbidden not in joined


def test_require_deploy_target_returns_dir_and_branch():
    wd, br = da.require_deploy_target(
        {"host": "local", "working_dir": "/srv/x", "branch": "develop"}
    )
    assert wd == "/srv/x"
    assert br == "develop"


def test_require_deploy_target_missing_working_dir_raises():
    """AC4: absent working_dir is rejected before any shell command."""
    with pytest.raises(da.DeployActionError):
        da.require_deploy_target({"host": "local", "branch": "develop"})


def test_require_deploy_target_missing_branch_raises():
    """AC4: absent branch is rejected."""
    with pytest.raises(da.DeployActionError):
        da.require_deploy_target({"host": "local", "working_dir": "/srv/x"})


def test_require_deploy_target_none_raises():
    with pytest.raises(da.DeployActionError):
        da.require_deploy_target(None)


def test_build_kickstart_command_uses_gui_domain_label():
    """AC7: kickstart -k targets the configured label in the gui domain."""
    cmd = da.build_kickstart_command("com.example.svc", uid=501)
    assert cmd == ["launchctl", "kickstart", "-k", "gui/501/com.example.svc"]


def test_kickstart_never_unloads_or_bootouts():
    """AC11: kickstart -k does not unload/bootout — KeepAlive stays intact."""
    cmd = da.build_kickstart_command("com.example.svc", uid=501)
    joined = " ".join(cmd)
    assert "-k" in cmd
    for forbidden in ("unload", "bootout", "bootstrap", "remove", "disable"):
        assert forbidden not in joined


def test_require_restart_target_ok_with_launchd_label():
    da.require_restart_target({"launchd_label": "com.x"})  # no raise


def test_require_restart_target_ok_with_stop_start_scripts():
    da.require_restart_target({"stop_script": "/s/stop.sh", "start_script": "/s/start.sh"})


def test_require_restart_target_rejects_when_nothing_configured():
    """AC9: neither launchd_label nor stop/start → error."""
    with pytest.raises(da.DeployActionError):
        da.require_restart_target({"host": "local", "working_dir": "/srv/x"})


def test_require_restart_target_rejects_partial_scripts():
    """AC9: only one of stop/start is not enough."""
    with pytest.raises(da.DeployActionError):
        da.require_restart_target({"stop_script": "/s/stop.sh"})


def test_is_self_restart_true_for_dashboard_label():
    """AC10: the dashboard's own label routes to the self-restart path."""
    assert da.is_self_restart({"launchd_label": da.DASHBOARD_LAUNCHD_LABEL}) is True
    assert da.is_self_restart({"launchd_label": "com.other"}) is False


def test_self_restart_command_is_detached_sleep_then_kickstart():
    """AC10: helper sleeps then kickstarts (so the response can flush first)."""
    cmd = da.build_self_restart_command(da.DASHBOARD_LAUNCHD_LABEL, uid=501)
    assert cmd[0] == "sh"
    assert "sleep" in cmd[2]
    assert "launchctl kickstart -k gui/501/com.commander.dashboard" in cmd[2]


# ── endpoint tests ───────────────────────────────────────────────────────────


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope, project, key)
            )
        """))
        conn.commit()
    return engine


_PROJECTS = [
    {"repo": "zealchaiwut/commander"},
    {"repo": "owner/perf-coach"},
]


@pytest.fixture()
def client_ctx():
    """Yield (client, srv, settings_repo) with in-memory DB + commander/perf-coach."""
    engine = _make_engine()

    for mod in (
        "server",
        "services.sprint_manager.settings_repo",
        "services.sprint_manager.deploy_config_schema",
        "services.sprint_manager.deploy_actions",
    ):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(srv, "_settings_repo", settings_repo):
            with patch.object(srv.projects_module, "get_project_environments", return_value={}):
                with patch.object(srv, "_derive_project_environments", return_value={}):
                    client = TestClient(srv.app, raise_server_exceptions=False)
                    yield client, srv, settings_repo


def _save_deploy_config(srv, settings_repo, env_overrides):
    """Persist a project-scoped deploy config override for commander."""
    from services.sprint_manager.deploy_config_schema import DEPLOY_CONFIG_KEY
    settings_repo.set_setting(
        "project", DEPLOY_CONFIG_KEY, env_overrides, project="zealchaiwut/commander"
    )


def _completed(cmd, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def test_deploy_route_registered(client_ctx):
    """AC1: the deploy endpoint exists on the app."""
    client, srv, repo = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/deploy" in paths


def test_restart_route_registered(client_ctx):
    """AC6: the restart endpoint exists on the app."""
    client, srv, repo = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/restart" in paths


def test_deploy_runs_pull_in_working_dir_and_returns_output(client_ctx):
    """AC2/AC3/AC5: pull runs in working_dir; response has stdout + HEAD; restart fires."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/commander-uat",
                "branch": "develop", "launchd_label": "com.commander.uat"},
    })

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append((cmd, kw.get("cwd")))
        if cmd[:2] == ["git", "pull"]:
            return _completed(cmd, stdout="Updating 111..222\nFast-forward\n")
        if cmd[:2] == ["git", "rev-parse"]:
            return _completed(cmd, stdout="deadbeefcafe\n")
        if cmd[0] == "launchctl":
            return _completed(cmd, stdout="")
        return _completed(cmd)

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/deploy")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "Fast-forward" in data["pull_output"]
    assert data["head"] == "deadbeefcafe"

    # pull ran in working_dir, fast-forward only
    pull_call = next(c for c in calls if c[0][:2] == ["git", "pull"])
    assert pull_call[1] == "/srv/commander-uat"
    assert "--ff-only" in pull_call[0]
    # AC5: a restart (kickstart) fired after the pull
    assert any(c[0][0] == "launchctl" for c in calls)
    # AC2: never merged/pushed
    assert not any("merge" in c[0] or "push" in c[0] for c in calls)


def test_deploy_rejects_missing_working_dir_without_shelling_out(client_ctx):
    """AC4: missing working_dir → 4xx and no subprocess call."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "branch": "develop", "working_dir": ""},
    })

    run_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        with patch.object(srv.subprocess, "Popen", MagicMock()) as popen_mock:
            resp = client.post("/api/projects/commander/environments/uat/deploy")

    assert 400 <= resp.status_code < 500
    run_mock.assert_not_called()
    popen_mock.assert_not_called()


def test_restart_uses_kickstart_when_label_present(client_ctx):
    """AC7: restart shells launchctl kickstart -k for the configured label."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "launchd_label": "com.commander.uat"},
    })

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return _completed(cmd, stdout="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 200, resp.text
    kick = next(c for c in calls if c[0] == "launchctl")
    assert kick[1] == "kickstart"
    assert "-k" in kick
    assert any("com.commander.uat" in part for part in kick)
    # AC11: no unload/bootout that would break KeepAlive
    assert not any("unload" in c or "bootout" in c for c in calls)


def test_restart_falls_back_to_scripts_without_label(client_ctx):
    """AC8: with no launchd_label, run stop then start; no launchctl call."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "stop_script": "/srv/stop.sh", "start_script": "/srv/start.sh"},
    })

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return _completed(cmd, stdout="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 200, resp.text
    flat = " ".join(" ".join(c) for c in calls)
    assert "/srv/stop.sh" in flat
    assert "/srv/start.sh" in flat
    # stop must run before start
    stop_idx = next(i for i, c in enumerate(calls) if "/srv/stop.sh" in " ".join(c))
    start_idx = next(i for i, c in enumerate(calls) if "/srv/start.sh" in " ".join(c))
    assert stop_idx < start_idx
    # AC8: no launchctl involved
    assert not any(c and c[0] == "launchctl" for c in calls)


def test_restart_rejects_when_nothing_configured(client_ctx):
    """AC9: neither launchd_label nor stop/start scripts → 4xx."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop"},
    })

    run_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        resp = client.post("/api/projects/commander/environments/uat/restart")

    assert 400 <= resp.status_code < 500
    run_mock.assert_not_called()


def test_dashboard_self_restart_is_detached_and_202(client_ctx):
    """AC10: restarting the dashboard env detaches a helper and returns 202."""
    client, srv, settings_repo = client_ctx
    # commander prd seed already carries launchd_label=com.commander.dashboard
    run_mock = MagicMock()
    popen_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        with patch.object(srv.subprocess, "Popen", popen_mock):
            resp = client.post("/api/projects/commander/environments/prd/restart")

    assert resp.status_code == 202, resp.text
    # detached helper spawned; no inline kickstart that would kill the worker
    popen_mock.assert_called_once()
    _, kwargs = popen_mock.call_args
    assert kwargs.get("start_new_session") is True
    run_mock.assert_not_called()


# ── AC12: UI wiring ──────────────────────────────────────────────────────────


def _project_html():
    return (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


def test_ui_has_deploy_and_restart_actions():
    """AC12: project settings UI exposes Deploy and Restart actions."""
    html = _project_html()
    assert "envDeploy" in html
    assert "envRestart" in html


def test_ui_calls_deploy_and_restart_endpoints():
    """AC12: the UI fetches the new deploy/restart endpoints."""
    html = _project_html()
    assert "/deploy" in html and "/restart" in html
    assert "environments/" in html

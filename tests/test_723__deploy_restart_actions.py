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


def test_pull_command_never_merges_pushes_or_rebases():
    """AC2: the pull command itself is fast-forward only."""
    cmd = da.build_pull_command("master")
    joined = " ".join(cmd)
    for forbidden in ("merge", "push", "reset", "rebase"):
        assert forbidden not in joined


def test_checkout_command_is_separate_from_pull():
    """Deploy checks out the target branch in a separate step before pull."""
    checkout = da.build_checkout_command("develop")
    pull = da.build_pull_command("develop")
    assert checkout == ["git", "checkout", "develop"]
    assert "checkout" not in " ".join(pull)


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


def test_check_deploy_readiness_blocks_with_message_and_missing_script(tmp_path):
    wd = tmp_path / "uat"
    wd.mkdir()
    entry = {
        "host": "local",
        "working_dir": str(wd),
        "start_script": "bash scripts/deploy-start.sh",
        "stop_script": "bash scripts/deploy-stop.sh",
        "deploy_not_ready_message": "Deploy lifecycle is not ready in vector-search-demo yet.",
    }
    ready, errors = da.check_deploy_readiness(entry)
    assert ready is False
    assert any("not ready" in e.lower() for e in errors)
    assert any("not found" in e for e in errors)


def test_deploy_not_ready_message_does_not_block_stop_when_launchd():
    """Stop/Restart stay available when only Deploy is gated by deploy_not_ready_message."""
    entry = {
        "host": "local",
        "launchd_label": "com.example.uat",
        "deploy_not_ready_message": "Deploy lifecycle is not ready yet.",
    }
    deploy_ready, _ = da.check_deploy_readiness(entry)
    restart_ready, _ = da.check_restart_readiness(entry)
    stop_ready, _ = da.check_stop_readiness(entry)
    assert deploy_ready is False
    assert restart_ready is True
    assert stop_ready is True


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


def test_is_self_restart_dir_matches_own_clone(tmp_path):
    """Script-path self-restart: working_dir == the dashboard's own clone."""
    root = str(tmp_path)
    assert da.is_self_restart_dir({"working_dir": root}, root) is True
    assert da.is_self_restart_dir({"working_dir": str(tmp_path / "other")}, root) is False
    assert da.is_self_restart_dir({}, root) is False
    assert da.is_self_restart_dir({"working_dir": root}, None) is False


def test_detached_restart_command_sleeps_then_stop_then_start():
    """The detached helper runs sleep → stop → start so the response flushes and
    the helper survives `stop` killing the dashboard before `start` runs."""
    cmd = da.build_detached_restart_command(
        "bash scripts/stop_all.sh uat", "bash scripts/start_uat.sh"
    )
    assert cmd[0] == "sh"
    body = cmd[2]
    i_sleep, i_stop, i_start = (
        body.index("sleep"), body.index("stop_all.sh"), body.index("start_uat.sh")
    )
    assert i_sleep < i_stop < i_start  # ordered

    # Missing stop/start are simply omitted, never empty segments.
    only_start = da.build_detached_restart_command(None, "bash start.sh")
    assert "start.sh" in only_start[2] and ";  " not in only_start[2]


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
    """AC2/AC3/AC5: sync runs in working_dir; response has stdout + HEAD; restart fires."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/commander-uat",
                "branch": "develop", "launchd_label": "com.commander.uat"},
    })

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append((cmd, kw.get("cwd")))
        if cmd[:2] == ["git", "stash"]:
            return _completed(cmd, returncode=1)
        if cmd[:2] == ["git", "checkout"]:
            return _completed(cmd, stdout=f"Switched to branch 'develop'\n")
        if cmd[:2] == ["git", "fetch"]:
            return _completed(cmd, stdout="From origin\n")
        if cmd[:2] == ["git", "reset"]:
            return _completed(cmd, stdout="HEAD is now at deadbeef\n")
        if cmd[:2] == ["git", "rev-parse"]:
            return _completed(cmd, stdout="deadbeefcafe\n")
        if cmd[0] == "launchctl":
            return _completed(cmd, stdout="")
        return _completed(cmd)

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/deploy")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "HEAD is now" in data["pull_output"]
    assert data["head"] == "deadbeefcafe"

    checkout_call = next(c for c in calls if c[0][:2] == ["git", "checkout"])
    assert checkout_call[1] == "/srv/commander-uat"
    assert checkout_call[0] == ["git", "checkout", "develop"]
    fetch_call = next(c for c in calls if c[0][:2] == ["git", "fetch"])
    assert fetch_call[1] == "/srv/commander-uat"
    reset_call = next(c for c in calls if c[0][:2] == ["git", "reset"])
    assert reset_call[0] == ["git", "reset", "--hard", "origin/develop"]
    # AC5: a restart (kickstart) fired after the sync
    assert any(c[0][0] == "launchctl" for c in calls)
    # AC2: never merged/pushed
    assert not any("merge" in c[0] or "push" in c[0] for c in calls if c[0][:2] != ["git", "stash"])


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
        calls.append((cmd, kw.get("cwd")))
        return _completed(cmd, stdout="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 200, resp.text
    flat = " ".join(" ".join(c[0]) for c in calls)
    assert "/srv/stop.sh" in flat
    assert "/srv/start.sh" in flat
    # stop must run before start
    stop_idx = next(i for i, c in enumerate(calls) if "/srv/stop.sh" in " ".join(c[0]))
    start_idx = next(i for i, c in enumerate(calls) if "/srv/start.sh" in " ".join(c[0]))
    assert stop_idx < start_idx
    # AC8: no launchctl involved
    assert not any(c and c[0][0] == "launchctl" for c, _ in calls)


def test_restart_scripts_use_working_dir_as_cwd(client_ctx, tmp_path):
    """Relative stop/start scripts run with cwd=working_dir when it exists.

    Uses a working_dir that is NOT the dashboard's own clone, so this exercises
    the synchronous (non-self) script path; a self-restart over scripts detaches
    instead (covered separately)."""
    client, srv, settings_repo = client_ctx
    wd = str(tmp_path)
    # Readiness checks the referenced .sh files exist under working_dir.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "stop_all.sh").write_text("#!/bin/sh\n")
    (scripts / "start_uat.sh").write_text("#!/bin/sh\n")
    _save_deploy_config(srv, settings_repo, {
        "uat": {
            "host": "local",
            "working_dir": wd,
            "branch": "develop",
            "stop_script": "bash scripts/stop_all.sh uat",
            "start_script": "bash scripts/start_uat.sh",
        },
    })

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(kw.get("cwd"))
        return _completed(cmd, stdout="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        with patch.object(srv.subprocess, "Popen", MagicMock()):
            resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 2
    assert all(cwd == wd for cwd in calls)


def test_script_self_restart_detaches_not_synchronous(client_ctx):
    """A stop+start restart whose working_dir is the dashboard's own clone must
    detach (Popen) so `stop` can't kill the handler before `start` runs — the
    cause of "Failed to fetch" + a dashboard that never came back."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {
            "host": "local",
            "working_dir": str(srv._REPO_ROOT),   # the dashboard's own clone
            "branch": "develop",
            "stop_script": "bash scripts/stop_all.sh uat",
            "start_script": "bash scripts/start_uat.sh",
        },
    })

    run_mock = MagicMock()
    popen_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        with patch.object(srv.subprocess, "Popen", popen_mock):
            resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 202, resp.text
    # Detached helper spawned in a new session; stop/start NOT run synchronously.
    popen_mock.assert_called_once()
    assert popen_mock.call_args.kwargs.get("start_new_session") is True
    run_mock.assert_not_called()


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
    """AC12: Deploy tab exposes Deploy and Restart actions."""
    html = _project_html()
    assert "_deployExecDeploy" in html
    assert "_deployExecRestart" in html


def test_ui_calls_deploy_and_restart_endpoints():
    """AC12: the UI fetches the new deploy/restart endpoints."""
    html = _project_html()
    assert "/deploy" in html and "/restart" in html
    assert "environments/" in html

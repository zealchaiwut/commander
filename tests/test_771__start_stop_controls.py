"""Tests for issue #771 — Start/Stop controls + run state on environment cards.

AC coverage:
  AC1  — each env card shows four action buttons: Deploy, Restart, Start, Stop
  AC2  — Deploy = git pull → start/restart (unchanged semantics; no regression)
  AC3  — Start brings up the server without pulling new code (launchctl bootstrap
         or env start script)
  AC4  — Stop halts the server without destroying it (launchctl bootout or env
         stop script)
  AC5  — Start disabled when running; Stop disabled when stopped (UI logic)
  AC6  — Restart & Deploy disabled when stopped (Stop → Start flow chosen)
  AC7  — card surface shows run state: running | stopped | idle
  AC8  — run-state label updates after each action without a full page refresh
  AC9  — local Stop uses launchctl bootout (or stop script); Start uses launchctl
         bootstrap (or start script)
  AC10 — render envs: no stop equivalent → Start/Stop hidden (not disabled) with a
         tooltip explaining why (documented path)
  AC11 — no regression on existing Deploy/Restart behaviour
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
from services.sprint_manager import deploy_config_schema as dcs  # noqa: E402


# ── unit: command builders / validators (AC3/AC4/AC7/AC9) ─────────────────────


def test_build_bootout_command_targets_gui_domain_label():
    """AC9: Stop on a launchd env uses `launchctl bootout gui/<uid>/<label>`."""
    cmd = da.build_bootout_command("com.commander.uat", uid=501)
    assert cmd == ["launchctl", "bootout", "gui/501/com.commander.uat"]


def test_build_bootstrap_command_targets_domain_and_plist():
    """AC9: Start on a launchd env uses `launchctl bootstrap gui/<uid> <plist>`."""
    cmd = da.build_bootstrap_command("/Lib/LaunchAgents/x.plist", uid=501)
    assert cmd == ["launchctl", "bootstrap", "gui/501", "/Lib/LaunchAgents/x.plist"]


def test_build_print_command_targets_service():
    """AC7: run-state probe uses `launchctl print gui/<uid>/<label>`."""
    cmd = da.build_print_command("com.commander.uat", uid=501)
    assert cmd == ["launchctl", "print", "gui/501/com.commander.uat"]


def test_interpret_run_state_running_when_print_succeeds():
    """AC7: print returncode 0 → the service is bootstrapped → running."""
    assert da.interpret_run_state(0) == "running"


def test_interpret_run_state_stopped_when_print_fails():
    """AC7: print non-zero → service has been booted out → stopped."""
    assert da.interpret_run_state(1) == "stopped"
    assert da.interpret_run_state(113) == "stopped"


def test_launchd_plist_getter():
    """AC9: the plist path is read from the env config for bootstrap."""
    assert da.launchd_plist({"launchd_plist": " /x.plist "}) == "/x.plist"
    assert da.launchd_plist({}) is None


def test_require_stop_target_accepts_label_or_stop_script():
    """AC4: Stop is valid with a launchd_label or a stop script."""
    da.require_stop_target({"launchd_label": "com.x"})
    da.require_stop_target({"stop_script": "/stop.sh"})
    da.require_stop_target({"stop": "/stop.sh"})


def test_require_stop_target_rejects_when_nothing_configured():
    """AC4: no launchd_label and no stop script → rejected before shelling out."""
    with pytest.raises(da.DeployActionError):
        da.require_stop_target({"host": "local"})


def test_require_start_target_accepts_label_plus_plist_or_start_script():
    """AC3: Start needs (launchd_label AND launchd_plist) or a start script."""
    da.require_start_target({"launchd_label": "com.x", "launchd_plist": "/x.plist"})
    da.require_start_target({"start_script": "/start.sh"})
    da.require_start_target({"start": "/start.sh"})


def test_require_start_target_rejects_label_without_plist():
    """AC3: a launchd_label with no plist (and no start script) → rejected."""
    with pytest.raises(da.DeployActionError):
        da.require_start_target({"launchd_label": "com.x"})


def test_require_start_target_rejects_when_nothing_configured():
    """AC3: nothing configured → rejected before any shell command."""
    with pytest.raises(da.DeployActionError):
        da.require_start_target({"host": "local"})


# ── unit: overview entries advertise start/stop support (AC10) ────────────────


def test_overview_entry_local_supports_start_stop():
    """AC9/AC10: local cards advertise start/stop support so the UI shows them."""
    entries = dcs.overview_entries_for(
        "commander", {"uat": {"host": "local", "branch": "develop"}}
    )
    uat = next(e for e in entries if e["env"] == "uat")
    assert uat["start_stop_supported"] is True


def test_overview_entry_render_does_not_support_start_stop():
    """AC10: render cards advertise NO start/stop support so the UI hides them."""
    entries = dcs.overview_entries_for(
        "commander", {"prd": {"host": "render", "render_service_id": "srv-1"}}
    )
    prd = next(e for e in entries if e["env"] == "prd")
    assert prd["start_stop_supported"] is False


# ── endpoint fixture (mirrors issue #723 setup) ───────────────────────────────


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
    from services.sprint_manager.deploy_config_schema import DEPLOY_CONFIG_KEY
    settings_repo.set_setting(
        "project", DEPLOY_CONFIG_KEY, env_overrides, project="zealchaiwut/commander"
    )


def _completed(cmd, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


# ── endpoint: routes registered (AC3/AC4/AC7) ─────────────────────────────────


def test_start_route_registered(client_ctx):
    client, srv, _ = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/start" in paths


def test_stop_route_registered(client_ctx):
    client, srv, _ = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/stop" in paths


def test_run_state_route_registered(client_ctx):
    client, srv, _ = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/run-state" in paths


# ── endpoint: Stop (AC4/AC9) ──────────────────────────────────────────────────


def test_stop_uses_bootout_when_label_present(client_ctx):
    """AC9: Stop on a launchd env runs `launchctl bootout` for the label."""
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
        resp = client.post("/api/projects/commander/environments/uat/stop")

    assert resp.status_code == 200, resp.text
    boot = next(c for c in calls if c[0] == "launchctl")
    assert boot[1] == "bootout"
    assert any("com.commander.uat" in part for part in boot)


def test_stop_falls_back_to_stop_script_without_label(client_ctx):
    """AC9: with no launchd_label, Stop runs the configured stop script only."""
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
        resp = client.post("/api/projects/commander/environments/uat/stop")

    assert resp.status_code == 200, resp.text
    flat = " ".join(" ".join(c) for c in calls)
    assert "/srv/stop.sh" in flat
    # Stop must not run the start script.
    assert "/srv/start.sh" not in flat


def test_stop_rejects_when_nothing_configured(client_ctx):
    """AC4: no launchd_label and no stop script → 4xx, no subprocess."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop"},
    })
    run_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        resp = client.post("/api/projects/commander/environments/uat/stop")
    assert 400 <= resp.status_code < 500
    run_mock.assert_not_called()


# ── endpoint: Start (AC3/AC9) ─────────────────────────────────────────────────


def test_start_uses_bootstrap_when_label_and_plist_present(client_ctx):
    """AC9: Start on a launchd env runs `launchctl bootstrap` with the plist."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "launchd_label": "com.commander.uat",
                "launchd_plist": "/Lib/LaunchAgents/uat.plist"},
    })
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return _completed(cmd, stdout="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/start")

    assert resp.status_code == 200, resp.text
    boot = next(c for c in calls if c[0] == "launchctl")
    assert boot[1] == "bootstrap"
    assert "/Lib/LaunchAgents/uat.plist" in boot
    # AC3: Start never pulls code.
    assert not any(c[:2] == ["git", "pull"] for c in calls)


def test_start_falls_back_to_start_script_without_label(client_ctx):
    """AC9: with no launchd_label, Start runs the configured start script only."""
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
        resp = client.post("/api/projects/commander/environments/uat/start")

    assert resp.status_code == 200, resp.text
    flat = " ".join(" ".join(c) for c in calls)
    assert "/srv/start.sh" in flat
    # Start must not run the stop script.
    assert "/srv/stop.sh" not in flat


def test_start_rejects_when_nothing_configured(client_ctx):
    """AC3: no start strategy → 4xx, no subprocess."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop"},
    })
    run_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        resp = client.post("/api/projects/commander/environments/uat/start")
    assert 400 <= resp.status_code < 500
    run_mock.assert_not_called()


# ── endpoint: run-state (AC7) ─────────────────────────────────────────────────


def test_run_state_running_when_print_succeeds(client_ctx):
    """AC7: launchctl print rc 0 → state running."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "launchd_label": "com.commander.uat"},
    })
    with patch.object(srv.subprocess, "run",
                      side_effect=lambda cmd, *a, **kw: _completed(cmd, returncode=0)):
        resp = client.get("/api/projects/commander/environments/uat/run-state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "running"


def test_run_state_stopped_when_print_fails(client_ctx):
    """AC7: launchctl print non-zero → state stopped."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "launchd_label": "com.commander.uat"},
    })
    with patch.object(srv.subprocess, "run",
                      side_effect=lambda cmd, *a, **kw: _completed(cmd, returncode=1)):
        resp = client.get("/api/projects/commander/environments/uat/run-state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "stopped"


def test_run_state_idle_when_no_launchd_label(client_ctx):
    """AC7: a script-only env with no port has no probe → idle (unknown)."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "stop_script": "/srv/stop.sh", "start_script": "/srv/start.sh"},
    })
    run_mock = MagicMock()
    with patch.object(srv.subprocess, "run", run_mock):
        with patch.object(da, "restart_port", return_value=None):
            resp = client.get("/api/projects/commander/environments/uat/run-state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "idle"
    run_mock.assert_not_called()


def test_run_state_running_when_port_listening(client_ctx):
    """Script-managed env: probe configured port when no launchd_label."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {
            "host": "local",
            "working_dir": "/srv/x",
            "branch": "develop",
            "port": 8001,
            "stop_script": "/srv/stop.sh",
            "start_script": "/srv/start.sh",
        },
    })
    with patch.object(da, "probe_port_listening", return_value=True):
        resp = client.get("/api/projects/commander/environments/uat/run-state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "running"
    assert resp.json().get("probe") == "port"


def test_run_state_stopped_when_port_not_listening(client_ctx):
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {
            "host": "local",
            "working_dir": "/srv/x",
            "branch": "develop",
            "port": 8001,
            "stop_script": "/srv/stop.sh",
            "start_script": "/srv/start.sh",
        },
    })
    with patch.object(da, "probe_port_listening", return_value=False):
        resp = client.get("/api/projects/commander/environments/uat/run-state")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "stopped"


# ── no regression on Deploy / Restart (AC2/AC11) ──────────────────────────────


def test_restart_still_works_after_start_stop_added(client_ctx):
    """AC11: existing Restart endpoint still kickstarts (no regression)."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(srv, settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/x", "branch": "develop",
                "launchd_label": "com.commander.uat"},
    })
    calls = []
    with patch.object(srv.subprocess, "run",
                      side_effect=lambda cmd, *a, **kw: (calls.append(cmd) or _completed(cmd))):
        resp = client.post("/api/projects/commander/environments/uat/restart")
    assert resp.status_code == 200, resp.text
    kick = next(c for c in calls if c[0] == "launchctl")
    assert kick[1] == "kickstart"


# ── frontend (project.html string contract) ───────────────────────────────────


def _project_html():
    return (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


def test_ui_has_four_action_buttons():
    """AC1: card actions expose Deploy, Restart, Start and Stop."""
    html = _project_html()
    assert "deployCardStart(" in html
    assert "deployCardStop(" in html
    assert "deployCardDeploy(" in html
    assert "deployCardRestart(" in html
    # The four button labels are rendered via the shared button builder; assert
    # each label string is present so all four actions are surfaced.
    assert "'Deploy'" in html
    assert "'Restart'" in html
    assert "'Start'" in html
    assert "'Stop'" in html


def test_ui_start_stop_call_endpoints():
    """AC3/AC4: Start/Stop POST the new endpoints."""
    html = _project_html()
    assert "'/start'" in html or "/start'" in html
    assert "'/stop'" in html or "/stop'" in html


def test_ui_has_run_state_pill():
    """AC7: a run-state pill surfaces running | stopped | idle."""
    html = _project_html()
    assert "deploy-card-runstate-" in html
    assert "_deploySetRunState" in html


def test_ui_disables_buttons_by_run_state():
    """AC5/AC6: enable/disable logic keyed on running/stopped run state."""
    html = _project_html()
    # The actions renderer keys disabled state on run state.
    assert "_deployRunState" in html
    assert "'running'" in html
    assert "'stopped'" in html


def test_ui_refreshes_run_state_after_action():
    """AC8: run state is re-read after an action without a full page refresh."""
    html = _project_html()
    assert "_deployRefreshRunState" in html
    assert "/run-state" in html


def test_ui_hides_start_stop_for_render_with_tooltip():
    """AC10: render cards hide Start/Stop (not disable) and explain via tooltip."""
    html = _project_html()
    assert "start_stop_supported" in html
    # A tooltip string explaining Render has no stop/start.
    assert "Render" in html and "does not support" in html

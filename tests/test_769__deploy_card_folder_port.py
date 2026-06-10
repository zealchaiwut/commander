"""Tests for issue #769 — show and edit run folder and port on Deploy cards.

AC coverage:
  AC1  — local-host card displays the configured working_dir
  AC2  — local-host card displays the configured port
  AC3  — working_dir and port are editable via an inline control on the card
  AC4  — edits persist: working_dir updates existing field, port saved to `port`
  AC5  — saving a working_dir that does not exist on disk is rejected (error)
  AC6  — saving a working_dir that exists but is not a git repo is rejected
  AC7  — saving a port that is not a valid integer (1–65535) is rejected
  AC8  — saving a port already in use on the host is rejected
  AC9  — start/restart commands use the configured port; no hardcoded 8000/8001
  AC10 — non-local-host (render) cards are unaffected (no folder/port fields)
"""

import os
import socket
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

from services.sprint_manager import deploy_config_schema as dcs  # noqa: E402
from services.sprint_manager import deploy_actions as da  # noqa: E402
from services.sprint_manager import deploy_validation as dv  # noqa: E402


# ── AC1 / AC2 / AC10: overview entries carry working_dir + port for local ─────


def test_overview_local_entry_includes_working_dir_and_port():
    """AC1/AC2: a local env's overview entry surfaces working_dir and port."""
    merged = {
        "uat": {
            "host": "local",
            "working_dir": "/srv/commander-uat",
            "branch": "develop",
            "port": 8001,
        }
    }
    entries = dcs.overview_entries_for("commander", merged)
    uat = next(e for e in entries if e["env"] == "uat")
    assert uat["working_dir"] == "/srv/commander-uat"
    assert uat["port"] == 8001


def test_overview_local_entry_port_defaults_per_env():
    """AC2: when no port is stored, the per-env default is surfaced."""
    merged = {
        "prd": {"host": "local", "working_dir": "/srv/prd", "branch": "master"},
        "uat": {"host": "local", "working_dir": "/srv/uat", "branch": "develop"},
    }
    entries = {e["env"]: e for e in dcs.overview_entries_for("commander", merged)}
    assert entries["prd"]["port"] == 8000
    assert entries["uat"]["port"] == 8001


def test_overview_render_entry_has_no_folder_or_port():
    """AC10: render (non-local) entries never carry working_dir/port fields."""
    merged = {"prd": {"host": "render", "render_service_id": "srv-1"}}
    entries = dcs.overview_entries_for("perf-coach", merged)
    prd = next(e for e in entries if e["env"] == "prd")
    assert "working_dir" not in prd
    assert "port" not in prd


def test_port_default_helper():
    """AC2: the per-env port default helper maps prd→8000, uat→8001."""
    assert dcs.port_default("prd") == 8000
    assert dcs.port_default("uat") == 8001
    assert dcs.port_default("staging") is None


# ── AC7: port integer-range validation ────────────────────────────────────────


@pytest.mark.parametrize("good", [1, "1", 8000, "3456", 65535])
def test_validate_port_accepts_valid_integers(good):
    assert dv.validate_port(good) == int(good)


@pytest.mark.parametrize("bad", ["abc", "", "12.5", 0, -1, 65536, 99999, None, True])
def test_validate_port_rejects_invalid(bad):
    with pytest.raises(dv.DeployValidationError):
        dv.validate_port(bad)


# ── AC8: port-in-use detection ────────────────────────────────────────────────


def test_port_in_use_true_for_a_bound_port():
    """AC8: a port held by a live listener is reported in use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert dv.port_in_use(port) is True
    finally:
        s.close()


def test_port_in_use_false_for_a_free_port():
    """AC8: a free port is not reported in use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()  # release it
    assert dv.port_in_use(free_port) is False


# ── AC5 / AC6: working_dir validation ─────────────────────────────────────────


def test_validate_working_dir_rejects_missing_path(tmp_path):
    """AC5: a path that does not exist on disk is rejected."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(dv.DeployValidationError):
        dv.validate_working_dir(str(missing))


def test_validate_working_dir_rejects_non_git_dir(tmp_path):
    """AC6: an existing directory that is not a git repo is rejected."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(dv.DeployValidationError):
        dv.validate_working_dir(str(plain))


def test_validate_working_dir_accepts_git_clone(tmp_path):
    """AC5/AC6: an existing git repo passes validation."""
    repo = tmp_path / "clone"
    repo.mkdir()
    (repo / ".git").mkdir()
    dv.validate_working_dir(str(repo))  # no raise


# ── AC9: start/restart use the configured port; no hardcoded 8000/8001 ────────


def test_restart_port_reads_configured_port():
    assert da.restart_port({"host": "local", "port": 3456}) == 3456
    assert da.restart_port({"host": "local", "port": "3456"}) == 3456
    assert da.restart_port({"host": "local"}) is None


def test_build_restart_env_injects_port():
    """AC9: the restart env carries PORT=<configured> for the start command."""
    env = da.build_restart_env({"host": "local", "port": 3456}, base_env={"FOO": "bar"})
    assert env["PORT"] == "3456"
    assert env["FOO"] == "bar"


def test_build_restart_env_none_when_no_port():
    assert da.build_restart_env({"host": "local"}, base_env={}) is None


def test_deploy_actions_has_no_hardcoded_dashboard_ports():
    """AC9: no literal 8000/8001 remains in the deploy command-building module."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "deploy_actions.py").read_text()
    assert "8000" not in src
    assert "8001" not in src


def test_start_scripts_honor_port_override():
    """AC9: start_prd.sh / start_uat.sh respect a PORT env override (not hardcoded)."""
    prd = (REPO_ROOT / "scripts" / "start_prd.sh").read_text()
    uat = (REPO_ROOT / "scripts" / "start_uat.sh").read_text()
    assert "${PORT:-8000}" in prd
    assert "${PORT:-8001}" in uat


# ── Endpoint tests (validation + persistence + restart wiring) ────────────────


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
        "services.sprint_manager.deploy_validation",
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


def _save_deploy_config(settings_repo, env_overrides):
    from services.sprint_manager.deploy_config_schema import DEPLOY_CONFIG_KEY
    settings_repo.set_setting(
        "project", DEPLOY_CONFIG_KEY, env_overrides, project="zealchaiwut/commander"
    )


def test_validate_route_registered(client_ctx):
    """AC3/AC5-8: the inline-edit validation endpoint exists."""
    client, srv, repo = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/projects/{slug}/environments/{env}/deploy-config/validate" in paths


def test_validate_rejects_missing_working_dir(client_ctx, tmp_path):
    """AC5: validate endpoint rejects a non-existent folder with a 400 + message."""
    client, srv, repo = client_ctx
    resp = client.post(
        "/api/projects/commander/environments/uat/deploy-config/validate",
        json={"working_dir": str(tmp_path / "nope")},
    )
    assert resp.status_code == 400, resp.text
    assert "exist" in resp.text.lower()


def test_validate_rejects_non_git_working_dir(client_ctx, tmp_path):
    """AC6: validate endpoint rejects an existing non-git folder."""
    client, srv, repo = client_ctx
    plain = tmp_path / "plain"
    plain.mkdir()
    resp = client.post(
        "/api/projects/commander/environments/uat/deploy-config/validate",
        json={"working_dir": str(plain)},
    )
    assert resp.status_code == 400, resp.text
    assert "git" in resp.text.lower()


def test_validate_accepts_valid_git_dir(client_ctx, tmp_path):
    """AC5/AC6: validate passes for an existing git clone."""
    client, srv, repo = client_ctx
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / ".git").mkdir()
    resp = client.post(
        "/api/projects/commander/environments/uat/deploy-config/validate",
        json={"working_dir": str(clone)},
    )
    assert resp.status_code == 200, resp.text


def test_validate_rejects_non_integer_port(client_ctx):
    """AC7: validate endpoint rejects a non-numeric port."""
    client, srv, repo = client_ctx
    resp = client.post(
        "/api/projects/commander/environments/uat/deploy-config/validate",
        json={"port": "abc"},
    )
    assert resp.status_code == 400, resp.text


def test_validate_rejects_in_use_port(client_ctx):
    """AC8: validate endpoint rejects a port already in use on the host."""
    client, srv, repo = client_ctx
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        resp = client.post(
            "/api/projects/commander/environments/uat/deploy-config/validate",
            json={"port": port},
        )
        assert resp.status_code == 400, resp.text
        assert "use" in resp.text.lower()
    finally:
        s.close()


def test_put_persists_working_dir_and_port(client_ctx, tmp_path):
    """AC4: working_dir updates the existing field and port is saved to `port`."""
    client, srv, settings_repo = client_ctx
    put = client.put(
        "/api/projects/commander/deploy-config",
        json={"uat": {"host": "local", "working_dir": "/srv/uat",
                      "branch": "develop", "port": 3456}},
    )
    assert put.status_code == 200, put.text
    data = client.get("/api/projects/commander/deploy-config").json()
    assert data["uat"]["working_dir"] == "/srv/uat"
    assert data["uat"]["port"] == 3456


def test_overview_endpoint_surfaces_port_for_local(client_ctx):
    """AC1/AC2: the deploy overview returns working_dir + port for a local env."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/uat",
                "branch": "develop", "port": 3456},
    })
    data = client.get("/api/deploy/overview").json()
    uat = next(e for e in data["environments"]
               if e["project"] == "commander" and e["env"] == "uat")
    assert uat["working_dir"] == "/srv/uat"
    assert uat["port"] == 3456


def test_restart_script_fallback_uses_configured_port(client_ctx):
    """AC9: restart runs the start script with PORT=<configured>, not 8000/8001."""
    client, srv, settings_repo = client_ctx
    _save_deploy_config(settings_repo, {
        "uat": {"host": "local", "working_dir": "/srv/uat", "branch": "develop",
                "stop_script": "/srv/stop.sh", "start_script": "/srv/start.sh",
                "port": 3456},
    })

    envs_seen = []

    def fake_run(cmd, *a, **kw):
        envs_seen.append(kw.get("env"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        resp = client.post("/api/projects/commander/environments/uat/restart")

    assert resp.status_code == 200, resp.text
    # the start (and stop) phase ran with PORT injected
    port_envs = [e for e in envs_seen if e and e.get("PORT") == "3456"]
    assert port_envs, "expected PORT=3456 to be injected into the restart env"


# ── AC3 / AC10: frontend inline-edit wiring ───────────────────────────────────


def _project_html():
    return (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


def test_ui_has_inline_edit_controls():
    """AC3: the Deploy card exposes inline edit + save handlers for folder/port."""
    html = _project_html()
    assert "deployEditField" in html
    assert "deploySaveField" in html


def test_ui_calls_validate_before_persisting():
    """AC5-8: the inline save flow calls the validate endpoint before PUT."""
    html = _project_html()
    assert "deploy-config/validate" in html


def test_ui_shows_folder_and_port_for_local_only():
    """AC1/AC2/AC10: folder + port rows are rendered for local hosts, gated on host."""
    html = _project_html()
    assert "'Folder'" in html
    assert "'Port'" in html
    # rows are built by a dedicated helper that is gated on a local-host check so
    # render cards stay unaffected (AC10).
    assert "_deployFolderPortRows" in html
    assert "_deployFieldRow" in html
    assert "c.host !== 'local'" in html

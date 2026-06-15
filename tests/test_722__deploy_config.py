"""Tests for issue #722 — per-environment deploy config in project settings.

AC coverage:
  AC1  — settings store accepts a deploy_config object keyed by env name (prd, uat)
  AC2  — each env entry has a host field: "local" or "render"
  AC3  — host=local supports working_dir, branch, optional launchd_label, restart_script
  AC4  — host=render supports render_service_id, render_api_key (masked secret)
  AC5  — config persists via settings_repo; JSON fallback when Neon unavailable
  AC6  — GET returns per-env config; render_api_key never cleartext;
         render_api_key_set bool + masked value
  AC7  — PUT saves full config; a new render_api_key replaces the stored secret
  AC8  — seed defaults present for commander (prd local, uat local)
  AC9  — seed defaults present for perf-coach (prd render, uat local)
  AC10 — PUT with no render_api_key leaves stored key unchanged
  AC11 — GET masking and PUT key-replacement logic (unit-level)
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager import deploy_config_schema as dcs  # noqa: E402


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
    ):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(srv, "_settings_repo", settings_repo):
            # Keep working_dir enrichment deterministic — no real disk layout.
            with patch.object(srv.projects_module, "get_project_environments", return_value={}):
                with patch.object(srv, "_derive_project_environments", return_value={}):
                    client = TestClient(srv.app, raise_server_exceptions=False)
                    yield client, srv, settings_repo


# ── AC11: unit — mask_secret ─────────────────────────────────────────────────

def test_mask_secret_example():
    """rnd_live_secret masks to rnd_...cret (first 4 + ... + last 4)."""
    assert dcs.mask_secret("rnd_live_secret") == "rnd_...cret"


def test_mask_secret_empty_is_none():
    assert dcs.mask_secret("") is None
    assert dcs.mask_secret(None) is None


def test_mask_secret_short_fully_masked():
    """Short secrets reveal no meaningful portion."""
    masked = dcs.mask_secret("abc")
    assert masked == "..."
    assert "abc" not in masked


# ── AC6 / AC11: unit — build_deploy_config_response masking ───────────────────

def test_build_response_strips_raw_render_api_key():
    merged = {"prd": {"host": "render", "render_service_id": "srv-1", "render_api_key": "rnd_live_secret"}}
    resp = dcs.build_deploy_config_response(merged)
    assert "render_api_key" not in resp["prd"]
    assert resp["prd"]["render_api_key_set"] is True
    assert resp["prd"]["render_api_key_masked"] == "rnd_...cret"
    # raw must not leak anywhere
    assert "rnd_live_secret" not in str(resp)


def test_build_response_render_key_unset_flag_false():
    merged = {"prd": {"host": "render"}}
    resp = dcs.build_deploy_config_response(merged)
    assert resp["prd"]["render_api_key_set"] is False
    assert resp["prd"]["render_api_key_masked"] is None


def test_build_response_local_fills_default_branch():
    merged = {"prd": {"host": "local"}, "uat": {"host": "local"}}
    resp = dcs.build_deploy_config_response(merged)
    assert resp["prd"]["branch"] == "master"
    assert resp["uat"]["branch"] == "develop"


# ── AC7 / AC10 / AC11: unit — merge_for_put key-replacement ───────────────────

def test_merge_put_new_key_replaces_stored():
    current = {"prd": {"host": "render", "render_api_key": "old_secret_value"}}
    incoming = {"prd": {"host": "render", "render_api_key": "new_secret_value"}}
    merged = dcs.merge_for_put(current, incoming)
    assert merged["prd"]["render_api_key"] == "new_secret_value"


def test_merge_put_absent_key_preserves_stored():
    current = {"prd": {"host": "render", "render_service_id": "srv-old", "render_api_key": "kept_secret"}}
    incoming = {"prd": {"host": "render", "render_service_id": "srv-new"}}
    merged = dcs.merge_for_put(current, incoming)
    assert merged["prd"]["render_api_key"] == "kept_secret"
    assert merged["prd"]["render_service_id"] == "srv-new"


def test_merge_put_null_key_preserves_stored():
    current = {"prd": {"host": "render", "render_api_key": "kept_secret"}}
    incoming = {"prd": {"host": "render", "render_api_key": None}}
    merged = dcs.merge_for_put(current, incoming)
    assert merged["prd"]["render_api_key"] == "kept_secret"


def test_merge_seed_repairs_legacy_script_paths():
    seed = dcs.seed_for("commander")
    stored = {
        "uat": {
            "start_script": "bash ../../scripts/start_uat.sh",
            "stop_script": "bash ../../scripts/stop_all.sh uat",
        }
    }
    merged = dcs.merge_seed(seed, stored)
    assert merged["uat"]["start_script"] == "bash scripts/start_uat.sh"
    assert merged["uat"]["stop_script"] == "bash scripts/stop_all.sh uat"


# ── AC8: seed defaults — commander ────────────────────────────────────────────

def test_get_commander_prd_seed_defaults(client_ctx):
    client, srv, repo = client_ctx
    resp = client.get("/api/projects/commander/deploy-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["prd"]["host"] == "local"
    assert data["prd"]["launchd_label"] == "com.commander.dashboard"
    assert data["prd"]["branch"] == "master"
    assert data["prd"]["start_script"] == "bash scripts/start_prd.sh"
    assert data["prd"]["stop_script"] == "bash scripts/stop_all.sh prd"
    # no cleartext key anywhere
    assert "render_api_key" not in data["prd"]


def test_get_commander_uat_seed_defaults(client_ctx):
    client, srv, repo = client_ctx
    data = client.get("/api/projects/commander/deploy-config").json()
    assert data["uat"]["host"] == "local"
    assert data["uat"]["branch"] == "develop"


def test_commander_uat_scripts_are_relative_to_working_dir():
    """UAT deploy scripts must resolve inside the shared prd clone working_dir."""
    from pathlib import Path

    from services.sprint_manager import deploy_actions as da

    seed = dcs.seed_for("commander")["uat"]
    assert seed.get("shared_working_dir") == "prd"
    assert "../../" not in seed["start_script"]
    assert seed["start_script"] == "bash scripts/start_uat.sh"
    assert seed["stop_script"] == "bash scripts/stop_all.sh uat"

    entry = dict(seed)
    entry["working_dir"] = str(Path(__file__).resolve().parent.parent)
    ready, errors = da.check_deploy_readiness(entry)
    assert ready, errors


def test_enrich_shared_working_dir_prefers_prd_clone():
    """commander uat defaults to the prd clone path when shared_working_dir is set."""
    resp = {"uat": {"host": "local", "shared_working_dir": "prd"}}
    dcs.enrich_local_working_dirs(
        resp,
        {"prd": "/dev/commander/prd", "uat": "/dev/commander/uat"},
    )
    assert resp["uat"]["working_dir"] == "/dev/commander/prd"


# ── AC9: seed defaults — perf-coach ───────────────────────────────────────────

def test_get_perfcoach_prd_seed_render(client_ctx):
    client, srv, repo = client_ctx
    resp = client.get("/api/projects/perf-coach/deploy-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["prd"]["host"] == "render"
    assert data["prd"]["render_api_key_set"] is False


def test_get_perfcoach_uat_seed_local(client_ctx):
    client, srv, repo = client_ctx
    data = client.get("/api/projects/perf-coach/deploy-config").json()
    assert data["uat"]["host"] == "local"
    assert data["uat"]["branch"] == "develop"


# ── AC6 / AC7: PUT render key, GET masked, no cleartext ───────────────────────

def test_put_render_key_then_get_masked(client_ctx):
    client, srv, repo = client_ctx
    put = client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "srv-abc123", "render_api_key": "rnd_live_secret"}},
    )
    assert put.status_code == 200, put.text
    data = client.get("/api/projects/perf-coach/deploy-config").json()
    assert data["prd"]["render_api_key_set"] is True
    assert data["prd"]["render_api_key_masked"] == "rnd_...cret"
    assert data["prd"]["render_service_id"] == "srv-abc123"
    # raw secret must not appear anywhere in the GET response
    assert "rnd_live_secret" not in client.get("/api/projects/perf-coach/deploy-config").text


# ── AC10: PUT without key preserves stored key ────────────────────────────────

def test_put_without_key_preserves(client_ctx):
    client, srv, repo = client_ctx
    client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "srv-abc123", "render_api_key": "rnd_live_secret"}},
    )
    # second PUT omits the key
    client.put(
        "/api/projects/perf-coach/deploy-config",
        json={"prd": {"host": "render", "render_service_id": "srv-xyz999"}},
    )
    data = client.get("/api/projects/perf-coach/deploy-config").json()
    assert data["prd"]["render_api_key_set"] is True
    assert data["prd"]["render_service_id"] == "srv-xyz999"


# ── AC3: local config persists ────────────────────────────────────────────────

def test_put_local_config_persists(client_ctx):
    client, srv, repo = client_ctx
    put = client.put(
        "/api/projects/commander/deploy-config",
        json={"uat": {"host": "local", "working_dir": "/srv/commander-uat",
                      "branch": "develop", "restart_script": "/srv/restart.sh"}},
    )
    assert put.status_code == 200, put.text
    data = client.get("/api/projects/commander/deploy-config").json()
    assert data["uat"]["working_dir"] == "/srv/commander-uat"
    assert data["uat"]["restart_script"] == "/srv/restart.sh"
    assert data["uat"].get("launchd_label") in (None, "")


# ── AC1 / AC2: validation — unsupported env / host rejected ───────────────────

def test_put_rejects_unsupported_env(client_ctx):
    client, srv, repo = client_ctx
    resp = client.put(
        "/api/projects/commander/deploy-config",
        json={"staging": {"host": "local"}},
    )
    assert resp.status_code == 400, resp.text
    assert "staging" in resp.text


def test_put_rejects_invalid_host(client_ctx):
    client, srv, repo = client_ctx
    resp = client.put(
        "/api/projects/commander/deploy-config",
        json={"prd": {"host": "aws"}},
    )
    assert resp.status_code == 400, resp.text


# ── 404 for unknown slug ──────────────────────────────────────────────────────

def test_get_404_unknown_slug(client_ctx):
    client, srv, repo = client_ctx
    assert client.get("/api/projects/nope/deploy-config").status_code == 404


def test_put_404_unknown_slug(client_ctx):
    client, srv, repo = client_ctx
    assert client.put("/api/projects/nope/deploy-config", json={"prd": {"host": "local"}}).status_code == 404


# ── AC5: JSON fallback when Neon unavailable ──────────────────────────────────

def test_json_fallback_round_trip(tmp_path, monkeypatch):
    """With no session factory (Neon down), reads/writes go to the JSON store."""
    for mod in (
        "server",
        "services.sprint_manager.settings_repo",
        "services.sprint_manager.deploy_config_schema",
    ):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    # Force fallback: no DB session, isolated JSON store.
    settings_repo._session_factory = None
    store_file = tmp_path / "settings_store.json"
    monkeypatch.setattr(settings_repo, "_fallback_store_path", lambda: store_file)

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=_PROJECTS):
        with patch.object(srv, "_settings_repo", settings_repo):
            with patch.object(srv.projects_module, "get_project_environments", return_value={}):
                with patch.object(srv, "_derive_project_environments", return_value={}):
                    client = TestClient(srv.app, raise_server_exceptions=False)

                    # read seed (no 500)
                    g = client.get("/api/projects/perf-coach/deploy-config")
                    assert g.status_code == 200, g.text

                    # write + read back via JSON fallback
                    p = client.put(
                        "/api/projects/perf-coach/deploy-config",
                        json={"prd": {"host": "render", "render_service_id": "srv-abc123",
                                      "render_api_key": "rnd_live_secret"}},
                    )
                    assert p.status_code == 200, p.text
                    data = client.get("/api/projects/perf-coach/deploy-config").json()
                    assert data["prd"]["render_api_key_set"] is True
                    assert data["prd"]["render_api_key_masked"] == "rnd_...cret"
                    assert store_file.exists()

"""Tests for the Deploy-tab "Copy PRD → UAT" data-copy feature.

AC coverage:
  AC1 — copy_strategy_for() resolves the registered strategy per project;
        None for an unregistered project (e.g. crux, pending its own
        SQLite→Postgres UAT migration).
  AC2 — overview_entries_for() sets copy_from_prd_supported=True only on the
        uat card of a project with a registered strategy; False everywhere else.
  AC3 — POST .../environments/{env}/copy-prd-bg rejects env != "uat" (400).
  AC4 — POST .../copy-prd-bg rejects a project with no registered strategy (400).
  AC5 — POST .../copy-prd-bg on a valid uat env starts a background job and
        returns {started: True, job_key}; a second call while running returns
        {started: False, already_running: True} instead of double-starting.
  AC6 — sqlite_file / json_dir strategies actually run backup -> stop -> copy
        -> start, in that order, and the copied file/dir has the source's
        content afterward (not just "some file exists").
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
import routers.data_copy_service as dc  # noqa: E402
import routers.deploy_progress_service as dps  # noqa: E402
import routers.environments as env_mod  # noqa: E402
import asyncio  # noqa: E402


# ── AC1: copy_strategy_for ────────────────────────────────────────────────────


def test_copy_strategy_for_viral_radar_is_sqlite_file():
    cfg = dcs.copy_strategy_for("viral-radar")
    assert cfg["strategy"] == "sqlite_file"
    assert cfg["db_filename"] == "viral-radar.db"


def test_copy_strategy_for_asset_studio_is_json_dir():
    cfg = dcs.copy_strategy_for("asset-studio")
    assert cfg["strategy"] == "json_dir"
    assert cfg["dir_name"] == "flows"


def test_copy_strategy_for_perf_coach_is_postgres_rowcopy():
    cfg = dcs.copy_strategy_for("perf-coach")
    assert cfg["strategy"] == "postgres_rowcopy"
    assert "copy_script" in cfg


def test_copy_strategy_for_crux_is_unregistered():
    """crux's uat still runs SQLite while prd runs Postgres — not wired up yet."""
    assert dcs.copy_strategy_for("crux") is None


def test_copy_strategy_for_unknown_project_is_none():
    assert dcs.copy_strategy_for("nonexistent-project") is None


# ── AC2: overview_entries_for copy_from_prd_supported flag ────────────────────


def test_overview_copy_supported_true_only_on_uat_card_for_registered_project():
    merged = {
        "prd": {"host": "local", "working_dir": "/x/prd"},
        "uat": {"host": "local", "working_dir": "/x/uat"},
    }
    entries = dcs.overview_entries_for("viral-radar", merged)
    by_env = {e["env"]: e for e in entries}
    assert by_env["uat"]["copy_from_prd_supported"] is True
    assert by_env["prd"]["copy_from_prd_supported"] is False


def test_overview_copy_supported_false_for_unregistered_project():
    merged = {
        "prd": {"host": "local", "working_dir": "/x/prd"},
        "uat": {"host": "local", "working_dir": "/x/uat"},
    }
    entries = dcs.overview_entries_for("crux", merged)
    by_env = {e["env"]: e for e in entries}
    assert by_env["uat"]["copy_from_prd_supported"] is False


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
    {"repo": "owner/viral-radar"},
    {"repo": "owner/crux"},
]


@pytest.fixture()
def client_ctx():
    """Yield (client, srv, settings_repo) with in-memory DB + registered projects."""
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


def test_copy_route_registered():
    """The copy-prd-bg endpoint exists on the environments router.

    Checked against env_mod.router.routes directly rather than srv.app.routes:
    this FastAPI version defers sub-router expansion until the first request
    (see the pre-existing, already-broken test_deploy_route_registered /
    test_restart_route_registered in test_723__deploy_restart_actions.py,
    which hit the same empty-until-first-request behavior — not something
    introduced here). The functional tests below (real POSTs through the
    TestClient) are the actual proof the route works end to end.
    """
    paths = {getattr(r, "path", None) for r in env_mod.router.routes}
    assert "/api/projects/{slug}/environments/{env}/copy-prd-bg" in paths


def test_copy_rejects_non_uat_env(client_ctx):
    """AC3: env must be 'uat' — direction is hard-locked PRD→UAT."""
    client, srv, repo = client_ctx
    resp = client.post("/api/projects/viral-radar/environments/prd/copy-prd-bg")
    assert resp.status_code == 400
    assert "uat" in resp.json()["detail"].lower()


def test_copy_rejects_project_with_no_strategy(client_ctx):
    """AC4: crux has no registered copy strategy yet."""
    client, srv, repo = client_ctx
    resp = client.post("/api/projects/crux/environments/uat/copy-prd-bg")
    assert resp.status_code == 400
    assert "strategy" in resp.json()["detail"].lower()


def test_copy_starts_job_for_valid_project(client_ctx):
    """AC5: a supported project's uat env starts a background job."""
    client, srv, repo = client_ctx
    with patch.object(dc, "run_copy_job", return_value=None):
        resp = client.post("/api/projects/viral-radar/environments/uat/copy-prd-bg")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True
    assert body["job_key"] == "uat@viral-radar"


def test_copy_second_call_while_running_does_not_double_start(client_ctx):
    """AC5: a second call while the job is still running reports already_running."""
    client, srv, repo = client_ctx
    key = dps.job_key("viral-radar", "uat")
    dps._JOBS[key] = {"status": "running"}
    try:
        resp = client.post("/api/projects/viral-radar/environments/uat/copy-prd-bg")
        assert resp.status_code == 200
        body = resp.json()
        assert body["started"] is False
        assert body["already_running"] is True
    finally:
        dps._JOBS.pop(key, None)


# ── AC6: sqlite_file / json_dir copy sequence ─────────────────────────────────


def test_sqlite_file_copy_backs_up_stops_copies_starts(tmp_path, monkeypatch):
    """AC6: sqlite_file runs backup -> stop -> copy -> start, and the copied
    file actually contains prd's bytes, not just "a file exists"."""
    prd_dir = tmp_path / "prd"
    uat_dir = tmp_path / "uat"
    prd_dir.mkdir()
    uat_dir.mkdir()
    (prd_dir / "viral-radar.db").write_bytes(b"PRD-DATA-V2")
    (uat_dir / "viral-radar.db").write_bytes(b"stale-uat-data")

    calls: list[str] = []

    def fake_resolve(slug):
        return f"owner/{slug}"

    def fake_merged(slug, repo):
        return {
            "prd": {"host": "local", "working_dir": str(prd_dir)},
            "uat": {"host": "local", "working_dir": str(uat_dir)},
        }

    def fake_enrich(repo, merged):
        pass

    def fake_stop(entry):
        calls.append("stop")
        return {"method": "test"}

    def fake_start(entry):
        calls.append("start")
        return {"method": "test"}

    monkeypatch.setattr(env_mod, "_resolve_project_slug", fake_resolve)
    monkeypatch.setattr(env_mod, "_merged_deploy_config", fake_merged)
    monkeypatch.setattr(env_mod, "_enrich_local_working_dirs", fake_enrich)
    monkeypatch.setattr(env_mod, "_stop_environment", fake_stop)
    monkeypatch.setattr(env_mod, "_start_environment", fake_start)

    snapshots: list[dict] = []

    async def fake_emit(key, snapshot):
        snapshots.append(snapshot)
        dc._progress._JOBS[key] = snapshot

    monkeypatch.setattr(dc._progress, "_emit", fake_emit)

    asyncio.run(dc.run_copy_job("uat@viral-radar", "viral-radar"))

    # backup created with the OLD uat content, before being overwritten
    backups = list(uat_dir.glob("viral-radar.db.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"stale-uat-data"

    # target now has prd's content
    assert (uat_dir / "viral-radar.db").read_bytes() == b"PRD-DATA-V2"

    # stop happened before start
    assert calls == ["stop", "start"]

    final = snapshots[-1]
    assert final["status"] == "done"


def test_json_dir_copy_replaces_whole_directory(tmp_path, monkeypatch):
    """AC6: json_dir removes uat's old flows/ dir and replaces it with prd's."""
    prd_dir = tmp_path / "prd"
    uat_dir = tmp_path / "uat"
    (prd_dir / "flows").mkdir(parents=True)
    (uat_dir / "flows").mkdir(parents=True)
    (prd_dir / "flows" / "a.json").write_text('{"from": "prd"}')
    (uat_dir / "flows" / "stale.json").write_text('{"from": "old-uat"}')

    def fake_resolve(slug):
        return f"owner/{slug}"

    def fake_merged(slug, repo):
        return {
            "prd": {"host": "local", "working_dir": str(prd_dir)},
            "uat": {"host": "local", "working_dir": str(uat_dir)},
        }

    monkeypatch.setattr(env_mod, "_resolve_project_slug", fake_resolve)
    monkeypatch.setattr(env_mod, "_merged_deploy_config", fake_merged)
    monkeypatch.setattr(env_mod, "_enrich_local_working_dirs", lambda repo, merged: None)
    monkeypatch.setattr(env_mod, "_stop_environment", lambda entry: {"method": "test"})
    monkeypatch.setattr(env_mod, "_start_environment", lambda entry: {"method": "test"})

    async def fake_emit(key, snapshot):
        dc._progress._JOBS[key] = snapshot

    monkeypatch.setattr(dc._progress, "_emit", fake_emit)

    asyncio.run(dc.run_copy_job("uat@asset-studio", "asset-studio"))

    result_files = {p.name for p in (uat_dir / "flows").glob("*.json")}
    assert result_files == {"a.json"}, "uat's flows/ must exactly match prd's after copy, stale file gone"
    assert (uat_dir / "flows" / "a.json").read_text() == '{"from": "prd"}'

    backups = list(uat_dir.glob("flows.bak-*"))
    assert len(backups) == 1
    assert (backups[0] / "stale.json").exists(), "backup must preserve the pre-copy uat state"


def test_copy_rejects_unregistered_project_at_job_level():
    """run_copy_job itself also guards against an unregistered strategy
    (defense in depth beyond the route-level 400)."""
    snapshots: list[dict] = []

    async def fake_emit(key, snapshot):
        snapshots.append(snapshot)

    with patch.object(dc._progress, "_emit", fake_emit):
        asyncio.run(dc.run_copy_job("uat@crux", "crux"))

    assert snapshots[-1]["status"] == "error"
    assert "strategy" in snapshots[-1]["error"].lower()

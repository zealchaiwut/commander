"""Tests for issue #726 — Add a Deploy tab to the project view.

Each test is anchored to a specific acceptance criterion (AC):

  AC1  — A "Deploy" tab appears in the top-level project navigation
  AC2  — Tab lists one card per configured environment (prd, uat)
  AC3  — Each card shows host badge, branch, commit SHA, last-deploy time, status pill
  AC4  — local commit SHA is fetched from /api/health (git_sha)
  AC5  — render commit SHA is fetched from Render's live deploy API
  AC6  — Each card has a Deploy and a Restart button
  AC7  — Both buttons show a confirmation step before executing
  AC8  — While an action runs, the card streams progress
  AC9  — A log tail is visible during the action
  AC10 — Restarting the dashboard itself shows a "Restarting… reconnecting" overlay
  AC11 — The overlay polls /api/health until the server responds, then refreshes
  AC12 — Both commander and perf-coach environments are visible on the tab

The backend additions (deploy overview aggregation + render commit/timestamp
extraction) are unit- and endpoint-tested. The frontend wiring (tab, cards,
confirmation, overlay) is asserted against the static project.html source.
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

from services.sprint_manager import render_actions as ra  # noqa: E402
from services.sprint_manager import deploy_config_schema as dcs  # noqa: E402

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


# ── AC5: render commit SHA + last-deploy timestamp from the live deploy API ───


def test_latest_deploy_extracts_commit_sha():
    """AC5: the latest-deploy parser surfaces the commit SHA from Render's payload."""
    payload = [{"deploy": {
        "id": "dep-1", "status": "live",
        "commit": {"id": "abc1234deadbeef", "message": "ship"},
        "finishedAt": "2026-06-10T12:00:00Z",
    }}]
    info = ra.latest_deploy_from_payload(payload)
    assert info["commit"] == "abc1234deadbeef"


def test_latest_deploy_extracts_finished_timestamp():
    """AC3: the latest-deploy parser surfaces a last-deploy timestamp."""
    payload = [{"deploy": {
        "id": "dep-1", "status": "live",
        "commit": {"id": "abc1234"},
        "finishedAt": "2026-06-10T12:00:00Z",
    }}]
    info = ra.latest_deploy_from_payload(payload)
    assert info["finished_at"] == "2026-06-10T12:00:00Z"


def test_latest_deploy_normalizes_status():
    """AC5: the latest-deploy parser still normalizes the deploy status."""
    payload = [{"deploy": {"status": "build_in_progress", "commit": {"id": "x"}}}]
    info = ra.latest_deploy_from_payload(payload)
    assert info["status"] == "building"


def test_latest_deploy_empty_payload_is_safe():
    """AC5: an empty deploy list yields a safe default with no commit."""
    info = ra.latest_deploy_from_payload([])
    assert info["status"] in {"queued", "building", "live", "failed"}
    assert info["commit"] is None
    assert info["finished_at"] is None


# ── AC2 / AC12: deploy overview aggregation (pure builders) ───────────────────


def test_known_deploy_slugs_include_wired_projects():
    """AC12: commander, perf-coach, and vector-search-demo are deployable."""
    slugs = dcs.known_deploy_slugs()
    assert "commander" in slugs
    assert "perf-coach" in slugs
    assert "vector-search-demo" in slugs


def test_overview_entries_one_per_configured_env():
    """AC2: one overview entry per configured environment (prd, uat)."""
    merged = dcs.merge_seed(dcs.seed_for("commander"), {})
    entries = dcs.overview_entries_for("commander", merged)
    envs = {e["env"] for e in entries}
    assert envs == {"prd", "uat"}


def test_overview_entry_carries_project_env_host():
    """AC3: each overview entry identifies its project, env, and host."""
    merged = dcs.merge_seed(dcs.seed_for("commander"), {})
    entries = dcs.overview_entries_for("commander", merged)
    for e in entries:
        assert e["project"] == "commander"
        assert e["env"] in {"prd", "uat"}
        assert e["host"] in {"local", "render"}


def test_overview_local_entry_has_branch():
    """AC3: a local environment entry carries its branch name."""
    merged = dcs.merge_seed(dcs.seed_for("commander"), {})
    entries = dcs.overview_entries_for("commander", merged)
    prd = next(e for e in entries if e["env"] == "prd")
    assert prd["host"] == "local"
    assert prd["branch"] == "master"


def test_overview_render_entry_marks_host_render():
    """AC3/AC5: perf-coach prd is a render-hosted environment."""
    merged = dcs.merge_seed(dcs.seed_for("perf-coach"), {})
    entries = dcs.overview_entries_for("perf-coach", merged)
    prd = next(e for e in entries if e["env"] == "prd")
    assert prd["host"] == "render"


def test_overview_entries_never_include_secret():
    """AC5: the overview never exposes a render_api_key in cleartext."""
    merged = dcs.merge_seed(
        dcs.seed_for("perf-coach"),
        {"prd": {"host": "render", "render_service_id": "srv-x",
                 "render_api_key": "rnd_supersecret"}},
    )
    entries = dcs.overview_entries_for("perf-coach", merged)
    blob = repr(entries)
    assert "rnd_supersecret" not in blob


def test_vector_search_demo_overview_has_prd_and_uat():
    merged = dcs.merge_seed(dcs.seed_for("vector-search-demo"), {})
    entries = dcs.overview_entries_for("vector-search-demo", merged)
    envs = {e["env"] for e in entries}
    assert envs == {"prd", "uat"}
    uat = next(e for e in entries if e["env"] == "uat")
    assert uat["host"] == "local"
    assert uat["port"] == 8010


# ── endpoint tests ────────────────────────────────────────────────────────────


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
    {"repo": "zealchaiwut/vector-search-demo"},
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
        "services.sprint_manager.render_actions",
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


def test_overview_route_registered(client_ctx):
    """AC1: a deploy-overview endpoint exists on the app."""
    client, srv, repo = client_ctx
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    assert "/api/deploy/overview" in paths


def test_overview_lists_wired_projects(client_ctx):
    """AC12: the overview returns environments for commander, perf-coach, and vector-search-demo."""
    client, srv, repo = client_ctx
    resp = client.get("/api/deploy/overview")
    assert resp.status_code == 200, resp.text
    envs = resp.json()["environments"]
    projects = {e["project"] for e in envs}
    assert "commander" in projects
    assert "perf-coach" in projects
    assert "vector-search-demo" in projects


def test_vector_search_demo_overview_marks_not_ready(client_ctx):
    """vector-search-demo cards carry deploy_ready=false until scripts ship."""
    client, srv, repo = client_ctx
    resp = client.get("/api/deploy/overview")
    vsd = [e for e in resp.json()["environments"] if e["project"] == "vector-search-demo"]
    assert len(vsd) == 2
    assert all(e.get("deploy_ready") is False for e in vsd)
    assert all(e.get("deploy_errors") for e in vsd)


def test_overview_lists_env_cards(client_ctx):
    """AC2: the overview returns one entry per configured environment."""
    client, srv, repo = client_ctx
    resp = client.get("/api/deploy/overview")
    envs = resp.json()["environments"]
    commander_envs = {e["env"] for e in envs if e["project"] == "commander"}
    assert commander_envs == {"prd", "uat"}


def test_overview_never_leaks_render_key(client_ctx):
    """AC5: a stored render_api_key never appears in the overview response."""
    client, srv, settings_repo = client_ctx
    settings_repo.set_setting(
        "project", dcs.DEPLOY_CONFIG_KEY,
        {"prd": {"host": "render", "render_service_id": "srv-x",
                 "render_api_key": "rnd_leakcheck"}},
        project="owner/perf-coach",
    )
    resp = client.get("/api/deploy/overview")
    assert resp.status_code == 200
    assert "rnd_leakcheck" not in resp.text


def test_status_endpoint_returns_commit_and_timestamp(client_ctx):
    """AC5/AC3: the render deploy-status endpoint surfaces commit + finished_at."""
    client, srv, settings_repo = client_ctx
    settings_repo.set_setting(
        "project", dcs.DEPLOY_CONFIG_KEY,
        {"prd": {"host": "render", "render_service_id": "srv-xyz",
                 "render_api_key": "rnd_topsecret"}},
        project="owner/perf-coach",
    )

    def fake_call(method, url, api_key, **kw):
        return 200, [{"deploy": {
            "id": "dep-1", "status": "live",
            "commit": {"id": "feedface1234"},
            "finishedAt": "2026-06-10T09:30:00Z",
        }}]

    with patch.object(srv._render_actions, "call_render", side_effect=fake_call):
        resp = client.get("/api/projects/perf-coach/environments/prd/deploy-status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "live"
    assert data["commit"] == "feedface1234"
    assert data["finished_at"] == "2026-06-10T09:30:00Z"
    assert "rnd_topsecret" not in resp.text


# ── frontend wiring (asserted against the static project.html source) ─────────


def test_deploy_tab_button_in_nav():
    """AC1: a Deploy tab button is present in the project sub-tab nav."""
    assert "switchTab('deploy')" in PROJECT_HTML
    assert 'id="stab-deploy"' in PROJECT_HTML


def test_deploy_pane_present():
    """AC1: a deploy tab pane exists to host the cards."""
    assert 'id="pane-deploy"' in PROJECT_HTML


def test_deploy_tab_registered_in_switchtab():
    """AC1: 'deploy' is wired into the switchTab tab/pane arrays and init."""
    assert "deployTabInit" in PROJECT_HTML


def test_deploy_tab_fetches_overview():
    """AC2/AC12: the deploy tab loads its cards from /api/deploy/overview."""
    assert "/api/deploy/overview" in PROJECT_HTML


def test_deploy_card_shows_host_badge_and_status_pill():
    """AC3: the card markup includes a host badge and a status pill."""
    assert "deploy-card__badge" in PROJECT_HTML
    assert "status-pill" in PROJECT_HTML


def test_deploy_card_uses_health_for_local_sha():
    """AC4: local commit SHA is read from /api/health (git_sha)."""
    assert "/api/health" in PROJECT_HTML
    assert "git_sha" in PROJECT_HTML


def test_deploy_card_has_deploy_and_restart_buttons():
    """AC6: each card carries a Deploy and a Restart action."""
    assert "deployCardDeploy" in PROJECT_HTML
    assert "deployCardRestart" in PROJECT_HTML


def test_deploy_actions_have_confirmation_step():
    """AC7: deploy/restart show a confirmation step before executing."""
    assert "deployCardConfirm" in PROJECT_HTML
    assert "deployCardCancel" in PROJECT_HTML


def test_deploy_card_has_log_tail():
    """AC8/AC9: a streaming log tail element is rendered during an action."""
    assert "deploy-card__log" in PROJECT_HTML


def test_self_restart_overlay_polls_health_and_reloads():
    """AC10/AC11: the reconnect overlay polls /api/health then refreshes."""
    assert "deploy-reconnect-overlay" in PROJECT_HTML
    assert "Restarting" in PROJECT_HTML
    # the overlay must reload the page once health returns
    assert "location.reload" in PROJECT_HTML

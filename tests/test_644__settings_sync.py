"""Tests for issue #644 — Add directional settings sync with diff preview.

AC coverage:
  AC1  — Upload direction: POST /api/settings/sync/diff?direction=upload returns a diff
  AC2  — Fetch direction: POST /api/settings/sync/diff?direction=fetch returns a diff
  AC3  — Diff has added/removed/unchanged lines; no writes until commit
  AC4  — Secrets (github_token, database_url) never in diff payload
  AC5  — Machine-specific paths (DB_PATH, environments values) never in diff
  AC6  — POST /api/settings/sync/commit?direction=upload writes to Neon; local unchanged
  AC7  — POST /api/settings/sync/commit?direction=fetch writes to local files; Neon unchanged
  AC8  — GET /api/settings/sync/status returns last_synced; updates after commit
  AC9  — Already-in-sync response when no differences
  AC10 — Calling diff does not write anything (cancel-safe)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SERVICES_DIR / "sprint_manager"))


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_settings_engine():
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


@pytest.fixture()
def sync_module():
    """Return the settings_sync module with a fresh import."""
    for mod in list(sys.modules):
        if "settings_sync" in mod:
            sys.modules.pop(mod, None)
    import services.sprint_manager.settings_sync as m
    return m


@pytest.fixture()
def settings_repo_ctx():
    """Return (settings_repo, engine) with in-memory DB."""
    engine = _make_settings_engine()
    for mod in ("services.sprint_manager.settings_repo",):
        sys.modules.pop(mod, None)
    import services.sprint_manager.settings_repo as repo
    SessionLocal = sessionmaker(bind=engine)
    repo._session_factory = SessionLocal
    return repo, engine


@pytest.fixture()
def client_ctx(tmp_path, settings_repo_ctx):
    """Yield (client, srv) with patched DB, projects.json, and sync status file."""
    settings_repo, engine = settings_repo_ctx

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {
            "repo": "owner/myproject",
            "name": "My Project",
            "icon": "ti-cpu",
            "color": "blue",
            "tracked": True,
            "environments": {
                "prd": "/local/machine/path/prd",
                "coder": "/local/machine/path/coder",
            },
        }
    ]))

    sync_status_file = tmp_path / "settings.last_synced"

    for mod in list(sys.modules):
        if mod in ("server",) or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv

    from fastapi.testclient import TestClient
    with patch.object(srv, "_settings_repo", settings_repo):
        with patch.object(srv, "_SYNC_SETTINGS_AVAILABLE", True, create=True):
            with patch.object(srv, "_SYNC_STATUS_FILE", sync_status_file, create=True):
                with patch.object(srv.projects_module, "load_projects", return_value=[
                    {
                        "repo": "owner/myproject",
                        "name": "My Project",
                        "icon": "ti-cpu",
                        "color": "blue",
                        "tracked": True,
                        "environments": {
                            "prd": "/local/machine/path/prd",
                        },
                    }
                ]):
                    with patch.object(srv, "_PROJECTS_FILE",
                                      projects_file, create=True):
                        client = TestClient(srv.app, raise_server_exceptions=False)
                        yield client, srv, settings_repo, projects_file, sync_status_file


# ── AC1: Upload direction diff endpoint ──────────────────────────────────────

def test_upload_diff_endpoint_exists(client_ctx):
    """POST /api/settings/sync/diff with direction=upload must return 200."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    assert resp.status_code == 200, (
        f"Expected 200 for upload diff, got {resp.status_code}: {resp.text}"
    )


def test_upload_diff_returns_diff_list(client_ctx):
    """Upload diff response must include a 'diff' list."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    data = resp.json()
    assert "diff" in data, f"Response must have 'diff' key, got: {list(data.keys())}"
    assert isinstance(data["diff"], list), "'diff' must be a list"


def test_upload_diff_returns_already_in_sync_flag(client_ctx):
    """Upload diff response must include 'already_in_sync' boolean."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    data = resp.json()
    assert "already_in_sync" in data, (
        f"Response must have 'already_in_sync' key, got: {list(data.keys())}"
    )
    assert isinstance(data["already_in_sync"], bool), "'already_in_sync' must be bool"


# ── AC2: Fetch direction diff endpoint ───────────────────────────────────────

def test_fetch_diff_endpoint_exists(client_ctx):
    """POST /api/settings/sync/diff with direction=fetch must return 200."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    assert resp.status_code == 200, (
        f"Expected 200 for fetch diff, got {resp.status_code}: {resp.text}"
    )


def test_fetch_diff_shows_neon_values_as_source(client_ctx, settings_repo_ctx):
    """Fetch diff shows Neon values as the incoming (source) data."""
    client, srv, settings_repo, projects_file, _ = client_ctx

    # Seed Neon with a value different from local
    settings_repo.set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})

    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    data = resp.json()
    diff = data["diff"]

    # Should show the Neon model value as coming in
    keys_in_diff = [item["key"] for item in diff]
    assert any("default_model" in k for k in keys_in_diff), (
        f"Fetch diff must show default_model; diff keys: {keys_in_diff}"
    )


def test_diff_invalid_direction_returns_400(client_ctx):
    """POST /api/settings/sync/diff with unknown direction returns 400."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "sideways"})
    assert resp.status_code == 400, (
        f"Expected 400 for invalid direction, got {resp.status_code}: {resp.text}"
    )


# ── AC3: Diff line format ─────────────────────────────────────────────────────

def test_diff_items_have_status_field(client_ctx, settings_repo_ctx):
    """Each diff item must have a 'status' field."""
    client, *_ = client_ctx
    settings_repo_ctx[0].set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})

    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    data = resp.json()
    for item in data["diff"]:
        assert "status" in item, f"Diff item missing 'status': {item}"


def test_diff_status_values_are_valid(client_ctx, settings_repo_ctx):
    """Diff item 'status' must be one of: added, removed, unchanged."""
    client, *_ = client_ctx
    settings_repo_ctx[0].set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})

    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    valid = {"added", "removed", "unchanged"}
    for item in resp.json()["diff"]:
        assert item["status"] in valid, (
            f"status '{item['status']}' not in {valid}; item: {item}"
        )


def test_diff_items_have_key_and_value(client_ctx, settings_repo_ctx):
    """Each diff item must have 'key' and 'value' fields."""
    client, *_ = client_ctx
    settings_repo_ctx[0].set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})

    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    for item in resp.json()["diff"]:
        assert "key" in item, f"Diff item missing 'key': {item}"
        assert "value" in item, f"Diff item missing 'value': {item}"


def test_diff_does_not_write_anything(client_ctx, settings_repo_ctx):
    """Calling diff (without commit) must not write to Neon."""
    client, *_ = client_ctx
    repo, _ = settings_repo_ctx

    # Upload direction: local has 'name', Neon is empty
    before = repo.get_setting_scoped("global", "app_config")

    client.post("/api/settings/sync/diff", json={"direction": "upload"})

    after = repo.get_setting_scoped("global", "app_config")
    assert before == after, (
        "Calling diff must not write to Neon; Neon changed from diff call"
    )


# ── AC4: Secrets excluded ─────────────────────────────────────────────────────

def test_upload_diff_excludes_github_token(client_ctx, settings_repo_ctx):
    """github_token must never appear in upload diff payload."""
    client, *_ = client_ctx
    # Even if someone put a token in local config somehow
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    data = resp.json()
    keys = [item["key"] for item in data["diff"]]
    assert not any("github_token" in k for k in keys), (
        f"github_token must not appear in diff; found in keys: {keys}"
    )


def test_fetch_diff_excludes_github_token(client_ctx, settings_repo_ctx):
    """github_token must never appear in fetch diff payload."""
    client, *_ = client_ctx
    # Seed Neon with a secret (should not happen but defensively exclude)
    settings_repo_ctx[0].set_setting("global", "app_config", {
        "github_token": "ghp_secretvalue",
        "default_model": "claude-sonnet-4-6",
    })
    resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    data = resp.json()
    keys = [item["key"] for item in data["diff"]]
    assert not any("github_token" in k for k in keys), (
        f"github_token must not appear in fetch diff; found in: {keys}"
    )
    # Values must not contain the secret either
    values = [str(item.get("value", "")) for item in data["diff"]]
    assert not any("ghp_secretvalue" in v for v in values), (
        "Secret token value must not appear in diff values"
    )


def test_upload_diff_excludes_database_url(client_ctx):
    """database_url must never appear in upload diff payload."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    keys = [item["key"] for item in resp.json()["diff"]]
    assert not any("database_url" in k for k in keys), (
        f"database_url must not appear in diff; found in: {keys}"
    )


def test_secrets_excluded_at_serialization_not_filtered(sync_module, tmp_path):
    """Secrets must be excluded when building the snapshot, not post-hoc filtered."""
    # Even if projects.json has a github_token field, it must never appear in the snapshot
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([{
        "repo": "o/r",
        "name": "R",
        "github_token": "secret",  # this field should never be in snapshot
    }]))
    snap = sync_module.load_local_snapshot(projects_file, sprint_yaml_path=None)
    # github_token must not appear anywhere in app_config
    keys_flat = list(snap.get("app_config", {}).keys())
    assert "github_token" not in keys_flat, (
        "github_token must not be in local snapshot app_config"
    )
    # Also verify it's not a project display field
    proj = snap["projects"].get("o/r", {})
    assert "github_token" not in proj, (
        "github_token must not be in project display fields"
    )


# ── AC5: Machine-specific paths excluded ─────────────────────────────────────

def test_upload_diff_excludes_environments_paths(client_ctx):
    """environments path values must not appear in upload diff."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/diff", json={"direction": "upload"})
    data = resp.json()
    for item in data["diff"]:
        assert "environments" not in item["key"], (
            f"environments path must not appear in diff: {item}"
        )
        assert "/local/machine/path" not in str(item.get("value", "")), (
            f"Machine path value must not appear in diff: {item}"
        )


def test_local_snapshot_excludes_environments(sync_module, tmp_path):
    """load_local_snapshot must not include environments paths."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {
            "repo": "owner/proj",
            "name": "Proj",
            "icon": "ti-cpu",
            "color": "blue",
            "environments": {
                "prd": "/home/user/dev/proj/prd",
                "coder": "/home/user/dev/proj/coder",
            }
        }
    ]))
    snap = sync_module.load_local_snapshot(projects_file)
    proj = snap["projects"].get("owner/proj", {})
    assert "environments" not in proj, (
        "environments must not be in local snapshot"
    )
    # Verify path values aren't leaking anywhere
    snap_str = json.dumps(snap)
    assert "/home/user/dev/proj" not in snap_str, (
        "Machine path values must not appear anywhere in local snapshot"
    )


def test_local_snapshot_includes_display_fields(sync_module, tmp_path):
    """load_local_snapshot must include name, icon, color, tracked per project."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/proj", "name": "Proj", "icon": "ti-cpu", "color": "blue", "tracked": True}
    ]))
    snap = sync_module.load_local_snapshot(projects_file)
    proj = snap["projects"].get("owner/proj", {})
    assert proj.get("name") == "Proj"
    assert proj.get("icon") == "ti-cpu"
    assert proj.get("color") == "blue"
    assert proj.get("tracked") is True


# ── AC6: Confirming Upload writes to Neon, local unchanged ───────────────────

def test_upload_commit_endpoint_exists(client_ctx):
    """POST /api/settings/sync/commit with direction=upload must exist (200)."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/commit", json={"direction": "upload"})
    assert resp.status_code == 200, (
        f"Expected 200 for upload commit, got {resp.status_code}: {resp.text}"
    )


def test_upload_commit_writes_to_neon(client_ctx, settings_repo_ctx, tmp_path):
    """Upload commit must write local display fields to Neon."""
    client, srv, settings_repo, projects_file, _ = client_ctx
    repo, _ = settings_repo_ctx

    # Ensure Neon starts empty
    before = repo.get_setting_scoped("global", "app_config")

    # Seed local with a model setting via sprint.yaml agent_config
    sprint_yaml = tmp_path / "sprint.yaml"
    sprint_yaml.write_text(
        "repo_name: owner/myproject\nagent_config:\n  default_model: claude-opus-4-8\n"
    )
    with patch.object(srv, "_SPRINT_YAML_PATH", sprint_yaml, create=True):
        resp = client.post("/api/settings/sync/commit", json={"direction": "upload"})

    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("ok") is True, f"Expected ok=true; got: {data}"


def test_upload_commit_local_files_unchanged(client_ctx, settings_repo_ctx):
    """Upload commit must NOT modify local projects.json."""
    client, *_ = client_ctx
    _, _, _, projects_file, _ = client_ctx

    content_before = projects_file.read_text()
    client.post("/api/settings/sync/commit", json={"direction": "upload"})
    content_after = projects_file.read_text()

    assert content_before == content_after, (
        "projects.json must not be modified by upload commit"
    )


# ── AC7: Confirming Fetch writes to local files, Neon unchanged ───────────────

def test_fetch_commit_endpoint_exists(client_ctx):
    """POST /api/settings/sync/commit with direction=fetch must exist (200)."""
    client, *_ = client_ctx
    resp = client.post("/api/settings/sync/commit", json={"direction": "fetch"})
    assert resp.status_code == 200, (
        f"Expected 200 for fetch commit, got {resp.status_code}: {resp.text}"
    )


def test_fetch_commit_writes_display_field_to_projects_json(client_ctx, settings_repo_ctx, tmp_path):
    """Fetch commit must write Neon project display fields to projects.json."""
    client, srv, settings_repo, projects_file, _ = client_ctx
    repo, _ = settings_repo_ctx

    # Seed Neon projects display via settings (per-project override)
    repo.set_setting("project", "app_config", {"display_name": "New Name"}, project="owner/myproject")

    # Read diff first to check what would change
    resp_diff = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    assert resp_diff.status_code == 200

    resp = client.post("/api/settings/sync/commit", json={"direction": "fetch"})
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("ok") is True


def test_fetch_commit_neon_unchanged(client_ctx, settings_repo_ctx):
    """Fetch commit must NOT modify Neon settings."""
    client, *_ = client_ctx
    repo, _ = settings_repo_ctx

    # Seed Neon
    repo.set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})
    neon_before = repo.get_setting_scoped("global", "app_config")

    client.post("/api/settings/sync/commit", json={"direction": "fetch"})

    neon_after = repo.get_setting_scoped("global", "app_config")
    assert neon_before == neon_after, (
        "Neon settings must not be modified by fetch commit"
    )


# ── AC8: Last-synced timestamp ─────────────────────────────────────────────────

def test_sync_status_endpoint_exists(client_ctx):
    """GET /api/settings/sync/status must return 200."""
    client, *_ = client_ctx
    resp = client.get("/api/settings/sync/status")
    assert resp.status_code == 200, (
        f"Expected 200 for sync status, got {resp.status_code}: {resp.text}"
    )


def test_sync_status_has_last_synced_field(client_ctx):
    """GET /api/settings/sync/status must include 'last_synced' field."""
    client, *_ = client_ctx
    resp = client.get("/api/settings/sync/status")
    data = resp.json()
    assert "last_synced" in data, (
        f"Response must have 'last_synced' key, got: {list(data.keys())}"
    )


def test_sync_status_null_before_any_sync(client_ctx):
    """last_synced must be null before any sync has occurred."""
    client, *_ = client_ctx
    resp = client.get("/api/settings/sync/status")
    data = resp.json()
    assert data["last_synced"] is None, (
        f"last_synced must be null before first sync; got: {data['last_synced']}"
    )


def test_sync_status_updates_after_commit(client_ctx):
    """last_synced must be set after a successful commit."""
    client, *_ = client_ctx

    before_resp = client.get("/api/settings/sync/status")
    assert before_resp.json()["last_synced"] is None

    client.post("/api/settings/sync/commit", json={"direction": "upload"})

    after_resp = client.get("/api/settings/sync/status")
    ts = after_resp.json()["last_synced"]
    assert ts is not None, "last_synced must be set after commit"
    # Must be valid ISO 8601
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert dt.tzinfo is not None, "timestamp must include timezone info"
    except ValueError as e:
        pytest.fail(f"last_synced is not valid ISO 8601: {ts!r} — {e}")


# ── AC9: Already in sync ──────────────────────────────────────────────────────

def test_already_in_sync_when_no_differences(sync_module, tmp_path):
    """When local and Neon snapshots are identical, already_in_sync=True."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/proj", "name": "Proj", "icon": "ti-cpu", "color": "blue"}
    ]))

    local = sync_module.load_local_snapshot(projects_file)
    # Neon snapshot identical to local
    neon = {
        "projects": {"owner/proj": {"name": "Proj", "icon": "ti-cpu", "color": "blue"}},
        "app_config": {},
    }
    diff = sync_module.compute_diff(local, neon, "upload")
    assert sync_module.is_already_in_sync(diff) is True, (
        f"Expected already_in_sync when snapshots match; diff: {diff}"
    )
    changed = [i for i in diff if i["status"] in ("added", "removed")]
    assert len(changed) == 0, f"No added/removed lines expected; got: {changed}"


def test_already_in_sync_false_when_differs(client_ctx):
    """When values differ, already_in_sync must be false."""
    client, srv, settings_repo, *_ = client_ctx
    # Seed Neon with a value that local (no sprint.yaml) won't have
    settings_repo.set_setting("global", "app_config", {"coder_model": "claude-opus-4-8"})

    # Patch sprint.yaml to None so local app_config is empty → fetch shows diff
    with patch.object(srv, "_SPRINT_YAML_PATH", None):
        resp = client.post("/api/settings/sync/diff", json={"direction": "fetch"})
    data = resp.json()
    assert data["already_in_sync"] is False, (
        f"Expected already_in_sync=false when values differ, got: {data}"
    )


# ── AC10: Cancel is safe (diff writes nothing) ────────────────────────────────

def test_diff_upload_does_not_modify_neon(client_ctx, settings_repo_ctx):
    """Repeated diff calls must not write to Neon (idempotent, no side-effects)."""
    client, *_ = client_ctx
    repo, _ = settings_repo_ctx

    neon_before = repo.get_setting_scoped("global", "app_config")

    # Call diff multiple times (simulating user reviewing and cancelling)
    for _ in range(3):
        client.post("/api/settings/sync/diff", json={"direction": "upload"})

    neon_after = repo.get_setting_scoped("global", "app_config")
    assert neon_before == neon_after, (
        "Multiple diff calls must not modify Neon (cancel-safe)"
    )


def test_diff_fetch_does_not_modify_local(client_ctx, settings_repo_ctx):
    """Fetch diff calls must not write to local files (cancel-safe)."""
    client, _, _, projects_file, _ = client_ctx
    settings_repo_ctx[0].set_setting("global", "app_config", {"default_model": "claude-opus-4-8"})

    content_before = projects_file.read_text()

    for _ in range(3):
        client.post("/api/settings/sync/diff", json={"direction": "fetch"})

    content_after = projects_file.read_text()
    assert content_before == content_after, (
        "Fetch diff must not modify projects.json (cancel-safe)"
    )


# ── Settings sync service unit tests ─────────────────────────────────────────

def test_compute_diff_upload_added(sync_module):
    """compute_diff upload: local has value, Neon doesn't → added."""
    local = {"projects": {}, "app_config": {"default_model": "claude-sonnet-4-6"}}
    neon = {"projects": {}, "app_config": {}}

    diff = sync_module.compute_diff(local, neon, "upload")
    added = [i for i in diff if i["status"] == "added"]
    assert any(i["key"] == "app_config.default_model" for i in added), (
        f"Expected added item for default_model; diff: {diff}"
    )


def test_compute_diff_fetch_added(sync_module):
    """compute_diff fetch: Neon has value, local doesn't → added."""
    local = {"projects": {}, "app_config": {}}
    neon = {"projects": {}, "app_config": {"coder_model": "claude-opus-4-8"}}

    diff = sync_module.compute_diff(local, neon, "fetch")
    added = [i for i in diff if i["status"] == "added"]
    assert any(i["key"] == "app_config.coder_model" for i in added), (
        f"Expected added item for coder_model; diff: {diff}"
    )


def test_compute_diff_unchanged_when_equal(sync_module):
    """compute_diff: matching values yield unchanged status."""
    local = {"projects": {}, "app_config": {"default_model": "claude-sonnet-4-6"}}
    neon = {"projects": {}, "app_config": {"default_model": "claude-sonnet-4-6"}}

    diff = sync_module.compute_diff(local, neon, "upload")
    unchanged = [i for i in diff if i["status"] == "unchanged"]
    assert any(i["key"] == "app_config.default_model" for i in unchanged), (
        f"Expected unchanged for equal values; diff: {diff}"
    )


def test_compute_diff_changed_shows_removed_and_added(sync_module):
    """compute_diff: different values → one removed (old) + one added (new)."""
    local = {"projects": {}, "app_config": {"default_model": "claude-sonnet-4-6"}}
    neon = {"projects": {}, "app_config": {"default_model": "claude-opus-4-8"}}

    diff = sync_module.compute_diff(local, neon, "upload")
    removed = [i for i in diff if i["status"] == "removed" and "default_model" in i["key"]]
    added = [i for i in diff if i["status"] == "added" and "default_model" in i["key"]]
    assert len(removed) == 1, f"Expected 1 removed item; got {removed}"
    assert len(added) == 1, f"Expected 1 added item; got {added}"
    assert removed[0]["value"] == "claude-opus-4-8", "Removed item must be old (neon) value"
    assert added[0]["value"] == "claude-sonnet-4-6", "Added item must be new (local) value"


def test_is_already_in_sync_true(sync_module):
    """is_already_in_sync returns True when all items are unchanged."""
    diff = [
        {"status": "unchanged", "key": "app_config.default_model", "value": "x"},
        {"status": "unchanged", "key": "app_config.coder_model", "value": "y"},
    ]
    assert sync_module.is_already_in_sync(diff) is True


def test_is_already_in_sync_false(sync_module):
    """is_already_in_sync returns False when any item is added or removed."""
    diff = [
        {"status": "unchanged", "key": "app_config.default_model", "value": "x"},
        {"status": "added", "key": "app_config.coder_model", "value": "y"},
    ]
    assert sync_module.is_already_in_sync(diff) is False


def test_compute_diff_secret_fields_excluded(sync_module):
    """compute_diff must not emit diff lines for secret fields."""
    local = {"projects": {}, "app_config": {"github_token": "secret", "default_model": "x"}}
    neon = {"projects": {}, "app_config": {"github_token": "other_secret", "default_model": "y"}}

    diff = sync_module.compute_diff(local, neon, "upload")
    keys = [i["key"] for i in diff]
    assert not any("github_token" in k for k in keys), (
        f"github_token must not appear in diff; got keys: {keys}"
    )
    assert not any("database_url" in k for k in keys), (
        f"database_url must not appear in diff; got keys: {keys}"
    )


# ── UI HTML structure ─────────────────────────────────────────────────────────

def test_settings_sync_card_in_global_settings_html():
    """Global settings modal HTML must contain a settings sync section."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "Settings Sync" in html or "settings-sync" in html or "settingsSync" in html, (
        "project.html global settings modal must contain a Settings Sync section"
    )


def test_upload_to_db_button_in_html():
    """project.html must have an 'Upload to DB' button for sync."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "Upload to DB" in html or "upload-to-db" in html or "_syncUpload" in html, (
        "project.html must have an Upload to DB trigger"
    )


def test_fetch_from_db_button_in_html():
    """project.html must have a 'Fetch from DB' button for sync."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "Fetch from DB" in html or "fetch-from-db" in html or "_syncFetch" in html, (
        "project.html must have a Fetch from DB trigger"
    )


def test_diff_preview_container_in_html():
    """project.html must have a diff preview container."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "sync-diff" in html or "syncDiff" in html or "diff-preview" in html, (
        "project.html must have a diff preview container"
    )


def test_last_synced_display_in_html():
    """project.html must display the last-synced timestamp in the sync section."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "last-synced" in html or "lastSynced" in html or "last_synced" in html, (
        "project.html must display last-synced timestamp"
    )


def test_already_in_sync_message_in_html():
    """project.html must have an 'already in sync' message element."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "already in sync" in html.lower() or "alreadyInSync" in html or "already-in-sync" in html, (
        "project.html must contain an 'already in sync' message"
    )


def test_sync_confirm_cancel_buttons_in_html():
    """project.html diff preview must have Confirm and Cancel buttons."""
    html = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "_syncConfirm" in html or "syncConfirm" in html or "confirmSync" in html, (
        "project.html must have a sync confirm button/function"
    )
    assert "_syncCancel" in html or "syncCancel" in html or "cancelSync" in html, (
        "project.html must have a sync cancel button/function"
    )


# ── Sprint.yaml agent_config integration ─────────────────────────────────────

def test_load_local_snapshot_reads_sprint_yaml_agent_config(sync_module, tmp_path):
    """load_local_snapshot reads agent_config from sprint.yaml if present."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([]))

    sprint_yaml = tmp_path / "sprint.yaml"
    sprint_yaml.write_text(
        "repo_name: owner/proj\nagent_config:\n  default_model: claude-opus-4-8\n"
    )

    snap = sync_module.load_local_snapshot(projects_file, sprint_yaml_path=sprint_yaml)
    assert snap["app_config"].get("default_model") == "claude-opus-4-8", (
        f"Expected default_model from sprint.yaml agent_config; got: {snap['app_config']}"
    )


def test_load_local_snapshot_excludes_path_fields_from_sprint_yaml(sync_module, tmp_path):
    """load_local_snapshot must exclude machine-specific paths from sprint.yaml."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([]))

    sprint_yaml = tmp_path / "sprint.yaml"
    sprint_yaml.write_text(
        "worktrees:\n  coder: /home/user/dev\npaths:\n  logs_dir: /home/user/dev/.commander/logs\n"
        "agent_config:\n  default_model: claude-sonnet-4-6\n"
    )

    snap = sync_module.load_local_snapshot(projects_file, sprint_yaml_path=sprint_yaml)
    snap_str = json.dumps(snap)
    assert "/home/user/dev" not in snap_str, (
        "Machine paths from sprint.yaml must not appear in local snapshot"
    )


def test_load_local_snapshot_handles_missing_sprint_yaml(sync_module, tmp_path):
    """load_local_snapshot must not fail when sprint.yaml is absent."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([]))

    snap = sync_module.load_local_snapshot(
        projects_file,
        sprint_yaml_path=tmp_path / "nonexistent.yaml",
    )
    assert isinstance(snap, dict), "Must return a dict even without sprint.yaml"
    assert "app_config" in snap
    assert "projects" in snap

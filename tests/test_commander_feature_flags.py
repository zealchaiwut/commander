"""Feature flags: env vars, global settings store, and /api/environment."""
import pytest

# Import client_ctx from settings API tests (shared in-memory DB fixture).
from test_639__settings_rest_api import client_ctx  # noqa: F401


@pytest.fixture()
def settings_store(tmp_path, monkeypatch):
    import settings_repo
    from settings_schema import APP_CONFIG_KEY

    store = tmp_path / "settings_store.json"
    monkeypatch.setattr(settings_repo, "_fallback_store_path", lambda: store)
    return settings_repo, APP_CONFIG_KEY


def test_global_settings_exposes_disable_fields(client_ctx):
    client, *_ = client_ctx
    data = client.get("/api/settings").json()
    assert data["disable_sprint_signoff"] is True
    assert data["disable_sprint_planning"] is True
    assert data["disable_sprint_goal_required"] is True
    assert "disable_advisor" not in data


def test_put_global_settings_persists_feature_flags(client_ctx):
    client, *_ = client_ctx
    resp = client.put(
        "/api/settings",
        json={
            "disable_sprint_signoff": False,
            "disable_sprint_planning": False,
            "disable_sprint_goal_required": False,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["disable_sprint_signoff"] is False


def test_commander_features_reads_global_settings(settings_store, monkeypatch):
    settings_repo, key = settings_store
    monkeypatch.delenv("COMMANDER_DISABLE_SIGNOFF", raising=False)
    settings_repo.set_setting("global", key, {"disable_sprint_signoff": False})

    import config

    feats = config.commander_features()
    assert feats["signoff"] is True
    assert config.sprint_signoff_disabled() is False


def test_env_overrides_global_settings(settings_store, monkeypatch):
    settings_repo, key = settings_store
    monkeypatch.setenv("COMMANDER_DISABLE_SIGNOFF", "1")
    settings_repo.set_setting("global", key, {"disable_sprint_signoff": False})

    import config

    assert config.sprint_signoff_disabled() is True
    assert config.commander_features()["signoff"] is False


def test_environment_endpoint_includes_features(client_ctx):
    client, *_ = client_ctx
    data = client.get("/api/environment").json()
    assert "features" in data
    assert set(data["features"]) >= {"signoff", "planning", "goal_required"}
    assert "advisor" not in data["features"]

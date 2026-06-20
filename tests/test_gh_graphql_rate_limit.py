"""GraphQL rate-limit hardening — REST reads, --active auth, GH_TOKEN persist."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def test_fetch_labels_uses_gh_api_rest():
    import services.sprint_manager.state_machine as sm

    proc = MagicMock(returncode=0, stdout='{"labels":[{"name":"SIT"}]}', stderr="")
    with patch.object(sm.subprocess, "run", return_value=proc) as run:
        labels = sm._fetch_labels(42, "zealchaiwut/commander")
    assert labels == frozenset({"SIT"})
    cmd = run.call_args[0][0]
    assert cmd[:3] == ["gh", "api", "repos/zealchaiwut/commander/issues/42"]


def test_check_gh_auth_uses_active_flag():
    import server as srv

    proc = MagicMock(returncode=0, stdout="Token scopes: repo\n", stderr="")
    with patch.object(srv.subprocess, "run", return_value=proc) as run:
        srv._check_gh_auth()
    cmd = run.call_args[0][0]
    assert "--active" in cmd
    assert srv._GH_AUTH_STATUS["ok"] is True


def test_gh_transport_label_classifies_api_as_rest():
    import github_client as gc

    assert gc._gh_transport_label(("api", "repos/o/r/issues")) == "rest"
    assert gc._gh_transport_label(("issue", "list")) == "graphql"
    assert gc._gh_transport_label(("label", "list")) == "graphql"


def test_persist_gh_token_writes_env(tmp_path):
    gas = importlib.import_module("routers.gh_auth_service")
    env_file = tmp_path / ".env"
    env_file.write_text("DB_PATH=./x.db\nGH_TOKEN=old\n", encoding="utf-8")

    with patch.object(gas, "_DASHBOARD_ENV", env_file):
        assert gas._write_env_token(env_file, "ghp_new_token") is True
    text = env_file.read_text(encoding="utf-8")
    assert "GH_TOKEN=ghp_new_token" in text
    assert "GH_TOKEN=old" not in text
    assert text.count("GH_TOKEN=") == 1


def test_refresh_server_gh_auth_persists_token_on_ok():
    gas = importlib.import_module("routers.gh_auth_service")
    import server as srv

    srv._GH_AUTH_STATUS = {"ok": True, "message": ""}
    with patch.object(srv, "_check_gh_auth"), patch.object(
        gas, "persist_gh_token_to_env", return_value=True
    ) as persist:
        gas.refresh_server_gh_auth()
    persist.assert_called_once()


@pytest.fixture
def client():
    import server as srv

    return __import__("fastapi.testclient").testclient.TestClient(
        srv.app, raise_server_exceptions=False
    )

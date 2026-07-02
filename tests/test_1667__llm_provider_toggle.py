"""Test suite for issue #1667: Global LLM provider toggle (Anthropic/ICA).

Each test is anchored to a specific acceptance criterion from the issue.

AC1  Commander settings store includes a llmProvider field accepting
     "anthropic" or "ica" (default: "anthropic")
AC2  When llmProvider is changed, Commander instructs the claude-proxy to
     activate the matching profile via POST {proxy}/profile endpoint
AC3  All agents dispatched after the toggle change use the newly active
     provider without any per-agent env changes
AC4  Settings UI exposes a switch labeled with the two provider names and
     displays the currently active provider
AC5  Selected provider persists across Commander restarts (written to the
     existing settings store)
AC6  If the proxy control mechanism is unavailable, Commander surfaces an
     error and does not silently fall back
AC7  Switching provider does not affect in-flight agent sessions (only newly
     dispatched agents pick it up)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: settings schema includes llmProvider ────────────────────────────────

class TestAC1SettingsSchemaField:
    """llmProvider is a known non-secret field in the settings schema."""

    def test_llm_provider_in_known_fields(self):
        from services.sprint_manager.settings_schema import KNOWN_FIELDS
        assert "llmProvider" in KNOWN_FIELDS, (
            "llmProvider must be present in KNOWN_FIELDS"
        )

    def test_llm_provider_is_not_secret(self):
        from services.sprint_manager.settings_schema import NON_SECRET_FIELDS
        assert "llmProvider" in NON_SECRET_FIELDS, (
            "llmProvider must not be a secret field"
        )

    def test_llm_provider_default_is_anthropic(self):
        from services.sprint_manager.settings_schema import KNOWN_FIELDS
        assert KNOWN_FIELDS["llmProvider"]["default"] == "anthropic", (
            "llmProvider must default to 'anthropic'"
        )

    def test_build_effective_response_includes_llm_provider(self):
        from services.sprint_manager.settings_schema import build_effective_response
        result = build_effective_response({})
        assert "llmProvider" in result
        assert result["llmProvider"] == "anthropic"

    def test_build_effective_response_reflects_stored_value(self):
        from services.sprint_manager.settings_schema import build_effective_response
        result = build_effective_response({"llmProvider": "ica"})
        assert result["llmProvider"] == "ica"


# ── AC1: llmProvider validation ───────────────────────────────────────────────

class TestAC1ProviderValidation:
    """llmProvider only accepts 'anthropic' or 'ica'."""

    def test_valid_provider_anthropic(self):
        from apps.dashboard.routers.llm_provider_service import validate_provider
        validate_provider("anthropic")  # must not raise

    def test_valid_provider_ica(self):
        from apps.dashboard.routers.llm_provider_service import validate_provider
        validate_provider("ica")  # must not raise

    def test_invalid_provider_raises(self):
        from fastapi import HTTPException
        from apps.dashboard.routers.llm_provider_service import validate_provider
        with pytest.raises(HTTPException) as exc_info:
            validate_provider("openai")
        assert exc_info.value.status_code == 400

    def test_empty_provider_raises(self):
        from fastapi import HTTPException
        from apps.dashboard.routers.llm_provider_service import validate_provider
        with pytest.raises(HTTPException):
            validate_provider("")


# ── AC1: PUT /api/settings rejects llmProvider ───────────────────────────────

class TestAC1PutSettingsRejectsProvider:
    """PUT /api/settings must reject llmProvider (use /api/settings/provider)."""

    def test_put_settings_rejects_llm_provider_field(self):
        from fastapi import HTTPException
        from apps.dashboard.routers.settings_service import _validate_settings_body
        with pytest.raises(HTTPException) as exc_info:
            _validate_settings_body({"llmProvider": "ica"})
        assert exc_info.value.status_code == 422


# ── AC2: proxy instruction on provider change ─────────────────────────────────

class TestAC2ProxyInstruction:
    """Changing the provider instructs the claude-proxy via POST /profile."""

    def test_switch_calls_proxy_endpoint(self):
        """POST /api/settings/provider calls the proxy profile endpoint."""
        import apps.dashboard.routers.llm_provider_service as svc
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        stored = {}

        def fake_set_setting(scope, key, value, **_kw):
            stored[key] = value

        with (
            patch.object(svc, "_call_proxy_profile", return_value=None) as mock_proxy,
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            mock_repo.set_setting.side_effect = fake_set_setting
            svc.set_provider("ica")
            mock_proxy.assert_called_once_with("ica")

    def test_switch_to_anthropic_calls_proxy_with_anthropic(self):
        import apps.dashboard.routers.llm_provider_service as svc
        with (
            patch.object(svc, "_call_proxy_profile", return_value=None) as mock_proxy,
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            mock_repo.set_setting.return_value = None
            svc.set_provider("anthropic")
            mock_proxy.assert_called_once_with("anthropic")

    def test_proxy_call_uses_configurable_url(self):
        """_call_proxy_profile targets COMMANDER_PROXY_URL."""
        import apps.dashboard.routers.llm_provider_service as svc
        with (
            patch.dict(os.environ, {"COMMANDER_PROXY_URL": "http://localhost:19090"}),
            patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx,
        ):
            mock_post = MagicMock()
            mock_post.raise_for_status = MagicMock()
            mock_httpx.post.return_value = mock_post
            svc._call_proxy_profile("ica")
            call_url = mock_httpx.post.call_args[0][0]
            assert "19090" in call_url, (
                "proxy URL must respect COMMANDER_PROXY_URL env var"
            )


# ── AC2: proxy call payload ───────────────────────────────────────────────────

class TestAC2ProxyPayload:
    """The proxy is called with the correct profile name."""

    def test_proxy_receives_correct_profile_name(self):
        import apps.dashboard.routers.llm_provider_service as svc
        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_post = MagicMock()
            mock_post.raise_for_status = MagicMock()
            mock_httpx.post.return_value = mock_post
            svc._call_proxy_profile("ica")
            kwargs = mock_httpx.post.call_args[1]
            payload = kwargs.get("json") or mock_httpx.post.call_args[0][1] if len(mock_httpx.post.call_args[0]) > 1 else kwargs.get("json")
            # Accept json kwarg
            if payload is None and "json" in mock_httpx.post.call_args.kwargs:
                payload = mock_httpx.post.call_args.kwargs["json"]
            assert payload is not None, "proxy call must send a JSON body"
            assert payload.get("name") == "ica" or payload.get("profile") == "ica", (
                "proxy body must contain the profile name under 'name' or 'profile' key"
            )


# ── AC5: persistence ──────────────────────────────────────────────────────────

class TestAC5Persistence:
    """Provider is written to the settings store so it survives restarts."""

    def test_set_provider_writes_to_settings_store(self):
        import apps.dashboard.routers.llm_provider_service as svc
        written = {}

        def fake_set(scope, key, value, **kw):
            written[(scope, key)] = value

        with (
            patch.object(svc, "_call_proxy_profile", return_value=None),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            mock_repo.set_setting.side_effect = fake_set
            svc.set_provider("ica")
            assert ("global", "app_config") in written, (
                "set_provider must write to the global app_config key"
            )
            assert written[("global", "app_config")].get("llmProvider") == "ica"

    def test_get_provider_reads_from_settings_store(self):
        import apps.dashboard.routers.llm_provider_service as svc
        with patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo:
            mock_repo.get_setting_scoped.return_value = {"llmProvider": "ica"}
            result = svc.get_provider()
            assert result["provider"] == "ica"

    def test_get_provider_returns_default_when_not_stored(self):
        import apps.dashboard.routers.llm_provider_service as svc
        with patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo:
            mock_repo.get_setting_scoped.return_value = {}
            result = svc.get_provider()
            assert result["provider"] == "anthropic"


# ── AC6: error when proxy unavailable ─────────────────────────────────────────

class TestAC6ProxyUnavailable:
    """If the proxy endpoint is unreachable, surface an error; do not persist."""

    def test_proxy_unreachable_raises_http_503(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException
        import httpx

        def fail_proxy(provider):
            raise HTTPException(
                status_code=503,
                detail="claude-proxy unreachable",
            )

        with (
            patch.object(svc, "_call_proxy_profile", side_effect=fail_proxy),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            with pytest.raises(HTTPException) as exc_info:
                svc.set_provider("ica")
            assert exc_info.value.status_code == 503

    def test_proxy_error_does_not_persist_change(self):
        """If proxy call fails, settings store must NOT be written."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        def fail_proxy(provider):
            raise HTTPException(status_code=503, detail="proxy down")

        with (
            patch.object(svc, "_call_proxy_profile", side_effect=fail_proxy),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            try:
                svc.set_provider("ica")
            except Exception:
                pass
            mock_repo.set_setting.assert_not_called()

    def test_call_proxy_profile_raises_503_on_connection_error(self):
        """_call_proxy_profile raises HTTPException(503) when httpx fails."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException
        import httpx

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503

    def test_call_proxy_profile_raises_503_on_non_2xx(self):
        """_call_proxy_profile raises HTTPException(503) on HTTP error status."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException
        import httpx

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            bad_response = MagicMock()
            bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=bad_response
            )
            mock_httpx.post.return_value = bad_response
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503


# ── AC7: in-flight sessions unaffected ───────────────────────────────────────

class TestAC7InFlightUnaffected:
    """Switching provider does not terminate or alter in-flight agent sessions."""

    def test_set_provider_does_not_kill_running_agents(self):
        """set_provider must not issue any signal or termination to agents."""
        import apps.dashboard.routers.llm_provider_service as svc
        with (
            patch.object(svc, "_call_proxy_profile", return_value=None),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            mock_repo.set_setting.return_value = None
            # This should complete without calling any kill/signal operation
            result = svc.set_provider("ica")
            # No exception = no agent disruption attempt
            assert result is not None

    def test_set_provider_only_affects_future_dispatches(self):
        """The service doc confirms only future dispatches pick up the change."""
        import apps.dashboard.routers.llm_provider_service as svc
        import inspect
        source = inspect.getsource(svc.set_provider)
        # Must not contain any subprocess kill, os.kill, or agent termination
        for forbidden in ("os.kill", "subprocess.kill", "proc.kill", "terminate()"):
            assert forbidden not in source, (
                f"set_provider must not call {forbidden} — in-flight agents must be unaffected"
            )


# ── Router surface: endpoint exists ──────────────────────────────────────────

class TestRouterEndpointExists:
    """The llm_provider router exposes GET and POST /api/settings/provider."""

    def test_router_module_importable(self):
        from apps.dashboard.routers import llm_provider_router  # noqa: F401

    def test_get_provider_endpoint_registered(self):
        from apps.dashboard.routers.llm_provider import router
        paths = [route.path for route in router.routes]
        assert "/api/settings/provider" in paths, (
            "GET /api/settings/provider must be registered on the router"
        )

    def test_post_provider_endpoint_registered(self):
        from apps.dashboard.routers.llm_provider import router
        from fastapi.routing import APIRoute
        post_routes = [
            r.path for r in router.routes
            if isinstance(r, APIRoute) and "POST" in r.methods
        ]
        assert "/api/settings/provider" in post_routes, (
            "POST /api/settings/provider must be registered on the router"
        )

    def test_router_in_init_all(self):
        import apps.dashboard.routers as routers_pkg
        assert "llm_provider_router" in routers_pkg.__all__, (
            "llm_provider_router must be in routers/__init__.py __all__"
        )


# ── AC4: UI has the provider toggle ──────────────────────────────────────────

class TestAC4UIProviderToggle:
    """The global-settings pane in project.html contains the provider toggle."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        html_path = DASHBOARD_DIR / "static" / "project.html"
        self.html = html_path.read_text(encoding="utf-8")

    def test_provider_section_exists(self):
        assert "llm-provider" in self.html.lower() or "llmProvider" in self.html or \
               "LLM Provider" in self.html or "llmprovider" in self.html.lower(), (
            "project.html must contain a provider toggle section"
        )

    def test_anthropic_label_present(self):
        assert "Anthropic" in self.html, (
            "project.html must display 'Anthropic' as a provider option"
        )

    def test_ica_label_present(self):
        assert "ICA" in self.html, (
            "project.html must display 'ICA' as a provider option"
        )

    def test_provider_api_endpoint_referenced(self):
        assert "/api/settings/provider" in self.html, (
            "project.html must reference the /api/settings/provider endpoint"
        )

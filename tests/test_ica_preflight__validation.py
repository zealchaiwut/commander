"""Tests for issue #1668 — ICA preflight readiness check before sprint start.

Each test is anchored to a specific acceptance criterion.

AC1  When provider = ICA, a preflight check runs automatically before sprint start.
AC2  /health endpoint unreachable or non-2xx → sprint blocked with descriptive error.
AC3  model_map missing a required model → per-model error message.
AC4  Multiple missing models → all reported in one error message.
AC5  Sprint start fully blocked (no agents dispatched) when either check fails.
AC6  provider = Anthropic → preflight not executed, behaviour unchanged.
AC7  All checks pass → sprint proceeds normally (no error raised).
AC8  Unit tests cover: proxy unreachable, partial model_map, empty model_map, all passing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_resp(status: int = 200, body: dict | None = None):
    """Build a minimal httpx-shaped mock response."""
    import httpx

    resp = MagicMock()
    resp.status_code = status
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = body if body is not None else {}
    return resp


# ── AC2 / AC8: proxy unreachable ─────────────────────────────────────────────

class TestProxyUnreachable:
    """AC2 / AC8: /health unreachable → IcaPreflightError with descriptive message."""

    def test_connect_error_raises_preflight_error(self):
        """ConnectError → IcaPreflightError mentioning the proxy check."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.side_effect = httpx.ConnectError("Connection refused")
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        assert "ICA not ready" in str(exc_info.value)
        assert "/health" in str(exc_info.value)

    def test_timeout_raises_preflight_error(self):
        """TimeoutException → IcaPreflightError."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.side_effect = httpx.TimeoutException("Timed out")
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        assert "ICA not ready" in str(exc_info.value)

    def test_non_2xx_status_raises_preflight_error(self):
        """AC2: non-2xx HTTP status → IcaPreflightError with status code in message."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(status=503)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        assert "503" in str(exc_info.value)

    def test_404_status_raises_preflight_error(self):
        """Non-2xx 404 → IcaPreflightError."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(status=404)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        assert "ICA not ready" in str(exc_info.value)


# ── AC3 / AC8: partial model_map ─────────────────────────────────────────────

class TestPartialModelMap:
    """AC3 / AC8: model_map missing one required model → per-model error."""

    def test_missing_haiku_raises_with_model_name(self):
        """model_map contains sonnet but not haiku → error names haiku."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        body = {"model_map": {"claude-sonnet-4-6": "some-target"}}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        msg = str(exc_info.value)
        assert "claude-haiku-4-5" in msg
        assert "ICA not ready" in msg

    def test_missing_sonnet_raises_with_model_name(self):
        """model_map contains haiku but not sonnet → error names sonnet."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        body = {"model_map": {"claude-haiku-4-5": "some-target"}}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        msg = str(exc_info.value)
        assert "claude-sonnet-4-6" in msg
        assert "ICA not ready" in msg

    def test_single_missing_model_error_format(self):
        """AC3: single missing model uses the canonical message format."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        body = {"model_map": {"claude-haiku-4-5": "x"}}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        assert "not mapped in proxy" in str(exc_info.value)


# ── AC4 / AC8: empty model_map — all missing ─────────────────────────────────

class TestEmptyModelMap:
    """AC4 / AC8: empty model_map → single error listing all missing models."""

    def test_empty_model_map_raises(self):
        """AC8: empty model_map → IcaPreflightError."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        body = {"model_map": {}}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        msg = str(exc_info.value)
        assert "ICA not ready" in msg

    def test_empty_model_map_reports_all_missing_in_one_error(self):
        """AC4: all required models missing → all reported in a single message."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            REQUIRED_MODELS,
            check_ica_readiness,
        )
        body = {"model_map": {}}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError) as exc_info:
                check_ica_readiness()
        msg = str(exc_info.value)
        for model in REQUIRED_MODELS:
            assert model in msg, f"Expected {model!r} in error: {msg!r}"

    def test_absent_model_map_key_treated_as_empty(self):
        """Health body with no model_map key → all required models missing."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            IcaPreflightError,
            check_ica_readiness,
        )
        body = {"status": "ok"}  # no model_map key
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            with pytest.raises(IcaPreflightError):
                check_ica_readiness()


# ── AC7 / AC8: all checks pass ───────────────────────────────────────────────

class TestAllChecksPassing:
    """AC7 / AC8: proxy up, all required models present → no error raised."""

    def test_all_required_models_present_passes(self):
        """AC8: fully-passing scenario returns None without raising."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            REQUIRED_MODELS,
            check_ica_readiness,
        )
        model_map = {m: f"mapped-{m}" for m in REQUIRED_MODELS}
        body = {"model_map": model_map}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            result = check_ica_readiness()
        assert result is None

    def test_extra_models_in_map_dont_cause_failure(self):
        """model_map with required + extra models still passes."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            REQUIRED_MODELS,
            check_ica_readiness,
        )
        model_map = {m: "x" for m in REQUIRED_MODELS}
        model_map["claude-opus-4-8"] = "x"
        body = {"model_map": model_map}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body=body)
            result = check_ica_readiness()
        assert result is None

    def test_custom_proxy_url_is_used(self):
        """proxy_url param overrides the default URL."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            REQUIRED_MODELS,
            check_ica_readiness,
        )
        model_map = {m: "x" for m in REQUIRED_MODELS}
        with patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx:
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body={"model_map": model_map})
            check_ica_readiness(proxy_url="http://myproxy:19090")
            called_url = mock_httpx.get.call_args[0][0]
        assert "19090" in called_url, "custom proxy_url must be reflected in the GET call"

    def test_commander_proxy_url_env_is_used(self):
        """COMMANDER_PROXY_URL env var overrides the default when proxy_url not passed."""
        import httpx
        from services.sprint_manager.ica_preflight import (
            REQUIRED_MODELS,
            check_ica_readiness,
        )
        model_map = {m: "x" for m in REQUIRED_MODELS}
        with (
            patch.dict(os.environ, {"COMMANDER_PROXY_URL": "http://envproxy:29090"}),
            patch("services.sprint_manager.ica_preflight.httpx") as mock_httpx,
        ):
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.get.return_value = _make_resp(body={"model_map": model_map})
            check_ica_readiness()
            called_url = mock_httpx.get.call_args[0][0]
        assert "29090" in called_url


# ── AC1 / AC6: provider gate — only runs for ICA ─────────────────────────────

class TestProviderGate:
    """AC1 / AC6: check_ica_readiness called only when provider == 'ica'."""

    def test_required_models_constant_includes_expected_models(self):
        """AC1: REQUIRED_MODELS contains the models Commander dispatch emits."""
        from services.sprint_manager.ica_preflight import REQUIRED_MODELS
        assert "claude-sonnet-4-6" in REQUIRED_MODELS
        assert "claude-haiku-4-5" in REQUIRED_MODELS

    def test_module_importable(self):
        """ica_preflight module must import without error."""
        import services.sprint_manager.ica_preflight  # noqa: F401

    def test_ica_preflight_error_is_runtime_error(self):
        """IcaPreflightError is a RuntimeError subclass."""
        from services.sprint_manager.ica_preflight import IcaPreflightError
        assert issubclass(IcaPreflightError, RuntimeError)


# ── AC1: sprint_manager integration ──────────────────────────────────────────

class TestSprintManagerIntegration:
    """AC1 / AC5 / AC6: sprint_manager.run_sprint_preflight calls ICA preflight
    when llmProvider == 'ica' and skips it for other providers."""

    def _make_preflight_kwargs(self) -> dict:
        """Minimal kwargs for run_sprint_preflight that return early."""
        return {
            "label": "sprint-99",
            "alert_modes": [],
            "repo_name": None,
            "dry_run": True,
        }

    def test_ica_provider_triggers_preflight(self):
        """AC1: provider=ica → check_ica_readiness is invoked."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        with (
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA not ready: test"),
            ) as mock_check,
            patch(
                "services.sprint_manager.sprint_manager._get_llm_provider",
                return_value="ica",
            ),
        ):
            try:
                sm.run_sprint_preflight(**self._make_preflight_kwargs())
            except Exception:
                pass
            mock_check.assert_called_once()

    def test_anthropic_provider_skips_preflight(self):
        """AC6: provider=anthropic → check_ica_readiness is NOT invoked."""
        import services.sprint_manager.sprint_manager as sm

        with (
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
            ) as mock_check,
            patch(
                "services.sprint_manager.sprint_manager._get_llm_provider",
                return_value="anthropic",
            ),
        ):
            try:
                sm.run_sprint_preflight(**self._make_preflight_kwargs())
            except Exception:
                pass
            mock_check.assert_not_called()

    def test_ica_preflight_failure_causes_early_exit(self):
        """AC5: IcaPreflightError causes run_sprint_preflight to return early_exit=True."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        with (
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA not ready: test blocked"),
            ),
            patch(
                "services.sprint_manager.sprint_manager._get_llm_provider",
                return_value="ica",
            ),
        ):
            result = sm.run_sprint_preflight(**self._make_preflight_kwargs())
        assert result.early_exit is True, (
            "IcaPreflightError must cause run_sprint_preflight to early-exit"
        )

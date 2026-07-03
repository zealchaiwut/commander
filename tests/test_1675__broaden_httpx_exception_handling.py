"""Test suite for issue #1675: Broaden httpx exception handling in _call_proxy_profile.

Each test is anchored to a specific acceptance criterion from the issue.

AC1  The first except clause catches httpx.RequestError (not the separate
     ConnectError + TimeoutException combination).
AC2  A separate except httpx.HTTPStatusError branch is retained.
AC3  Any httpx.RequestError subclass (including RemoteProtocolError,
     ConnectError, TimeoutException) raises HTTP 503.
AC4  The 503 response body matches the existing documented error shape.
AC5  When a transport error is raised, set_setting is never called.
AC6  Existing ConnectError and TimeoutException paths still raise 503;
     new test covers RemoteProtocolError raising 503.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: single RequestError branch replaces ConnectError + TimeoutException ──

class TestAC1SingleRequestErrorBranch:
    """The except clause uses httpx.RequestError, not ConnectError+TimeoutException."""

    def test_source_uses_request_error_not_connect_error_plus_timeout(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        assert "httpx.RequestError" in source, (
            "_call_proxy_profile must catch httpx.RequestError"
        )
        # The old tuple of two separate exception types must be gone
        assert "httpx.ConnectError, httpx.TimeoutException" not in source, (
            "Old separate ConnectError + TimeoutException tuple must be removed"
        )
        assert "(httpx.ConnectError, httpx.TimeoutException)" not in source, (
            "Old except tuple must be replaced with httpx.RequestError"
        )


# ── AC2: HTTPStatusError branch is retained ───────────────────────────────────

class TestAC2HTTPStatusErrorBranchRetained:
    """A separate except httpx.HTTPStatusError branch still exists."""

    def test_source_retains_http_status_error_branch(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        assert "httpx.HTTPStatusError" in source, (
            "_call_proxy_profile must retain the except httpx.HTTPStatusError branch"
        )


# ── AC3: any RequestError subclass → 503 ──────────────────────────────────────

class TestAC3AnyRequestErrorSubclassRaises503:
    """RemoteProtocolError, ConnectError, and TimeoutException all produce 503."""

    def test_remote_protocol_error_raises_503(self):
        """httpx.RemoteProtocolError (new coverage) must surface as 503, not 500."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.RemoteProtocolError(
                "half-open connection", request=MagicMock()
            )
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503, (
                "RemoteProtocolError must produce HTTP 503, not 500"
            )

    def test_connect_error_still_raises_503(self):
        """ConnectError (previously caught) must still produce 503."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503

    def test_timeout_exception_still_raises_503(self):
        """TimeoutException (previously caught) must still produce 503."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.TimeoutException("timed out")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503


# ── AC4: 503 response body matches documented error shape ─────────────────────

class TestAC4ErrorShapeConsistent:
    """All transport errors produce the same JSON error shape (503 + detail str)."""

    def _get_503_detail(self, exc_to_raise):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = exc_to_raise
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            return exc_info.value.detail

    def test_remote_protocol_error_detail_is_string(self):
        detail = self._get_503_detail(
            httpx.RemoteProtocolError("half-open", request=MagicMock())
        )
        assert isinstance(detail, str), "503 detail must be a string"

    def test_connect_error_detail_is_string(self):
        detail = self._get_503_detail(httpx.ConnectError("refused"))
        assert isinstance(detail, str)

    def test_timeout_exception_detail_is_string(self):
        detail = self._get_503_detail(httpx.TimeoutException("timed out"))
        assert isinstance(detail, str)

    def test_remote_protocol_error_detail_mentions_url(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.RemoteProtocolError(
                "half-open", request=MagicMock()
            )
            with patch.dict(
                os.environ, {"COMMANDER_PROXY_URL": "http://localhost:9090"}
            ):
                with pytest.raises(HTTPException) as exc_info:
                    svc._call_proxy_profile("ica")
            assert "localhost:9090" in exc_info.value.detail or "9090" in exc_info.value.detail, (
                "503 detail must mention the proxy URL"
            )


# ── AC5: set_setting never called on transport error ─────────────────────────

class TestAC5SetSettingNotCalledOnTransportError:
    """When a transport error is raised, set_setting must not be called."""

    def test_remote_protocol_error_does_not_persist(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with (
            patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx,
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.RemoteProtocolError(
                "half-open", request=MagicMock()
            )
            mock_repo.get_setting_scoped.return_value = {}

            try:
                svc.set_provider("ica")
            except HTTPException:
                pass

            mock_repo.set_setting.assert_not_called()

    def test_connect_error_does_not_persist(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with (
            patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx,
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            mock_repo.get_setting_scoped.return_value = {}

            try:
                svc.set_provider("ica")
            except HTTPException:
                pass

            mock_repo.set_setting.assert_not_called()


# ── AC6: HTTPStatusError handled distinctly (not via RequestError path) ───────

class TestAC6HTTPStatusErrorDistinct:
    """Non-2xx HTTP response is handled by HTTPStatusError branch, not RequestError."""

    def test_non_2xx_response_raises_503_via_status_error_branch(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            bad_response = MagicMock()
            bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request", request=MagicMock(), response=bad_response
            )
            bad_response.status_code = 400
            mock_httpx.post.return_value = bad_response
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503

    def test_non_2xx_response_detail_mentions_status_code(self):
        """HTTPStatusError detail references the proxy's response code."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            bad_response = MagicMock()
            bad_response.status_code = 400
            bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request", request=MagicMock(), response=bad_response
            )
            mock_httpx.post.return_value = bad_response
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert "400" in exc_info.value.detail, (
                "HTTPStatusError detail must include the response status code"
            )

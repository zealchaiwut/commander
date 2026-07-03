"""Test suite for issue #1675: Broaden httpx exception handling to httpx.RequestError.

Each test is anchored to a specific acceptance criterion from the issue.

AC1  First except clause catches httpx.RequestError (not separate ConnectError/TimeoutException)
AC2  Separate except httpx.HTTPStatusError branch is retained
AC3  Any httpx.RequestError subclass (RemoteProtocolError, ConnectError, TimeoutException) → 503
AC4  503 response body matches existing documented error shape (string detail)
AC5  When a transport error is raised, set_setting is never called
AC6  Existing ConnectError/TimeoutException paths still pass; new test for RemoteProtocolError → 503
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


# ── AC1: First except clause catches httpx.RequestError ──────────────────────

class TestAC1ExceptUsesRequestError:
    """The first except clause in _call_proxy_profile uses httpx.RequestError."""

    def test_request_error_present_in_source(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        assert "httpx.RequestError" in source, (
            "_call_proxy_profile must catch httpx.RequestError"
        )

    def test_no_separate_connect_error_clause(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("except") and "ConnectError" in stripped:
                pytest.fail(
                    f"Separate ConnectError except clause found: {stripped!r}; "
                    "it should be subsumed by httpx.RequestError"
                )

    def test_no_separate_timeout_exception_clause(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("except") and "TimeoutException" in stripped:
                pytest.fail(
                    f"Separate TimeoutException except clause found: {stripped!r}; "
                    "it should be subsumed by httpx.RequestError"
                )


# ── AC2: Separate HTTPStatusError branch retained ─────────────────────────────

class TestAC2HTTPStatusErrorBranchRetained:
    """except httpx.HTTPStatusError is kept as a distinct branch."""

    def test_http_status_error_in_source(self):
        import apps.dashboard.routers.llm_provider_service as svc
        source = inspect.getsource(svc._call_proxy_profile)
        assert "httpx.HTTPStatusError" in source, (
            "_call_proxy_profile must retain a separate except httpx.HTTPStatusError branch"
        )

    def test_non_2xx_response_handled_by_status_error_branch(self):
        """A non-2xx proxy response still yields 503 via the HTTPStatusError branch."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            bad_resp = MagicMock()
            bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request", request=MagicMock(), response=bad_resp
            )
            mock_httpx.post.return_value = bad_resp
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503


# ── AC3: Any RequestError subclass maps to 503 ───────────────────────────────

class TestAC3AnyRequestErrorSubclass503:
    """RemoteProtocolError, ConnectError, and TimeoutException all yield 503."""

    def test_remote_protocol_error_raises_503(self):
        """httpx.RemoteProtocolError is a new case not previously caught."""
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.RemoteProtocolError(
                "half-open connection"
            )
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503

    def test_connect_error_still_raises_503(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.ConnectError("connection refused")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503

    def test_timeout_exception_still_raises_503(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.TimeoutException("timed out")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            assert exc_info.value.status_code == 503


# ── AC4: 503 body matches existing documented error shape ─────────────────────

class TestAC4ResponseBodyShape:
    """503 detail is a non-empty string for all transport-error cases."""

    def test_remote_protocol_error_503_detail_is_string(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.RemoteProtocolError("flaky proxy")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            exc = exc_info.value
            assert isinstance(exc.detail, str) and len(exc.detail) > 0, (
                "503 detail must be a non-empty string"
            )

    def test_connect_error_503_detail_is_string(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        with patch("apps.dashboard.routers.llm_provider_service.httpx") as mock_httpx:
            mock_httpx.RequestError = httpx.RequestError
            mock_httpx.HTTPStatusError = httpx.HTTPStatusError
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            with pytest.raises(HTTPException) as exc_info:
                svc._call_proxy_profile("ica")
            exc = exc_info.value
            assert isinstance(exc.detail, str) and len(exc.detail) > 0


# ── AC5: set_setting not called on transport error ───────────────────────────

class TestAC5NoSettingsPersistOnTransportError:
    """If _call_proxy_profile raises (transport error), set_setting is never called."""

    def test_remote_protocol_error_does_not_persist_provider(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        def fail_transport(_provider):
            raise HTTPException(status_code=503, detail="transport failure")

        with (
            patch.object(svc, "_call_proxy_profile", side_effect=fail_transport),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            with pytest.raises(HTTPException):
                svc.set_provider("ica")
            mock_repo.set_setting.assert_not_called()

    def test_connect_error_does_not_persist_provider(self):
        import apps.dashboard.routers.llm_provider_service as svc
        from fastapi import HTTPException

        def fail_transport(_provider):
            raise HTTPException(status_code=503, detail="connect failed")

        with (
            patch.object(svc, "_call_proxy_profile", side_effect=fail_transport),
            patch("apps.dashboard.routers.llm_provider_service._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            with pytest.raises(HTTPException):
                svc.set_provider("anthropic")
            mock_repo.set_setting.assert_not_called()

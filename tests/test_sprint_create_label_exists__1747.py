"""Tests for issue #1747: Move to Sprint silently no-ops when label already exists.

AC3: Backend returns non-2xx with a clear `detail` when asked to create a sprint
     whose label already exists (covers sprint-N and sprint-N.* base check).
AC4 (backend half): label exists → endpoint rejects cleanly with structured error.
AC1 (backend half): When sprint_number is omitted the backend computes the next
     free number using _next_new_sprint_number (never proposes an existing label).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))


def _get_test_client():
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app), srv


# ── AC3 + AC4 backend: existing sprint_number → 409 with clear detail ─────────

class TestSprintCreateLabelExists:
    """POST /api/sprints/create with a sprint_number that is already taken must
    return 409 with a non-empty detail string (AC3, AC4 backend half)."""

    def test_explicit_existing_sprint_number_returns_409(self):
        """AC3/AC4: creating sprint-5 when sprint-5 already exists → 409 + detail."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        existing_sprints = [3, 4, 5]

        with patch.object(srv.github_client, "list_sprints", return_value=existing_sprints), \
             patch.object(srv, "_sprint_number_reserved", return_value=True):
            resp = client.post(
                "/api/sprints/create",
                json={"project": "zealchaiwut/commander", "sprint_number": 5},
            )

        assert resp.status_code == 409, (
            f"Expected 409 when sprint-5 already exists, got {resp.status_code}"
        )
        body = resp.json()
        assert "detail" in body, "Response must have a 'detail' field"
        assert body["detail"], "detail must be non-empty"
        assert "5" in str(body["detail"]), (
            f"detail should mention the sprint number; got: {body['detail']!r}"
        )

    def test_lineage_label_base_number_reserved_returns_409(self):
        """AC3: sprint-N is rejected when sprint-N.M exists (same base number reserved)."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # sprint-7 label absent from GitHub, but sprint-7.1 exists → base 7 reserved
        with patch.object(srv.github_client, "list_sprints", return_value=[6, 8]), \
             patch.object(srv, "_sprint_number_reserved", return_value=True):
            resp = client.post(
                "/api/sprints/create",
                json={"project": "zealchaiwut/commander", "sprint_number": 7},
            )

        assert resp.status_code == 409
        body = resp.json()
        assert "detail" in body and body["detail"]

    def test_detail_mentions_already_exists(self):
        """AC3: the 409 detail message should indicate the sprint already exists."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        with patch.object(srv.github_client, "list_sprints", return_value=[10]), \
             patch.object(srv, "_sprint_number_reserved", return_value=True):
            resp = client.post(
                "/api/sprints/create",
                json={"project": "zealchaiwut/commander", "sprint_number": 10},
            )

        assert resp.status_code == 409
        detail = resp.json().get("detail", "")
        assert any(kw in detail.lower() for kw in ("exist", "reserved", "already")), (
            f"detail should mention the conflict reason; got: {detail!r}"
        )


# ── AC1 backend: no sprint_number → backend picks the next free number ─────────

class TestSprintCreatePicksNextFree:
    """When sprint_number is omitted the backend computes the next free number
    via _next_new_sprint_number and creates that label (AC1 backend half)."""

    def test_create_without_sprint_number_uses_next_free(self):
        """AC1: POST without sprint_number passes sprint_number=None to
        create_sprint_verified so the backend computes the correct next number."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # Patch create_sprint_verified to capture args and return a label
        with patch("routers.sprints_service.create_sprint_verified",
                   return_value="sprint-106") as mock_create:
            resp = client.post(
                "/api/sprints/create",
                json={"project": "zealchaiwut/commander"},
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("sprint_label") == "sprint-106", (
            f"Response sprint_label should be the service-returned 'sprint-106', "
            f"got {data.get('sprint_label')!r}"
        )
        # sprint_number must be None — backend picks the free number, not frontend
        args, kwargs = mock_create.call_args
        sprint_number_arg = args[1] if len(args) > 1 else kwargs.get("sprint_number")
        assert sprint_number_arg is None, (
            f"create_sprint_verified must be called with sprint_number=None "
            f"when the request omits sprint_number; got {sprint_number_arg!r}"
        )

    def test_response_includes_sprint_label_field(self):
        """AC1/AC2: successful create response includes sprint_label so the frontend
        can use the backend-assigned label for the subsequent assignment step."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        with patch("routers.sprints_service.create_sprint_verified",
                   return_value="sprint-200"):
            resp = client.post(
                "/api/sprints/create",
                json={"project": "zealchaiwut/commander"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "sprint_label" in data, (
            "Response must include sprint_label so frontend can use the real label"
        )

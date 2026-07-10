"""Tests for issue #1823: sprint create error handler raises AttributeError.

AC1: sprint_crud.py catches SprintCreationError from the real class (not via
     sprints_service module attribute that doesn't exist).
AC2: Failing sprint creation returns JSON error body with step + reason —
     never a bare 500.
AC3: routers/logs.py no longer references logs_service._slog (non-existent
     attribute); _slog is accessible at module scope in logs.py.
AC4: Regression — force SprintCreationError in create_sprint_verified →
     endpoint returns step-named message and error status_code.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ── AC3: logs.py:30 — _slog accessible at module scope after fix ──────────────

def test_logs_module_has_slog_at_module_scope():
    """AC3: After the fix, logs.py imports _slog directly so route handler won't AttributeError."""
    # Clear cached version that may have the old broken import state
    for key in list(sys.modules):
        if key in ("routers.logs", "routers.logs_service", "routers"):
            sys.modules.pop(key, None)

    # Import the logs router module — if _slog is imported directly, it will be
    # a module-level name and the route handler at line 30 won't AttributeError.
    import routers.logs as logs_mod  # noqa: PLC0415

    assert hasattr(logs_mod, "_slog"), (
        "routers/logs.py must import _slog at module scope so that "
        "the route handler at line 30 does not raise AttributeError at runtime. "
        "Add: from services.logging import log as _slog"
    )


# ── AC1 + AC2 + AC4: SprintCreationError properly caught ─────────────────────

def _make_mini_app():
    """Return a mini FastAPI app with just the sprint_crud router mounted."""
    from fastapi import FastAPI  # noqa: PLC0415
    import routers.sprint_crud as sprint_crud_mod  # noqa: PLC0415

    mini = FastAPI()
    mini.include_router(sprint_crud_mod.router)
    return mini, sprint_crud_mod


def test_sprint_create_returns_step_error_not_500():
    """AC1/AC2/AC4: SprintCreationError → HTTP error with step message, not bare 500."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    # Clear stale module cache so patch takes effect cleanly
    for key in list(sys.modules):
        if "sprint_crud" in key or "sprints_service" in key:
            sys.modules.pop(key, None)

    from services.sprint_manager.sprint_creation import SprintCreationError  # noqa: PLC0415
    import routers.sprints_service as ss  # noqa: PLC0415
    import routers.sprint_crud as sprint_crud_mod  # noqa: PLC0415

    forced_error = SprintCreationError(
        "apply sprint label to tickets",
        "verification failed — the change did not take effect",
        502,
    )

    mock_srv = MagicMock()
    mock_srv._emit_dashboard_event = MagicMock()

    with patch.object(ss, "create_sprint_verified", side_effect=forced_error), \
         patch.object(sprint_crud_mod, "_server", return_value=mock_srv):

        from fastapi import FastAPI  # noqa: PLC0415
        mini = FastAPI()
        mini.include_router(sprint_crud_mod.router)
        client = TestClient(mini, raise_server_exceptions=False)

        resp = client.post(
            "/api/sprints/create",
            json={"project": "zealchaiwut/commander"},
        )

    # AC2: must NOT be a bare 500 (what AttributeError produces)
    assert resp.status_code != 500, (
        f"Got 500 — SprintCreationError not caught, likely AttributeError in except clause.\n"
        f"Response: {resp.text}"
    )
    # AC1/AC4: must be the error's declared status_code
    assert resp.status_code == 502, (
        f"Expected 502 (SprintCreationError.status_code), got {resp.status_code}:\n{resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    # AC4: step name and reason must appear in the response
    assert "apply sprint label to tickets" in detail, (
        f"Step name missing from error detail. Got: {detail!r}"
    )
    assert "verification failed" in detail, (
        f"Reason missing from error detail. Got: {detail!r}"
    )


def test_sprint_create_returns_json_on_creation_error():
    """AC2: Response body is valid JSON — not an empty or HTML body."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from services.sprint_manager.sprint_creation import SprintCreationError  # noqa: PLC0415
    import routers.sprints_service as ss  # noqa: PLC0415
    import routers.sprint_crud as sprint_crud_mod  # noqa: PLC0415

    forced_error = SprintCreationError("create GitHub label", "Rate limit exceeded", 429)
    mock_srv = MagicMock()

    with patch.object(ss, "create_sprint_verified", side_effect=forced_error), \
         patch.object(sprint_crud_mod, "_server", return_value=mock_srv):

        from fastapi import FastAPI  # noqa: PLC0415
        mini = FastAPI()
        mini.include_router(sprint_crud_mod.router)
        client = TestClient(mini, raise_server_exceptions=False)

        resp = client.post(
            "/api/sprints/create",
            json={"project": "zealchaiwut/commander"},
        )

    assert resp.status_code == 429
    body = resp.json()
    assert "detail" in body, f"JSON body missing 'detail' key: {body}"
    assert "create GitHub label" in body["detail"]
    assert "Rate limit exceeded" in body["detail"]


def test_sprint_creation_error_class_attributes():
    """AC1: SprintCreationError has step, reason, status_code and message attributes."""
    from services.sprint_manager.sprint_creation import SprintCreationError  # noqa: PLC0415

    err = SprintCreationError("write sprint plan file", "disk full", 502)
    assert err.step == "write sprint plan file"
    assert err.reason == "disk full"
    assert err.status_code == 502
    assert "write sprint plan file" in err.message
    assert "disk full" in err.message

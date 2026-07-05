"""Tests for issue #501 — Add stderr to estimation error messages.

When _ei_fetch_issue() raises CalledProcessError, error messages must include
subprocess stderr so transient fetch failures are debuggable.

AC coverage:
  AC1: POST /api/issues/{id}/estimate returns 404 with stderr appended to detail
       when _ei_fetch_issue raises CalledProcessError with non-empty stderr.
  AC2: POST /api/issues/{id}/estimate returns 404 cleanly (no crash) when
       CalledProcessError has None/empty stderr.
  AC3: _preflight_estimate_one re-raises with stderr text when fetch fails,
       so the preflight-fix SSE stream can surface the stderr to the operator.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(DASHBOARD_DIR.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cpe(stderr: str | None = None) -> subprocess.CalledProcessError:
    """Build a CalledProcessError with the given stderr."""
    e = subprocess.CalledProcessError(
        returncode=1,
        cmd=["gh", "api", "repos/test/repo/issues/42"],
        stderr=stderr,
    )
    return e


# ── AC1: endpoint includes stderr in 404 detail ───────────────────────────────

def test_ac1_estimate_endpoint_includes_stderr_in_404():
    """AC1: 404 detail includes stderr when _ei_fetch_issue fails with stderr output."""
    from fastapi.testclient import TestClient
    from server import app

    cpe = _make_cpe(stderr="error: could not resolve host: api.github.com")

    with (
        patch("server._ei_fetch_issue", side_effect=cpe),
        patch("server._slog", MagicMock()),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/issues/42/estimate?repo=test/repo")

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    detail = resp.json().get("detail", "")
    assert "stderr" in detail, (
        f"Expected 'stderr' in detail (issue #501 AC1). Got: {detail!r}"
    )
    assert "could not resolve host" in detail, (
        f"Expected stderr content in detail (issue #501 AC1). Got: {detail!r}"
    )


# ── AC2: endpoint handles None/empty stderr cleanly ──────────────────────────

@pytest.mark.parametrize("stderr_val", [None, "", "   "])
def test_ac2_estimate_endpoint_404_with_empty_stderr(stderr_val):
    """AC2: 404 returned cleanly when CalledProcessError has empty/None stderr."""
    from fastapi.testclient import TestClient
    from server import app

    cpe = _make_cpe(stderr=stderr_val)

    with (
        patch("server._ei_fetch_issue", side_effect=cpe),
        patch("server._slog", MagicMock()),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/issues/42/estimate?repo=test/repo")

    assert resp.status_code == 404, (
        f"Expected 404 for empty stderr={stderr_val!r}, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail", "")
    assert "Could not fetch issue" in detail, (
        f"Expected base error message in detail. Got: {detail!r}"
    )


# ── AC3: _preflight_estimate_one re-raises with stderr ────────────────────────

def test_ac3_preflight_estimate_one_reraises_with_stderr():
    """AC3: _preflight_estimate_one raises an exception whose message includes
    stderr text when _ei_fetch_issue fails — so the preflight-fix SSE stream
    can surface it via str(e)."""
    from apps.dashboard.routers.sprint_preflight import _preflight_estimate_one

    cpe = _make_cpe(stderr="rate limit exceeded: 403 Forbidden")

    with patch(
        "apps.dashboard.routers.sprint_preflight._ei_fetch_issue",
        side_effect=cpe,
    ):
        with pytest.raises(Exception) as exc_info:
            _preflight_estimate_one(42, "test/repo")

    exc_msg = str(exc_info.value)
    assert "rate limit exceeded" in exc_msg, (
        f"stderr must appear in re-raised exception message (issue #501 AC3). "
        f"Got: {exc_msg!r}"
    )
    assert "stderr" in exc_msg, (
        f"'stderr' label must appear in message (issue #501 AC3). Got: {exc_msg!r}"
    )


def test_ac3_preflight_estimate_one_reraises_without_stderr_when_empty():
    """AC3 edge case: when CalledProcessError has no stderr, exception message
    still contains a meaningful error (no crash, no 'stderr: ' with empty value)."""
    from apps.dashboard.routers.sprint_preflight import _preflight_estimate_one

    cpe = _make_cpe(stderr=None)

    with patch(
        "apps.dashboard.routers.sprint_preflight._ei_fetch_issue",
        side_effect=cpe,
    ):
        with pytest.raises(Exception) as exc_info:
            _preflight_estimate_one(42, "test/repo")

    exc_msg = str(exc_info.value)
    assert "Could not fetch issue" in exc_msg, (
        f"Base error message must be present (issue #501 AC3). Got: {exc_msg!r}"
    )
    # Should not have a dangling '— stderr: ' with nothing after it
    assert "stderr: \n" not in exc_msg and exc_msg.strip().endswith("stderr:") is False, (
        f"Should not have dangling empty stderr label. Got: {exc_msg!r}"
    )

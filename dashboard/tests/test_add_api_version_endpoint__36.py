"""Tests for issue #36: Add GET /api/version endpoint returning {"version": "<sha>"}.

Acceptance Criteria:
  AC1: A new GET /api/version endpoint exists in server.py (non-404 response).
  AC2: The endpoint returns HTTP status 200.
  AC3: The response body has {"version": "<sha>"} where sha is exactly 7 hex characters.
  AC4: The returned sha matches `git rev-parse --short HEAD` from the server's working directory.
  AC5: Graceful fallback — endpoint never returns HTTP 500.
  AC6: No authentication or rate limiting required.
"""
import re
import subprocess
import pytest
import httpx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_expected_sha(working_dir: str = "/Users/chaiwutchaianuchittrakul/commander/work-tester") -> str:
    """Return the 7-char short SHA from git rev-parse --short HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=working_dir,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ac1_version_endpoint_exists(client):
    """AC1: GET /api/version exists — must not return 404."""
    r = client.get("/api/version")
    assert r.status_code != 404, (
        f"Expected /api/version to exist (non-404), got {r.status_code}"
    )


def test_ac2_version_returns_200(client):
    """AC2: GET /api/version returns HTTP 200."""
    r = client.get("/api/version")
    assert r.status_code == 200, (
        f"Expected HTTP 200 from /api/version, got {r.status_code}: {r.text}"
    )


def test_ac3_response_body_has_version_key_with_7_char_hex_sha(client):
    """AC3: Response body is {"version": "<sha>"} where sha is exactly 7 hex chars
    (or the literal string "unknown" as the fallback value)."""
    r = client.get("/api/version")
    assert r.status_code == 200

    body = r.json()
    assert "version" in body, f"Expected 'version' key in response body, got: {body}"

    sha = body["version"]
    # Must be either a 7-char hex string or "unknown" (fallback)
    valid_sha = re.fullmatch(r"[0-9a-f]{7}", sha)
    is_unknown = sha == "unknown"
    assert valid_sha or is_unknown, (
        f"Expected 7-char hex SHA or 'unknown', got: {sha!r}"
    )


def test_ac4_sha_matches_git_rev_parse(client):
    """AC4: The returned SHA matches `git rev-parse --short HEAD` from the server's cwd."""
    r = client.get("/api/version")
    assert r.status_code == 200

    body = r.json()
    returned_sha = body.get("version", "")

    if returned_sha == "unknown":
        pytest.skip("Server returned 'unknown'; git may be unavailable in server's env.")

    expected_sha = _get_expected_sha()
    assert returned_sha == expected_sha, (
        f"SHA mismatch: endpoint returned {returned_sha!r}, "
        f"git rev-parse --short HEAD returned {expected_sha!r}"
    )


def test_ac5_endpoint_does_not_return_500(client):
    """AC5: Graceful fallback — endpoint never returns HTTP 500 (or any 5xx error)."""
    r = client.get("/api/version")
    assert r.status_code < 500, (
        f"Expected non-5xx status from /api/version, got {r.status_code}: {r.text}"
    )


def test_ac6_no_auth_required(client):
    """AC6: Endpoint is accessible without any authentication headers."""
    # Make request with explicitly no auth headers
    r = client.get("/api/version")
    assert r.status_code not in (401, 403), (
        f"Expected no auth required but got {r.status_code}: {r.text}"
    )
    assert r.status_code == 200, (
        f"Expected 200 without auth, got {r.status_code}"
    )

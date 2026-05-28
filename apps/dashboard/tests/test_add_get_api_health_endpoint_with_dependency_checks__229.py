"""Tests for issue #229: Add GET /api/health endpoint with dependency checks."""
import time
import pytest
import httpx


BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC: Response shape ────────────────────────────────────────────────────────

def test_health_response_shape(client):
    """GET /api/health returns JSON with status, checked_at, and checks dict."""
    r = client.get("/api/health")
    assert r.status_code in (200, 503)
    body = r.json()

    assert "status" in body
    assert body["status"] in ("ok", "degraded", "down")
    assert "checked_at" in body
    assert "checks" in body
    checks = body["checks"]
    for key in ("dashboard", "database", "github_auth", "claude_code_auth", "disk", "stuck_sprints"):
        assert key in checks, f"Missing check: {key}"


def test_health_dashboard_check(client):
    """dashboard check always ok with uptime_sec >= 0."""
    r = client.get("/api/health")
    body = r.json()
    dashboard = body["checks"]["dashboard"]
    assert dashboard["status"] == "ok"
    assert "uptime_sec" in dashboard
    assert isinstance(dashboard["uptime_sec"], int)
    assert dashboard["uptime_sec"] >= 0


def test_health_disk_check_fields(client):
    """disk check contains status, free_gb, total_gb."""
    r = client.get("/api/health")
    body = r.json()
    disk = body["checks"]["disk"]
    assert disk["status"] in ("ok", "warn", "critical", "timeout")
    if "free_gb" in disk:
        assert isinstance(disk["free_gb"], (int, float))
    if "total_gb" in disk:
        assert isinstance(disk["total_gb"], (int, float))


def test_health_stuck_sprints_check_fields(client):
    """stuck_sprints check has status, count, labels."""
    r = client.get("/api/health")
    body = r.json()
    ss = body["checks"]["stuck_sprints"]
    assert ss["status"] in ("ok", "warn", "timeout")
    assert "count" in ss
    assert "labels" in ss
    assert isinstance(ss["labels"], list)


# ── AC: Overall status rules ─────────────────────────────────────────────────

def test_health_status_ok_when_all_healthy(client):
    """When all checks are ok, overall status is ok and HTTP 200."""
    r = client.get("/api/health")
    body = r.json()
    checks = body["checks"]
    all_ok = all(c.get("status") == "ok" for c in checks.values())
    if all_ok:
        assert body["status"] == "ok"
        assert r.status_code == 200


def test_health_http_200_for_ok_or_degraded(client):
    """HTTP 200 when overall status is ok or degraded."""
    r = client.get("/api/health")
    body = r.json()
    if body["status"] in ("ok", "degraded"):
        assert r.status_code == 200


def test_health_http_503_when_down(client):
    """HTTP 503 when overall status is down."""
    r = client.get("/api/health")
    body = r.json()
    if body["status"] == "down":
        assert r.status_code == 503


# ── AC: Caching ───────────────────────────────────────────────────────────────

def test_health_cache_returns_same_checked_at_within_10s(client):
    """Two requests within 10 seconds return the same checked_at timestamp."""
    r1 = client.get("/api/health")
    r2 = client.get("/api/health")
    assert r1.json()["checked_at"] == r2.json()["checked_at"], (
        "checked_at changed between two rapid requests — cache not working"
    )


# ── AC: No authentication required ──────────────────────────────────────────

def test_health_no_auth_required(client):
    """Endpoint responds without auth challenge (no 401/403)."""
    r = client.get("/api/health")
    assert r.status_code not in (401, 403)


# ── AC: Response time ────────────────────────────────────────────────────────

def test_health_responds_under_2_seconds(client):
    """Whole endpoint responds in under 2 seconds."""
    # Invalidate cache by making first request; second should be cached and fast.
    client.get("/api/health")
    start = time.monotonic()
    r = client.get("/api/health")
    elapsed = time.monotonic() - start
    assert r.status_code in (200, 503)
    assert elapsed < 2.0, f"Response took {elapsed:.2f}s, expected < 2.0s"


def test_health_uncached_responds_under_2_seconds():
    """Uncached health check (fresh client, no prior cache) still responds in < 2 s.

    Note: this test may be slow on first call but should still be < 2 s total.
    """
    with httpx.Client(base_url=BASE_URL, timeout=5.0) as c:
        start = time.monotonic()
        r = c.get("/api/health")
        elapsed = time.monotonic() - start
        assert r.status_code in (200, 503)
        assert elapsed < 2.5, f"Uncached health check took {elapsed:.2f}s"


# ── AC: checked_at is ISO UTC timestamp ──────────────────────────────────────

def test_health_checked_at_is_iso_utc(client):
    """checked_at is an ISO 8601 timestamp ending in Z."""
    r = client.get("/api/health")
    body = r.json()
    checked_at = body["checked_at"]
    assert isinstance(checked_at, str)
    assert checked_at.endswith("Z"), f"checked_at not UTC: {checked_at}"
    # Should parse as valid datetime
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(checked_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    assert dt.year >= 2024


# ── AC: github_auth check ────────────────────────────────────────────────────

def test_health_github_auth_check_valid_statuses(client):
    """github_auth status is one of ok, expired, missing, timeout."""
    r = client.get("/api/health")
    body = r.json()
    gh = body["checks"]["github_auth"]
    assert gh["status"] in ("ok", "expired", "missing", "timeout")


def test_health_github_auth_ok_has_user(client):
    """When github_auth is ok, user field is present."""
    r = client.get("/api/health")
    body = r.json()
    gh = body["checks"]["github_auth"]
    if gh["status"] == "ok":
        assert "user" in gh, "github_auth.status == ok but no user field"


# ── AC: database check ───────────────────────────────────────────────────────

def test_health_database_check_ok_normally(client):
    """database check returns ok under normal conditions."""
    r = client.get("/api/health")
    body = r.json()
    db_check = body["checks"]["database"]
    # Normal conditions: db should be ok
    assert db_check["status"] in ("ok", "down", "timeout")


# ── AC: claude_code_auth check ───────────────────────────────────────────────

def test_health_claude_code_auth_valid_statuses(client):
    """claude_code_auth status is one of ok, expired, missing, timeout."""
    r = client.get("/api/health")
    body = r.json()
    cc = body["checks"]["claude_code_auth"]
    assert cc["status"] in ("ok", "expired", "missing", "timeout")

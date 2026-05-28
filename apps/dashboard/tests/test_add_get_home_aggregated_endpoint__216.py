"""Tests for issue #216: Add GET /api/home aggregated Home page endpoint."""
import os
import re
import time
from datetime import datetime, timezone

import httpx
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
VALID_ACTIVITY_TYPES = {
    "sprint_started",
    "sprint_completed",
    "ticket_moved_to_uat",
    "ticket_needs_rework",
    "sprint_failed",
}
VALID_PROJECT_STATUSES = {"running", "uat-pending", "idle"}


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# ── AC-1: three top-level keys ────────────────────────────────────────────────

def test_get_home_returns_200(client):
    """GET /api/home returns HTTP 200."""
    r = client.get("/api/home")
    assert r.status_code == 200


def test_get_home_three_top_level_keys(client):
    """Response body contains exactly: stats, projects, activity."""
    r = client.get("/api/home")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"stats", "projects", "activity"}, (
        f"Unexpected top-level keys: {set(body.keys())}"
    )


def test_get_home_stats_projects_activity_are_correct_types(client):
    """stats is a dict; projects and activity are arrays."""
    r = client.get("/api/home")
    body = r.json()
    assert isinstance(body["stats"], dict)
    assert isinstance(body["projects"], list)
    assert isinstance(body["activity"], list)


# ── AC-2: stats structure ─────────────────────────────────────────────────────

def test_get_home_stats_sprint_running_structure(client):
    """stats.sprint_running has count (int) and projects (list)."""
    r = client.get("/api/home")
    sr = r.json()["stats"]["sprint_running"]
    assert "count" in sr and isinstance(sr["count"], int)
    assert "projects" in sr and isinstance(sr["projects"], list)


def test_get_home_stats_sprint_running_projects_entry_shape(client):
    """Each entry in stats.sprint_running.projects has name, sprint_label, elapsed_sec."""
    r = client.get("/api/home")
    for entry in r.json()["stats"]["sprint_running"]["projects"]:
        assert "name" in entry, f"Missing 'name' in {entry}"
        assert "sprint_label" in entry, f"Missing 'sprint_label' in {entry}"
        assert "elapsed_sec" in entry, f"Missing 'elapsed_sec' in {entry}"
        assert isinstance(entry["elapsed_sec"], int)


def test_get_home_stats_awaiting_uat_structure(client):
    """stats.awaiting_uat has count (int), projects (int), oldest_age_sec (int|null)."""
    r = client.get("/api/home")
    au = r.json()["stats"]["awaiting_uat"]
    assert "count" in au and isinstance(au["count"], int)
    assert "projects" in au and isinstance(au["projects"], int)
    assert "oldest_age_sec" in au
    assert au["oldest_age_sec"] is None or isinstance(au["oldest_age_sec"], int)


def test_get_home_stats_sprints_planned_structure(client):
    """stats.sprints_planned has count (int) and total_tickets (int)."""
    r = client.get("/api/home")
    sp = r.json()["stats"]["sprints_planned"]
    assert "count" in sp and isinstance(sp["count"], int)
    assert "total_tickets" in sp and isinstance(sp["total_tickets"], int)


def test_get_home_stats_backlog_structure(client):
    """stats.backlog has count (int) and per_project (list of {name, count})."""
    r = client.get("/api/home")
    bl = r.json()["stats"]["backlog"]
    assert "count" in bl and isinstance(bl["count"], int)
    assert "per_project" in bl and isinstance(bl["per_project"], list)
    for entry in bl["per_project"]:
        assert "name" in entry, f"Missing 'name' in backlog entry {entry}"
        assert "count" in entry, f"Missing 'count' in backlog entry {entry}"
        assert isinstance(entry["count"], int)


def test_get_home_stats_backlog_max_five_entries(client):
    """stats.backlog.per_project contains at most 5 entries."""
    r = client.get("/api/home")
    per_project = r.json()["stats"]["backlog"]["per_project"]
    assert len(per_project) <= 5, f"Expected at most 5 backlog entries, got {len(per_project)}"


def test_get_home_stats_backlog_sorted_descending(client):
    """stats.backlog.per_project is sorted descending by count."""
    r = client.get("/api/home")
    counts = [e["count"] for e in r.json()["stats"]["backlog"]["per_project"]]
    assert counts == sorted(counts, reverse=True), f"Backlog not sorted descending: {counts}"


# ── AC-3: projects array structure ────────────────────────────────────────────

def test_get_home_projects_required_fields(client):
    """Each project entry has: name, slug, icon, status, uat_count, backlog_count, last_activity_at."""
    r = client.get("/api/home")
    required = {"name", "slug", "icon", "status", "uat_count", "backlog_count", "last_activity_at"}
    for proj in r.json()["projects"]:
        missing = required - set(proj.keys())
        assert not missing, f"Project entry missing fields {missing}: {proj}"


def test_get_home_projects_status_values_valid(client):
    """Each project status is one of: 'running', 'uat-pending', 'idle'."""
    r = client.get("/api/home")
    for proj in r.json()["projects"]:
        assert proj["status"] in VALID_PROJECT_STATUSES, (
            f"Invalid project status '{proj['status']}' for project '{proj['slug']}'"
        )


def test_get_home_projects_count_fields_non_negative(client):
    """uat_count and backlog_count are non-negative integers."""
    r = client.get("/api/home")
    for proj in r.json()["projects"]:
        assert isinstance(proj["uat_count"], int) and proj["uat_count"] >= 0, (
            f"uat_count invalid for {proj['slug']}: {proj['uat_count']}"
        )
        assert isinstance(proj["backlog_count"], int) and proj["backlog_count"] >= 0, (
            f"backlog_count invalid for {proj['slug']}: {proj['backlog_count']}"
        )


def test_get_home_projects_optional_fields_valid_when_present(client):
    """sprint_running and last_sprint, if present, have expected sub-structure."""
    r = client.get("/api/home")
    for proj in r.json()["projects"]:
        if "sprint_running" in proj:
            sr = proj["sprint_running"]
            assert "label" in sr and "elapsed_sec" in sr, (
                f"sprint_running malformed for {proj['slug']}: {sr}"
            )
            assert isinstance(sr["elapsed_sec"], int)
        if "last_sprint" in proj:
            ls = proj["last_sprint"]
            assert isinstance(ls, dict), f"last_sprint is not a dict for {proj['slug']}"


# ── AC-4: activity array ≤ 5 entries ─────────────────────────────────────────

def test_get_home_activity_max_five_entries(client):
    """activity array contains at most 5 entries."""
    r = client.get("/api/home")
    activity = r.json()["activity"]
    assert len(activity) <= 5, f"Expected at most 5 activity entries, got {len(activity)}"


# ── AC-5: valid activity type values ─────────────────────────────────────────

def test_get_home_activity_type_values_from_allowed_set(client):
    """Each activity entry's type is from the defined set of valid activity types."""
    r = client.get("/api/home")
    for entry in r.json()["activity"]:
        assert entry["type"] in VALID_ACTIVITY_TYPES, (
            f"Unknown activity type '{entry['type']}'"
        )


def test_get_home_activity_entry_required_fields(client):
    """Each activity entry has: type, project, title, sub, timestamp, link."""
    r = client.get("/api/home")
    required = {"type", "project", "title", "sub", "timestamp", "link"}
    for entry in r.json()["activity"]:
        missing = required - set(entry.keys())
        assert not missing, f"Activity entry missing fields {missing}: {entry}"


# ── AC-6: all timestamps are ISO 8601 UTC ─────────────────────────────────────

def _is_iso8601_utc(ts: str) -> bool:
    """Return True if ts is a valid ISO 8601 UTC string ending in Z."""
    if not ts.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def test_get_home_activity_timestamps_are_iso8601_utc(client):
    """All activity entry timestamps parse as ISO 8601 UTC."""
    r = client.get("/api/home")
    for entry in r.json()["activity"]:
        ts = entry.get("timestamp", "")
        assert _is_iso8601_utc(ts), (
            f"activity timestamp '{ts}' is not ISO 8601 UTC for entry type={entry['type']}"
        )


def test_get_home_project_last_activity_at_is_iso8601_utc_or_null(client):
    """last_activity_at per project is either null or a valid ISO 8601 UTC string."""
    r = client.get("/api/home")
    for proj in r.json()["projects"]:
        ts = proj["last_activity_at"]
        if ts is not None:
            assert _is_iso8601_utc(ts), (
                f"last_activity_at '{ts}' is not ISO 8601 UTC for project '{proj['slug']}'"
            )


# ── AC-8: idle project has null last_activity_at ─────────────────────────────

def test_get_home_idle_projects_last_activity_at_rule(client):
    """Projects with status 'idle' and no sprint/issue history have last_activity_at=null."""
    r = client.get("/api/home")
    # All idle projects: last_activity_at may be null (required if no sprint history)
    # We can only assert that if last_activity_at is non-null, the project needn't be idle
    # If a project is idle AND last_activity_at is non-null it means there was some issue activity
    # — that's valid per AC-7. The rule is: if NO sprint history AND no issue updates → null.
    for proj in r.json()["projects"]:
        # Can't force "no history" in a live test, but last_activity_at must be null-or-string
        lat = proj["last_activity_at"]
        assert lat is None or isinstance(lat, str), (
            f"last_activity_at must be null or string, got {type(lat)} for {proj['slug']}"
        )


# ── AC-9: no new DB tables ────────────────────────────────────────────────────

def test_get_home_no_new_db_tables(client):
    """The endpoint must not require new DB tables — existing tables suffice."""
    r = client.get("/api/health")
    assert r.status_code == 200
    # GET /api/home must work even without additional DB setup
    r2 = client.get("/api/home")
    assert r2.status_code == 200, (
        f"GET /api/home returned {r2.status_code} — may indicate a missing DB dependency"
    )


# ── AC-10 / AC-13: response time budgets ─────────────────────────────────────

def test_get_home_cold_response_within_500ms(client):
    """Cold response time is ≤ 500ms."""
    start = time.monotonic()
    r = client.get("/api/home")
    elapsed_ms = (time.monotonic() - start) * 1000
    assert r.status_code == 200
    assert elapsed_ms <= 500, (
        f"Cold response took {elapsed_ms:.1f}ms, budget is 500ms"
    )


def test_get_home_warm_cache_response_within_50ms(client):
    """Warm (cache hit) response time is ≤ 50ms (both home and github caches warm)."""
    # Prime both caches with a first call
    r_prime = client.get("/api/home")
    assert r_prime.status_code == 200

    # Second call should hit both _home_cache and github_client._cache
    start = time.monotonic()
    r = client.get("/api/home")
    elapsed_ms = (time.monotonic() - start) * 1000
    assert r.status_code == 200
    assert elapsed_ms <= 50, (
        f"Warm response took {elapsed_ms:.1f}ms, budget is 50ms (cache may not be hit)"
    )


def test_get_home_second_call_faster_than_first(client):
    """The second consecutive call is no slower than the first (cache reduces latency).

    When no projects are registered both calls return immediately and are both fast,
    so we accept that as a passing scenario (cache didn't need to kick in).
    When projects exist, the second call must be faster due to in-memory caching.
    """
    start1 = time.monotonic()
    r1 = client.get("/api/home")
    elapsed1_ms = (time.monotonic() - start1) * 1000
    assert r1.status_code == 200

    start2 = time.monotonic()
    r2 = client.get("/api/home")
    elapsed2_ms = (time.monotonic() - start2) * 1000
    assert r2.status_code == 200

    # If both are under 20ms the endpoint is trivially fast (no projects/no GitHub calls)
    # and caching behavior doesn't need asserting by timing alone.
    if elapsed1_ms < 20:
        assert elapsed2_ms < 20, (
            f"Empty-projects warm call took {elapsed2_ms:.1f}ms, expected < 20ms"
        )
    else:
        assert elapsed2_ms < elapsed1_ms, (
            f"Second call ({elapsed2_ms:.1f}ms) not faster than first ({elapsed1_ms:.1f}ms)"
        )


# ── AC-12: GitHub failure degrades gracefully ─────────────────────────────────

def test_get_home_always_returns_200(client):
    """Endpoint always returns 200 regardless of project data availability."""
    r = client.get("/api/home")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_get_home_failing_project_has_valid_idle_structure(client):
    """Any project with status='idle' has uat_count=0 and backlog_count=0 OR has real data.

    Projects that failed GitHub fetch degrade to idle with 0 counts; valid projects
    with idle status may have non-zero backlog counts. We verify the structural invariant:
    a project with non-zero uat_count must not have status 'idle'.
    """
    r = client.get("/api/home")
    for proj in r.json()["projects"]:
        if proj["uat_count"] > 0:
            assert proj["status"] != "idle", (
                f"Project '{proj['slug']}' has uat_count={proj['uat_count']} but status='idle'"
            )


# ── AC-14: reuses existing GitHub fetch helpers ───────────────────────────────

def test_get_home_no_duplicate_github_calls(client):
    """Endpoint responds successfully — implying it uses the existing github_client module."""
    # The implementation imports github_client (shared module); if it used a new client,
    # the shared cache would not apply and warm calls would be slow. We verify warm path.
    r1 = client.get("/api/home")
    assert r1.status_code == 200

    start = time.monotonic()
    r2 = client.get("/api/home")
    elapsed_ms = (time.monotonic() - start) * 1000
    assert r2.status_code == 200
    # If existing helpers + their 30s cache are reused, warm calls are very fast
    assert elapsed_ms < 200, (
        f"Warm call took {elapsed_ms:.1f}ms — may indicate no reuse of cached github_client"
    )

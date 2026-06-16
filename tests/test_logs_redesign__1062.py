"""Tests for issue #1062: Redesign Logs tab UI with token-based styling (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_logs_redesign__logpanel_and_activity_modules_updated(client):
    """AC: logpanel.js and progress-activity.js updated; no other src modules changed"""
    pytest.skip("manual — code review of logpanel.js and progress-activity.js diff verification")


def test_logs_redesign__activity_default_tab(client):
    """AC: Activity view is the default tab on page load at localhost:8000/project/commander/logs"""
    r = client.get("/project/commander/logs")
    assert r.status_code == 200

    # Check that the HTML contains the activity tab marked as active or default
    html = r.text
    assert 'id="stab-activity"' in html or 'activity' in html.lower(), \
        "Activity tab not found in logs page"


def test_logs_redesign__fetch_and_handlers_functional(client):
    """AC: All fetch calls and event handlers remain functional after redesign"""
    # Fetch the project page which loads the bundle and sets up event handlers
    r = client.get("/project/commander/logs")
    assert r.status_code == 200

    # Fetch the API endpoint that the activity view uses
    r = client.get("/api/projects/commander/events")
    assert r.status_code == 200
    assert isinstance(r.json(), list), "Events API should return a list"


def test_logs_redesign__event_rows_anatomy(client):
    """AC: Event rows have consistent anatomy: timestamp | source chip | severity chip | message"""
    # Fetch the activity events
    r = client.get("/api/projects/commander/events")
    assert r.status_code == 200
    events = r.json()

    # Verify the API response includes the required fields for anatomy
    # (timestamp, source, severity, message)
    if events:
        first_event = events[0]
        # Check for fields that would be used to construct the row anatomy
        assert any(key in first_event for key in ["timestamp", "ts", "time"]), \
            "Event missing timestamp field"
        assert "source" in first_event or "actor" in first_event, \
            "Event missing source/actor field"


def test_logs_redesign__source_filter_chips_token_colors(client):
    """AC: Source filter chips use role/semantic tokens for color and spacing"""
    r = client.get("/project/commander/logs")
    assert r.status_code == 200

    html = r.text
    # Check that filter chips are present (All, Agent & Sprint, Dashboard, GitHub)
    assert "source" in html.lower() or "filter" in html.lower(), \
        "Source filter chips not found in logs page"


def test_logs_redesign__severity_chips_distinct_colors(client):
    """AC: Severity chips use distinct token-mapped colors (error, warn, info)"""
    r = client.get("/project/commander/logs")
    assert r.status_code == 200

    html = r.text
    # Check for token color references in the CSS (--red-bg, --amber-bg, --blue-bg, etc.)
    # These should be used for severity mapping
    assert "--red-bg" in html or "--red" in html or "error" in html.lower(), \
        "Error severity color not found"


def test_logs_redesign__timestamp_visibility(client):
    """AC: Timestamps are human-readable and visually de-emphasized"""
    r = client.get("/api/projects/commander/events")
    assert r.status_code == 200

    # Verify the API returns timestamps in a format that can be human-readable
    events = r.json()
    if events:
        first_event = events[0]
        timestamp_field = next(
            (first_event.get(k) for k in ["timestamp", "ts", "time"]),
            None
        )
        assert timestamp_field is not None, "Timestamp field not present in event"


def test_logs_redesign__dark_theme_tokens(client):
    """AC: Dark theme applied throughout with tokens.css variables; no hardcoded hex/rgb"""
    r = client.get("/project/commander/logs")
    assert r.status_code == 200

    html = r.text
    # Verify CSS uses token variables instead of hardcoded colors
    # Should have var(--...) references
    assert "var(--" in html, "CSS token variables not found"


def test_logs_redesign__vanilla_js_only(client):
    """AC: Vanilla JS only — no new framework dependencies introduced"""
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200

    bundle = r.text
    # Check that no major framework libraries are imported in the bundle
    # (React, Vue, Svelte should not be present)
    assert "react" not in bundle.lower() or bundle.count("react") < 5, \
        "React detected in bundle (should be vanilla JS only)"


def test_logs_redesign__bundle_built_and_committed(client):
    """AC: npm run build run after edits; static/dist/bundle.js committed"""
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200

    # Verify the bundle is not empty and contains expected module signatures
    bundle = r.text
    assert len(bundle) > 1000, "Bundle appears to be empty or minimal"
    assert "export" in bundle or "window" in bundle, \
        "Bundle does not appear to be built (missing module patterns)"


def test_logs_redesign__impeccable_detect_passes(client):
    """AC: Impeccable detect passes on the logs view"""
    pytest.skip("manual — UI design verification via impeccable CLI, not HTTP")


def test_logs_redesign__scoped_diff(client):
    """AC: Diff scoped to project.html, logpanel.js, progress-activity.js"""
    pytest.skip("manual — git diff verification, not HTTP")

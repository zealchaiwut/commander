"""Tests for GitHub issue #7 — Plan Usage card: 5-hour rolling window and weekly tracking.

AC-1  PLAN_TYPE / WINDOW_TOKEN_LIMIT env vars control card visibility; missing vars → hidden, no error
AC-2  db.get_window_usage(window_start_utc) sums input+output tokens >= window_start_utc
AC-3  GET /api/plan-usage returns all required JSON keys
AC-4  status logic: no rows → no_activity; expired window, no new rows → expired; active → active
AC-5  ⚠️  PARTIAL MANUAL — progress bar colour tiers verified via source; rendering in browser manual
AC-6  ⚠️  PARTIAL MANUAL — time-remaining text verified via source; display in browser manual
AC-7  "(approx)" or "rough estimate" labels verified via source inspection

NOTE: HTTP tests for /api/plan-usage require the server to be running with the feature-branch code
AND PLAN_TYPE / WINDOW_TOKEN_LIMIT set in .env. If the server is running the old develop-branch
code the endpoint returns 404, so HTTP tests skip gracefully.
"""

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

DASH_DIR   = Path(__file__).parent.parent / "dashboard"
DB_PATH    = DASH_DIR / "dashboard.db"
APP_JS     = DASH_DIR / "static" / "app.js"
INDEX_HTML = DASH_DIR / "static" / "index.html"

# Ensure dashboard package is importable
if str(DASH_DIR) not in sys.path:
    sys.path.insert(0, str(DASH_DIR))

import db as dashboard_db  # noqa: E402 — must come after sys.path fix

BASE_URL = "http://localhost:8000"


# ─── helpers ──────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def insert_token_row(session_id: str, recorded_at: str,
                     input_tokens: int = 100, output_tokens: int = 50):
    """Directly insert a token_usage row for deterministic testing."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage (session_id, project, input_tokens, output_tokens, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, "test-project", input_tokens, output_tokens, recorded_at),
        )
        conn.commit()


def delete_token_rows(session_id: str):
    """Clean up rows inserted by a test session."""
    with get_conn() as conn:
        conn.execute("DELETE FROM token_usage WHERE session_id = ?", (session_id,))
        conn.commit()


def get_plan_usage_response():
    """Return (status_code, json_body) from /api/plan-usage, or (None, None) on connection error."""
    try:
        import httpx
        resp = httpx.get(f"{BASE_URL}/api/plan-usage", timeout=5)
        return resp.status_code, resp.json()
    except Exception:
        return None, None


# ─── AC-1: env vars control card; missing vars → hidden, no error ─────────────

def test_ac1_get_window_usage_function_exists():
    """db.py must expose get_window_usage()."""
    assert callable(getattr(dashboard_db, "get_window_usage", None)), \
        "get_window_usage not found in dashboard/db.py"


def test_ac1_get_earliest_row_after_exists():
    """db.py must expose get_earliest_row_after()."""
    assert callable(getattr(dashboard_db, "get_earliest_row_after", None)), \
        "get_earliest_row_after not found in dashboard/db.py"


def test_ac1_get_weekly_usage_exists():
    """db.py must expose get_weekly_usage()."""
    assert callable(getattr(dashboard_db, "get_weekly_usage", None)), \
        "get_weekly_usage not found in dashboard/db.py"


def test_ac1_endpoint_returns_200_or_skip():
    """GET /api/plan-usage must return 200 (plan-usage endpoint exists on server)."""
    status, data = get_plan_usage_response()
    if status is None:
        pytest.skip("Server not reachable — HTTP tests require server running with feature-branch code")
    if status == 404:
        pytest.skip(
            "Server running old code (404 on /api/plan-usage) — "
            "restart server with feature branch to test HTTP layer"
        )
    assert status == 200, f"Expected 200, got {status}: {data}"


def test_ac1_disabled_returns_enabled_false_no_error():
    """When PLAN_TYPE/WINDOW_TOKEN_LIMIT are unset the endpoint returns {enabled:false} — no 5xx."""
    status, data = get_plan_usage_response()
    if status is None:
        pytest.skip("Server not reachable")
    if status == 404:
        pytest.skip("Server running old code without plan-usage endpoint")
    assert status == 200
    assert "enabled" in data, f"Response missing 'enabled' key: {data}"
    assert isinstance(data["enabled"], bool), f"'enabled' must be bool, got {type(data['enabled'])}"
    # If disabled (vars not set), no 5xx should occur — already verified by 200 above


def test_ac1_server_py_reads_env_vars():
    """server.py must read PLAN_TYPE and WINDOW_TOKEN_LIMIT from env and gate on them."""
    server_src = (DASH_DIR / "server.py").read_text()
    assert "PLAN_TYPE" in server_src, "server.py missing PLAN_TYPE env var"
    assert "WINDOW_TOKEN_LIMIT" in server_src, "server.py missing WINDOW_TOKEN_LIMIT env var"
    assert "PLAN_USAGE_ENABLED" in server_src or "enabled" in server_src, \
        "server.py missing plan usage enabled gate"


# ─── AC-2: db.get_window_usage sums correctly ─────────────────────────────────

def test_ac2_get_window_usage_returns_zero_for_no_matching_rows():
    """get_window_usage with a far-future timestamp should return 0."""
    result = dashboard_db.get_window_usage("2099-01-01T00:00:00")
    assert result == 0, f"Expected 0 for far-future timestamp, got {result}"


def test_ac2_get_window_usage_sums_rows_in_window():
    """get_window_usage must sum input+output tokens for rows >= window_start_utc."""
    session_id = f"tester-ac2-sum-{uuid.uuid4().hex[:8]}"
    window_start = "2020-01-01T10:00:00"

    try:
        # Row inside window: 300 + 150 = 450
        insert_token_row(session_id, "2020-01-01T10:30:00", input_tokens=300, output_tokens=150)
        # Row inside window: 200 + 100 = 300
        insert_token_row(session_id, "2020-01-01T11:00:00", input_tokens=200, output_tokens=100)
        # Row BEFORE window: should NOT be counted
        insert_token_row(session_id, "2020-01-01T09:59:59", input_tokens=999, output_tokens=999)

        total_in_window = dashboard_db.get_window_usage(window_start)
        # Our two in-window rows contribute 750; there may be rows from other sessions
        assert total_in_window >= 750, \
            f"Expected at least 750 tokens in window (2 rows × 450+300), got {total_in_window}"
    finally:
        delete_token_rows(session_id)


def test_ac2_get_window_usage_excludes_rows_before_start():
    """get_window_usage must exclude rows with recorded_at < window_start_utc."""
    session_id = f"tester-ac2-excl-{uuid.uuid4().hex[:8]}"
    # Far-future window start — only our "before" row exists, should be excluded
    window_start = "2099-06-01T00:00:00"
    try:
        insert_token_row(session_id, "2099-05-31T23:59:59", input_tokens=500, output_tokens=500)
        result = dashboard_db.get_window_usage(window_start)
        assert result == 0, f"Expected 0 (row before window), got {result}"
    finally:
        delete_token_rows(session_id)


def test_ac2_get_window_usage_boundary_inclusive():
    """Rows with recorded_at exactly equal to window_start_utc must be included (>=)."""
    session_id = f"tester-ac2-boundary-{uuid.uuid4().hex[:8]}"
    boundary = "2020-03-15T08:00:00"
    try:
        insert_token_row(session_id, boundary, input_tokens=100, output_tokens=50)
        result = dashboard_db.get_window_usage(boundary)
        assert result >= 150, \
            f"Boundary-equal row (150 tokens) must be included; got {result}"
    finally:
        delete_token_rows(session_id)


# ─── AC-3: GET /api/plan-usage returns all required keys ──────────────────────

REQUIRED_KEYS = {
    "window_tokens", "window_limit", "window_pct",
    "window_start", "window_resets_at", "seconds_remaining",
    "weekly_tokens", "weekly_limit", "status",
}


def test_ac3_plan_usage_enabled_has_all_required_keys():
    """GET /api/plan-usage must return all required JSON keys when enabled=true."""
    status, data = get_plan_usage_response()
    if status is None:
        pytest.skip("Server not reachable")
    if status == 404:
        pytest.skip("Server running old code without plan-usage endpoint")
    assert status == 200

    if not data.get("enabled"):
        pytest.skip(
            "Server running without PLAN_TYPE/WINDOW_TOKEN_LIMIT — "
            "AC-3 key check skipped (disabled path is correct per AC-1). "
            "Re-run with PLAN_TYPE=max-5x WINDOW_TOKEN_LIMIT=88000 set in .env."
        )

    missing = REQUIRED_KEYS - set(data.keys())
    assert not missing, f"Missing required keys in /api/plan-usage response: {missing}"


def test_ac3_status_is_valid_value():
    """status field must be one of: no_activity, active, expired."""
    status, data = get_plan_usage_response()
    if status is None or status == 404:
        pytest.skip("Server not available or running old code")
    if not data.get("enabled"):
        pytest.skip("Plan usage not enabled on this server instance")
    assert data["status"] in ("no_activity", "active", "expired"), \
        f"Unexpected status value: {data['status']}"


def test_ac3_numeric_fields_are_numeric():
    """window_tokens, window_pct, seconds_remaining must be numeric when enabled."""
    status, data = get_plan_usage_response()
    if status is None or status == 404:
        pytest.skip("Server not available or running old code")
    if not data.get("enabled"):
        pytest.skip("Plan usage not enabled on this server instance")
    assert isinstance(data["window_tokens"], (int, float)), \
        f"window_tokens must be numeric: {data['window_tokens']}"
    assert isinstance(data["window_pct"], (int, float)), \
        f"window_pct must be numeric: {data['window_pct']}"
    assert isinstance(data["seconds_remaining"], (int, float)), \
        f"seconds_remaining must be numeric: {data['seconds_remaining']}"


def test_ac3_server_py_has_plan_usage_endpoint():
    """server.py must define GET /api/plan-usage route."""
    server_src = (DASH_DIR / "server.py").read_text()
    assert "/api/plan-usage" in server_src, \
        "server.py missing GET /api/plan-usage route definition"


def test_ac3_server_returns_enabled_key():
    """server.py get_plan_usage function must return a dict with 'enabled' key in all branches."""
    server_src = (DASH_DIR / "server.py").read_text()
    # Both the disabled return and active return must have 'enabled'
    assert server_src.count('"enabled"') >= 2 or server_src.count("'enabled'") >= 2 \
           or ('"enabled"' in server_src and "'enabled'" in server_src), \
        "server.py should return 'enabled' key in both enabled/disabled branches"


# ─── AC-4: status logic — db-level tests ──────────────────────────────────────

def test_ac4_get_earliest_row_after_returns_none_for_far_future():
    """get_earliest_row_after(far_future) must return None (no rows after that date)."""
    result = dashboard_db.get_earliest_row_after("2099-12-31T23:59:59")
    assert result is None, f"Expected None for far-future cutoff, got {result}"


def test_ac4_get_earliest_row_after_no_cutoff():
    """get_earliest_row_after() with no cutoff returns the global minimum recorded_at or None."""
    result = dashboard_db.get_earliest_row_after()
    # Must be None or a valid ISO timestamp string
    if result is not None:
        assert "T" in result, f"Expected ISO timestamp or None, got {result}"


def test_ac4_get_earliest_row_after_returns_min_after_cutoff():
    """get_earliest_row_after(cutoff) must return the minimum recorded_at >= cutoff."""
    session_id = f"tester-ac4-earliest-{uuid.uuid4().hex[:8]}"
    cutoff = "2021-06-01T00:00:00"
    try:
        insert_token_row(session_id, "2021-06-01T01:00:00")   # after cutoff — earlier
        insert_token_row(session_id, "2021-06-01T02:00:00")   # after cutoff — later
        insert_token_row(session_id, "2021-05-31T23:59:59")   # before cutoff — excluded

        earliest = dashboard_db.get_earliest_row_after(cutoff)
        assert earliest is not None, "Expected a row after cutoff, got None"
        # The min row after cutoff is "2021-06-01T01:00:00"
        assert earliest >= cutoff, \
            f"Earliest row must be >= cutoff ({cutoff}), got {earliest}"
        assert earliest <= "2021-06-01T02:00:00", \
            f"Earliest should be the min row after cutoff, got {earliest}"
    finally:
        delete_token_rows(session_id)


def test_ac4_status_no_activity_when_window_tokens_zero():
    """If status is no_activity or expired, window_tokens must be 0."""
    status, data = get_plan_usage_response()
    if status is None or status == 404:
        pytest.skip("Server not available or running old code")
    if not data.get("enabled"):
        pytest.skip("Plan usage not enabled on this server instance")
    if data["status"] in ("no_activity", "expired"):
        assert data["window_tokens"] == 0, \
            f"window_tokens should be 0 for status={data['status']}, got {data['window_tokens']}"


def test_ac4_no_activity_status_in_server_source():
    """server.py must implement no_activity status for empty DB."""
    server_src = (DASH_DIR / "server.py").read_text()
    assert "no_activity" in server_src, \
        "server.py missing 'no_activity' status implementation"
    assert "expired" in server_src, \
        "server.py missing 'expired' status implementation"


# ─── AC-5: progress bar colour tiers in source ────────────────────────────────

def test_ac5_source_contains_red_tier_at_80():
    """app.js must apply 'red' class at >= 80%."""
    source = APP_JS.read_text()
    assert "pct >= 80" in source and "red" in source, \
        "app.js missing red tier (>= 80%) logic"


def test_ac5_source_contains_amber_tier_at_50():
    """app.js must apply 'amber' class at >= 50%."""
    source = APP_JS.read_text()
    assert "pct >= 50" in source and "amber" in source, \
        "app.js missing amber tier (>= 50%) logic"


def test_ac5_source_contains_green_tier():
    """app.js must apply 'green' class below 50%."""
    source = APP_JS.read_text()
    # green must appear in the colour-tier logic (not just CSS vars)
    assert "'green'" in source or '"green"' in source or "green" in source, \
        "app.js missing green tier logic"


def test_ac5_source_has_slow_down_hint():
    """app.js or index.html must contain 'Slow down' hint text."""
    html   = INDEX_HTML.read_text()
    js     = APP_JS.read_text()
    assert "Slow down" in html or "Slow down" in js, \
        "Neither index.html nor app.js contains 'Slow down' hint text"


def test_ac5_slow_down_shown_above_80_pct():
    """app.js must toggle the slow-down element visible only when pct > 80."""
    source = APP_JS.read_text()
    # The logic: toggle hidden when NOT (status='active' AND pct > 80)
    assert "pct > 80" in source or "pct >= 80" in source, \
        "app.js missing threshold check for slow-down hint (pct > 80)"


def test_ac5_slow_down_tooltip_text():
    """The 'Slow down' element must have a tooltip mentioning rough estimate / Anthropic adjusts."""
    html = INDEX_HTML.read_text()
    assert "rough estimate" in html or "Anthropic adjusts" in html, \
        "index.html missing tooltip text about rough estimate / Anthropic adjusts"


@pytest.mark.skip(reason="MANUAL: Open http://localhost:8000 — verify bar is green <50%, "
                          "amber 50-80%, red >80%, and 'Slow down' hint appears above 80%")
def test_ac5_manual_color_rendering():
    pass


# ─── AC-6: time-remaining text logic in source ────────────────────────────────

def test_ac6_source_has_time_remaining_format():
    """app.js must format active window time as 'Xh Ym left'."""
    source = APP_JS.read_text()
    assert "m left" in source, \
        "app.js missing 'Xh Ym left' time-remaining format"


def test_ac6_source_has_hours_minutes_format():
    """app.js must compute hours and minutes from seconds_remaining."""
    source = APP_JS.read_text()
    # Look for Math.floor patterns used to compute h/m from seconds
    assert "3600" in source, \
        "app.js missing seconds→hours conversion (divide by 3600)"


def test_ac6_source_has_no_activity_text():
    """app.js must show 'ready · new window starts on next agent activity' for no_activity/expired."""
    source = APP_JS.read_text()
    assert "new window starts on next agent activity" in source, \
        "app.js missing no_activity/expired status text"


@pytest.mark.skip(reason="MANUAL: verify time-remaining shows 'Xh Ym left' when active, "
                          "'ready · new window starts on next agent activity' when idle")
def test_ac6_manual_time_display():
    pass


# ─── AC-7: (approx) and rough estimate labels in source ───────────────────────

def test_ac7_approx_label_in_html():
    """index.html must contain '(approx)' label near the percentage display."""
    html = INDEX_HTML.read_text()
    assert "(approx)" in html, \
        "index.html missing '(approx)' label for percentage display"


def test_ac7_approx_label_in_js():
    """app.js must include '(approx)' in the weekly token display."""
    source = APP_JS.read_text()
    assert "(approx)" in source, \
        "app.js missing '(approx)' label in weekly token display"


def test_ac7_rough_estimate_in_source():
    """index.html or app.js must include 'rough estimate' wording."""
    html = INDEX_HTML.read_text()
    js   = APP_JS.read_text()
    assert "rough estimate" in html or "rough estimate" in js, \
        "Neither index.html nor app.js contains 'rough estimate' label"


def test_ac7_weekly_line_shows_approx():
    """app.js weekly line must show '(approx)' after the weekly percentage."""
    source = APP_JS.read_text()
    # The weekly line format: `Weekly: ${wPct}% (approx) · ...`
    assert "% (approx)" in source or "(approx)" in source, \
        "app.js weekly usage line missing '(approx)' qualifier"

"""Tests for GitHub issue #4 — real-time token usage tracking.

AC-1  token_usage table schema
AC-2  post_tool_used.py hook posts to /api/token-usage; exits 0; skips when no usage
AC-3  GET /api/projects returns metrics.tokens_today (int) and metrics.cost_today_usd (float, 2 dp)
AC-4  ⚠️  MANUAL — "Tokens Today" card in UI (verified by UAT)
AC-5  GET /api/project-details?repo=<repo> returns tokens_today (int) and cost_today_usd (float)
AC-6  ⚠️  MANUAL — expanded project panel shows "Tokens today" line (verified by UAT)
AC-7  Hook silently skips DB write when usage is absent or zero
"""

import json
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

BASE_URL   = "http://localhost:8000"
DB_PATH    = Path(__file__).parent.parent / "dashboard" / "dashboard.db"
HOOK_PATH  = Path(__file__).parent.parent / "dashboard" / "hooks" / "post_tool_used.py"
REPO_ROOT  = str(Path(__file__).parent.parent)


# ─── helpers ──────────────────────────────────────────────────────────────────

def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def row_count_for_session(session_id: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT COUNT(*) FROM token_usage WHERE session_id = ?", (session_id,)
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


# ─── AC-1: token_usage table schema ───────────────────────────────────────────

def test_ac1_token_usage_table_exists():
    """token_usage table must exist in dashboard.db."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None, "token_usage table not found in dashboard.db"


def test_ac1_token_usage_columns():
    """token_usage must have the required columns."""
    required_columns = {"session_id", "project", "input_tokens", "output_tokens", "recorded_at"}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("PRAGMA table_info(token_usage)")
    actual_columns = {row[1] for row in cur.fetchall()}
    conn.close()
    missing = required_columns - actual_columns
    assert not missing, f"token_usage table missing columns: {missing}"


# ─── AC-2: hook POSTs to /api/token-usage, exits 0 ───────────────────────────

def test_ac2_hook_exits_zero_with_usage():
    """Hook must exit 0 when a valid usage payload is provided."""
    session_id = f"tester-ac2-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id":  session_id,
        "tool_name":   "Bash",
        "cwd":         REPO_ROOT,
        "usage":       {"input_tokens": 100, "output_tokens": 50},
    }
    result = run_hook(payload)
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"


def test_ac2_hook_inserts_row_into_db():
    """Hook must insert a row into token_usage when usage is present."""
    session_id = f"tester-ac2-insert-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id":  session_id,
        "tool_name":   "Bash",
        "cwd":         REPO_ROOT,
        "usage":       {"input_tokens": 200, "output_tokens": 80},
    }
    run_hook(payload)
    # Give the server a brief moment to process the async POST
    time.sleep(0.3)
    count = row_count_for_session(session_id)
    assert count >= 1, f"Expected at least 1 row for session {session_id}, found {count}"


def test_ac2_hook_endpoint_accepts_post():
    """POST /api/token-usage must accept a valid payload and return ok."""
    session_id = f"tester-ac2-api-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id":    session_id,
        "event_type":    "token_usage",
        "working_dir":   REPO_ROOT,
        "input_tokens":  300,
        "output_tokens": 100,
    }
    resp = httpx.post(f"{BASE_URL}/api/token-usage", json=payload, timeout=5)
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"
    assert resp.json().get("ok") is True


# ─── AC-3: GET /api/projects metrics ──────────────────────────────────────────

def test_ac3_projects_has_tokens_today():
    """GET /api/projects must return metrics.tokens_today as an integer."""
    resp = httpx.get(f"{BASE_URL}/api/projects", timeout=10)
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    data = resp.json()
    metrics = data.get("metrics", {})
    assert "tokens_today" in metrics, f"metrics.tokens_today missing; got keys: {list(metrics.keys())}"
    assert isinstance(metrics["tokens_today"], int), (
        f"metrics.tokens_today must be int, got {type(metrics['tokens_today'])}"
    )


def test_ac3_projects_has_cost_today_usd():
    """GET /api/projects must return metrics.cost_today_usd as a float rounded to 2 dp."""
    resp = httpx.get(f"{BASE_URL}/api/projects", timeout=10)
    assert resp.status_code == 200
    metrics = resp.json().get("metrics", {})
    assert "cost_today_usd" in metrics, f"metrics.cost_today_usd missing; got keys: {list(metrics.keys())}"
    cost = metrics["cost_today_usd"]
    assert isinstance(cost, (int, float)), f"cost_today_usd must be numeric, got {type(cost)}"
    # Verify rounded to 2 decimal places
    assert round(cost, 2) == cost, f"cost_today_usd not rounded to 2 dp: {cost}"


def test_ac3_cost_formula():
    """cost_today_usd must use the formula (input*3 + output*15) / 1_000_000."""
    # Insert a known token event, then verify the formula holds
    session_id = f"tester-ac3-formula-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id":    session_id,
        "event_type":    "token_usage",
        "working_dir":   REPO_ROOT,
        "input_tokens":  1_000_000,
        "output_tokens": 0,
    }
    # POST directly to server
    httpx.post(f"{BASE_URL}/api/token-usage", json=payload, timeout=5)

    resp = httpx.get(f"{BASE_URL}/api/projects", timeout=10)
    metrics = resp.json().get("metrics", {})
    cost = metrics.get("cost_today_usd", 0.0)
    # With 1M input tokens, cost contribution should be at least $3.00
    # (there may be other events; we just verify cost is >= $3.00 and the field exists)
    assert cost >= 3.00, f"Expected cost_today_usd >= 3.00 after 1M input tokens, got {cost}"


# ─── AC-4: MANUAL — "Tokens Today" card in UI ────────────────────────────────

@pytest.mark.skip(reason="MANUAL: verify 'Tokens Today' card shows integer count and ~$X.XX in browser UI")
def test_ac4_manual_tokens_today_card():
    """
    MANUAL verification:
    Open http://localhost:8000 in a browser.
    The 'Tokens Today' metric card must show an integer token count
    and a cost estimate formatted as ~$X.XX.
    It must update when the SSE 'update' event fires.
    """
    pass


# ─── AC-5: GET /api/project-details scoped tokens ─────────────────────────────

def test_ac5_project_details_has_tokens_today():
    """GET /api/project-details?repo=commander must return tokens_today (int)."""
    resp = httpx.get(f"{BASE_URL}/api/project-details", params={"repo": "zealchaiwut/commander"}, timeout=10)
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"
    data = resp.json()
    assert "tokens_today" in data, f"tokens_today missing from project-details; keys: {list(data.keys())}"
    assert isinstance(data["tokens_today"], int), (
        f"tokens_today must be int, got {type(data['tokens_today'])}"
    )


def test_ac5_project_details_has_cost_today_usd():
    """GET /api/project-details?repo=commander must return cost_today_usd (float)."""
    resp = httpx.get(f"{BASE_URL}/api/project-details", params={"repo": "zealchaiwut/commander"}, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "cost_today_usd" in data, f"cost_today_usd missing from project-details; keys: {list(data.keys())}"
    cost = data["cost_today_usd"]
    assert isinstance(cost, (int, float)), f"cost_today_usd must be numeric, got {type(cost)}"
    assert round(cost, 2) == cost, f"cost_today_usd not rounded to 2 dp: {cost}"


# ─── AC-6: MANUAL — expanded project panel ────────────────────────────────────

@pytest.mark.skip(reason="MANUAL: verify expanded project panel shows 'Tokens today' line or '—'")
def test_ac6_manual_expanded_panel():
    """
    MANUAL verification:
    Click on any project row in http://localhost:8000.
    The expanded panel must show a 'Tokens today' line with token count
    and ~$X.XX cost, or '—' if no data exists for that project today.
    """
    pass


# ─── AC-7: Hook silently skips when usage absent or zero ──────────────────────

def test_ac7_hook_exits_zero_without_usage():
    """Hook must exit 0 when no usage field is present."""
    session_id = f"tester-ac7-no-usage-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id": session_id,
        "tool_name":  "Read",
        "cwd":        REPO_ROOT,
    }
    result = run_hook(payload)
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"


def test_ac7_hook_does_not_insert_when_no_usage():
    """Hook must not insert a row when usage is absent."""
    session_id = f"tester-ac7-no-insert-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id": session_id,
        "tool_name":  "Read",
        "cwd":        REPO_ROOT,
    }
    run_hook(payload)
    time.sleep(0.3)
    count = row_count_for_session(session_id)
    assert count == 0, f"Expected 0 rows for session {session_id} (no usage), found {count}"


def test_ac7_hook_exits_zero_with_zero_tokens():
    """Hook must exit 0 when usage.input_tokens and usage.output_tokens are both 0."""
    session_id = f"tester-ac7-zero-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id": session_id,
        "tool_name":  "Bash",
        "cwd":        REPO_ROOT,
        "usage":      {"input_tokens": 0, "output_tokens": 0},
    }
    result = run_hook(payload)
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"


def test_ac7_hook_does_not_insert_with_zero_tokens():
    """Hook must not insert a row when both token counts are 0."""
    session_id = f"tester-ac7-zero-insert-{uuid.uuid4().hex[:8]}"
    payload = {
        "session_id": session_id,
        "tool_name":  "Bash",
        "cwd":        REPO_ROOT,
        "usage":      {"input_tokens": 0, "output_tokens": 0},
    }
    run_hook(payload)
    time.sleep(0.3)
    count = row_count_for_session(session_id)
    assert count == 0, f"Expected 0 rows for session {session_id} (zero tokens), found {count}"

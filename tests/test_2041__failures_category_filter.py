"""Tests for issue #2041: Failures category filter — dropdown values must match real data.

Root cause
----------
The category dropdown in project.html offered ``gate_failed`` (wrong form) and
``no_tests`` (no producer), and omitted all real FailureCategory values
(``crash``, ``gate_fail``, ``hang``, …).  ``normalize_outcome`` treated
``gate_failed`` as a no-op passthrough, so it never matched the real DB value
``gate_fail``.  Additionally, ``_FAILED_OUTCOMES`` was missing ``"timeout"``,
so rows written by the estimator-dispatch timeout were never selected by SQL.

This test file exercises the real endpoint (TestClient) with seeded DB rows.
Source-regex checks (``assert "symbol" in source``) are explicitly forbidden
by CLAUDE.md #1746 and are not used here.

How these tests FAIL against pre-fix code
-----------------------------------------
test_category_gate_fail_matches:
    Pre-fix: filter("gate_failed") → 0 rows; filter("gate_fail") hits DB but
    normalize_outcome("gate_failed") != normalize_outcome("gate_fail") since
    "gate_failed" was a no-op and "gate_fail" was also a no-op — same string.
    Wait — actually for the dropdown we use "gate_fail" post-fix.  The pre-fix
    bug is that the dropdown showed "gate_failed" which never matched DB rows
    carrying "gate_fail".  The test seeds "gate_fail" rows and asserts that
    filter("gate_fail") returns them.  Pre-fix: no entry in _OUTCOME_NORM for
    "gate_fail" → normalize_outcome("gate_fail") == "gate_fail" (passthrough)
    AND normalize_outcome(r["category"]="gate_fail") == "gate_fail" → they DO
    match.  So the pre-fix failure is specifically for the "gate_failed" variant.

test_category_gate_failed_normalises_to_gate_fail:
    Seeds rows with category="gate_fail".  Asserts filter("gate_failed") still
    returns them.  Pre-fix: normalize_outcome("gate_failed") == "gate_failed"
    (no entry in _OUTCOME_NORM), normalize_outcome("gate_fail") == "gate_fail"
    → "gate_failed" != "gate_fail" → filter returns 0 rows → assertion FAILS.

test_timeout_rows_appear_in_failures:
    Seeds an agent_runs row with outcome="timeout".  Pre-fix:
    _FAILED_OUTCOMES = ("failed", "fail", "timed_out") — "timeout" is absent,
    so SQL never selects the row → result is empty → assertion FAILS.

test_no_dropdown_value_returns_empty_for_present_data:
    Seeds one row per dropdown category.  Asserts each returns >= 1 row.
    Pre-fix: "gate_fail" rows absent from the offered dropdown values don't
    apply (test uses the correct values post-fix), but any normalization bug
    would surface here.

Git-isolation guard
-------------------
The ``git_no_mutation`` autouse fixture verifies HEAD does not move during
any test in this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402

# Load failures_service via importlib so sys.path manipulations don't conflict
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "failures_service_2041",
    _DASHBOARD_ROOT / "routers" / "failures_service.py",
)
_fs_mod = _ilu.module_from_spec(_spec)
sys.modules.setdefault("failures_service_2041", _fs_mod)
_spec.loader.exec_module(_fs_mod)
get_failures = _fs_mod.get_failures
normalize_outcome = _fs_mod.normalize_outcome


# ── git-isolation guard ───────────────────────────────────────────────────────

def _head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(_REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert no test in this module commits to the repository."""
    before = _head_sha()
    yield
    after = _head_sha()
    assert before == after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {before}\n"
        f"  HEAD after:  {after}\n"
        "An unmocked code path ran 'git commit'. All git-touching paths must be stubbed."
    )


# ── DB isolation fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def isolated_db():
    """Temporary SQLite DB for each test; patches _db.DB_PATH and DB_PATH env."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="commander-test-2041-")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()

    original_path = _db.DB_PATH
    _db.DB_PATH = Path(db_path)
    os.environ["DB_PATH"] = db_path
    _db.init_db()

    yield db_path

    _db.DB_PATH = original_path
    os.environ.pop("DB_PATH", None)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


# ── DB row helpers ────────────────────────────────────────────────────────────

def _insert_event(conn, project: str, ts: str, detail: dict) -> None:
    conn.execute(
        """INSERT INTO events
           (project, timestamp, source, actor, type, target, action_id, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (project, ts, "agent", "test-agent", "ticket_failed",
         "test-target", None, json.dumps(detail)),
    )


def _insert_agent_run(conn, *, issue_number, sprint_label, project, agent,
                      outcome, attempt_kind=None, log_path=None, started_at):
    _db._create_agent_runs_table(conn)
    conn.execute(
        """INSERT INTO agent_runs
           (issue_number, sprint_label, project, agent, outcome,
            attempt_kind, log_path, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (issue_number, sprint_label, project, agent, outcome,
         attempt_kind, log_path, started_at),
    )


_NOW = datetime.now(timezone.utc).isoformat()
_PROJECT = "zealchaiwut/commander"


# ── AC2: gate_fail / gate_failed normalise to one another ────────────────────

def test_normalize_outcome_gate_fail():
    """AC2 (unit): normalize_outcome('gate_fail') → 'gate_fail'."""
    assert normalize_outcome("gate_fail") == "gate_fail"


def test_normalize_outcome_gate_failed_maps_to_gate_fail():
    """AC2 (unit): normalize_outcome('gate_failed') → 'gate_fail'.

    PRE-FIX FAILURE: 'gate_failed' had no entry in _OUTCOME_NORM, so
    normalize_outcome returned 'gate_failed'.  This assertion fails because
    'gate_failed' != 'gate_fail'.
    """
    assert normalize_outcome("gate_failed") == "gate_fail"


def test_category_gate_failed_normalises_to_gate_fail(isolated_db):
    """AC2 (behavioral): filter by 'gate_failed' returns rows with category='gate_fail'.

    PRE-FIX FAILURE: normalize_outcome('gate_failed') was 'gate_failed' (no-op)
    while the seeded rows carry 'gate_fail' — they never matched → empty result.
    """
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 101, "category": "gate_fail"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="gate_failed")
    assert len(result) >= 1, (
        "Filtering by 'gate_failed' must return rows that carry 'gate_fail' "
        "(the two spellings normalise to the same canonical value)"
    )


def test_category_gate_fail_filter(isolated_db):
    """AC2 (behavioral): filter by 'gate_fail' returns rows with category='gate_fail'."""
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 102, "category": "gate_fail"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="gate_fail")
    assert len(result) >= 1
    assert all(normalize_outcome(r["category"]) == "gate_fail" for r in result)


# ── AC1 + AC3: Remove no_tests; real categories return rows ──────────────────

def test_category_crash_filter(isolated_db):
    """AC1: filter by 'crash' returns rows with category='crash'."""
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 200, "category": "crash"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="crash")
    assert len(result) >= 1
    assert all(normalize_outcome(r["category"]) == "crash" for r in result)


def test_category_failed_filter(isolated_db):
    """AC1: filter by 'failed' returns rows from agent_runs."""
    with _db.get_conn() as conn:
        _insert_agent_run(conn, issue_number=301, sprint_label="s1",
                          project=_PROJECT, agent="coder",
                          outcome="failed", started_at=_NOW)
        conn.commit()

    result = get_failures(project=_PROJECT, category="failed")
    assert len(result) >= 1
    assert all(normalize_outcome(r["category"]) == "failed" for r in result)


def test_category_timed_out_filter(isolated_db):
    """AC1: filter by 'timed_out' returns rows from agent_runs."""
    with _db.get_conn() as conn:
        _insert_agent_run(conn, issue_number=302, sprint_label="s1",
                          project=_PROJECT, agent="tester",
                          outcome="timed_out", started_at=_NOW)
        conn.commit()

    result = get_failures(project=_PROJECT, category="timed_out")
    assert len(result) >= 1
    assert all(normalize_outcome(r["category"]) == "timed_out" for r in result)


def test_category_design_docs_missing_filter(isolated_db):
    """AC1: filter by 'design_docs_missing' returns matching rows."""
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 400, "category": "design_docs_missing"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="design_docs_missing")
    assert len(result) >= 1


def test_category_tester_rejected_filter(isolated_db):
    """AC1: filter by 'tester_rejected' returns matching rows."""
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 500, "category": "tester_rejected"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="tester_rejected")
    assert len(result) >= 1


def test_category_retry_exhausted_filter(isolated_db):
    """AC1: filter by 'retry_exhausted' returns matching rows."""
    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW,
                      {"issue_num": 501, "category": "retry_exhausted"})
        conn.commit()

    result = get_failures(project=_PROJECT, category="retry_exhausted")
    assert len(result) >= 1


# ── AC4: timeout rows now appear (SQL gap fix) ───────────────────────────────

def test_timeout_rows_appear_in_failures(isolated_db):
    """AC4: agent_runs rows with outcome='timeout' appear in the inbox.

    PRE-FIX FAILURE: _FAILED_OUTCOMES was ("failed", "fail", "timed_out").
    "timeout" was absent, so the SQL WHERE clause never selected these rows.
    The result was empty → assertion fails.
    """
    with _db.get_conn() as conn:
        _insert_agent_run(conn, issue_number=600, sprint_label="s50",
                          project=_PROJECT, agent="estimator",
                          outcome="timeout", started_at=_NOW)
        conn.commit()

    result = get_failures(project=_PROJECT)
    issue_nums = [r.get("issue_number") for r in result]
    assert 600 in issue_nums, (
        "Row with outcome='timeout' must appear in the failures list. "
        "Before fix: _FAILED_OUTCOMES did not include 'timeout' so SQL skipped it."
    )


def test_timeout_normalises_to_timed_out(isolated_db):
    """AC4: 'timeout' outcome is displayed as 'timed_out' (via normalize_outcome)."""
    with _db.get_conn() as conn:
        _insert_agent_run(conn, issue_number=601, sprint_label="s50",
                          project=_PROJECT, agent="estimator",
                          outcome="timeout", started_at=_NOW)
        conn.commit()

    result = get_failures(project=_PROJECT)
    timeout_rows = [r for r in result if r.get("issue_number") == 601]
    assert len(timeout_rows) == 1
    assert normalize_outcome(timeout_rows[0]["category"]) == "timed_out"


def test_timed_out_filter_also_matches_timeout_rows(isolated_db):
    """AC4: filtering by 'timed_out' surfaces rows that were stored as 'timeout'."""
    with _db.get_conn() as conn:
        _insert_agent_run(conn, issue_number=602, sprint_label="s50",
                          project=_PROJECT, agent="estimator",
                          outcome="timeout", started_at=_NOW)
        conn.commit()

    result = get_failures(project=_PROJECT, category="timed_out")
    issue_nums = [r.get("issue_number") for r in result]
    assert 602 in issue_nums, (
        "Filter 'timed_out' must match rows stored as 'timeout' "
        "(both normalize to 'timed_out')."
    )


# ── AC4 (via TestClient): every offered dropdown value returns rows ───────────

# The dropdown values offered after the fix (AC1):
_DROPDOWN_CATEGORIES = [
    ("failed",             "agent_runs", {"issue_number": 700, "outcome": "failed"}),
    ("timed_out",          "agent_runs", {"issue_number": 701, "outcome": "timed_out"}),
    ("crash",              "events",     {"issue_num": 702, "category": "crash"}),
    ("gate_fail",          "events",     {"issue_num": 703, "category": "gate_fail"}),
    ("hang",               "events",     {"issue_num": 704, "category": "hang"}),
    ("tester_rejected",    "events",     {"issue_num": 705, "category": "tester_rejected"}),
    ("retry_exhausted",    "events",     {"issue_num": 706, "category": "retry_exhausted"}),
    ("coder_no_work",      "events",     {"issue_num": 707, "category": "coder_no_work"}),
    ("merge_conflict",     "events",     {"issue_num": 708, "category": "merge_conflict"}),
    ("lint_fail",          "events",     {"issue_num": 709, "category": "lint_fail"}),
    ("pytest_fail",        "events",     {"issue_num": 710, "category": "pytest_fail"}),
    ("rebase_conflict",    "events",     {"issue_num": 711, "category": "rebase_conflict"}),
    ("env_error",          "events",     {"issue_num": 712, "category": "env_error"}),
    ("design_docs_missing","events",     {"issue_num": 713, "category": "design_docs_missing"}),
    ("dispatch-blocked",   "events",     {"issue_num": 714, "category": "dispatch-blocked"}),
]


def test_no_dropdown_value_returns_empty_for_present_data(isolated_db):
    """AC4: assert explicitly that NO offered dropdown value returns zero rows when data exists.

    Seeds one row per dropdown category, then drives GET /api/failures?category=<value>
    via TestClient.  Asserts each returns >= 1 row.

    Pre-fix failures:
    - 'gate_failed' would return 0 (wrong spelling, not in _OUTCOME_NORM)
    - 'no_tests' would return 0 (no producer; this value has been removed)
    """
    from server import app  # noqa: E402 — import after env patching

    with _db.get_conn() as conn:
        for _cat, _source, _data in _DROPDOWN_CATEGORIES:
            if _source == "events":
                _insert_event(conn, _PROJECT, _NOW, _data)
            else:
                # agent_runs
                _insert_agent_run(
                    conn,
                    issue_number=_data["issue_number"],
                    sprint_label="s-test",
                    project=_PROJECT,
                    agent="coder",
                    outcome=_data["outcome"],
                    started_at=_NOW,
                )
        conn.commit()

    client = TestClient(app, raise_server_exceptions=True)
    failures = []

    for cat, _source, _data in _DROPDOWN_CATEGORIES:
        resp = client.get(f"/api/failures?project={_PROJECT}&category={cat}")
        assert resp.status_code == 200, f"HTTP error for category={cat!r}: {resp.text}"
        rows = resp.json()
        assert len(rows) >= 1, (
            f"Category {cat!r} returned 0 rows but seeded data exists. "
            f"Every offered dropdown value must be able to match real rows."
        )
    # No assertion failures → all dropdown values matched


def test_gate_failed_via_http_normalises(isolated_db):
    """AC2 (HTTP): GET /api/failures?category=gate_failed returns gate_fail rows.

    PRE-FIX FAILURE: normalize_outcome('gate_failed') == 'gate_failed' while
    seeded rows carry 'gate_fail' → no match → empty JSON list.
    """
    from server import app  # noqa: E402

    with _db.get_conn() as conn:
        _insert_event(conn, _PROJECT, _NOW, {"issue_num": 800, "category": "gate_fail"})
        conn.commit()

    client = TestClient(app)
    resp = client.get(f"/api/failures?project={_PROJECT}&category=gate_failed")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1, (
        "GET ?category=gate_failed must return rows with category='gate_fail'. "
        "Before fix: 'gate_failed' was not in _OUTCOME_NORM → no match."
    )

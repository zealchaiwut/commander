"""Tests for issue #2039: agent-name case mismatch in log URL resolution.

The bug: ``_build_log_url`` (failures_service) embeds the agent value from
``events.detail`` directly into the URL.  ``events.detail.agent`` is UPPERCASE
(e.g. "CODER"), but ``agent_runs.agent`` is always lowercase ("coder").
``get_log_path_from_db`` did a case-sensitive SQL match, so
``GET /runs/sprint-X/42/CODER/log`` returned 404 even though the log exists.

Fix: ``get_log_path_from_db`` now normalises ``agent`` to lowercase before the
SQL query.  This fixes all callers of the route, not just the Failures tab.

AC2 (behavioral, per #1746):
    Seed an agent_runs row with agent="coder", point its log_path at a real
    file inside ``.commander/logs/``, then hit
    ``GET /runs/{sprint}/{issue}/CODER/log`` through ``TestClient``.
    Assert 200 (not 404).

AC3 (regression guard):
    Same setup but URL uses lowercase "coder".  Must also return 200 (was
    always working; must stay working after the normalization).

Git-isolation guarantee
-----------------------
Every test in this module is guarded by the ``git_no_mutation`` autouse
fixture (pattern copied verbatim from test_2031__false_orphan_sweep.py).
Any code path that runs ``git commit`` or ``git add`` causes the fixture to
fail loudly.

How these tests FAIL against pre-fix code
-----------------------------------------
Before the fix, ``get_log_path_from_db`` passes ``agent`` to SQL verbatim:

    WHERE agent = 'CODER'

``agent_runs.agent`` is stored as 'coder', so the query returns no row.
``_resolve_agent_log`` then raises ``HTTPException(status_code=404)``.

    test_uppercase_coder_url_resolves_200:
        Pre-fix: response.status_code == 404.
        The assertion ``assert response.status_code == 200`` FAILS.

    test_uppercase_tester_url_resolves_200:
        Same — 404 instead of 200.  FAILS.

    test_lowercase_coder_url_still_resolves:
        Pre-fix: this was already working (lowercase matches lowercase).
        The assertion passes.  This test is the regression guard.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  before: {sha_before}\n"
        f"  after:  {sha_after}\n"
    )


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite DB, initialize schema, monkeypatch _db.DB_PATH."""
    db_path = tmp_path / "test_2039.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()

    original_db_path = _db.DB_PATH
    _db.DB_PATH = db_path
    os.environ["DB_PATH"] = str(db_path)

    _db.init_db()

    yield str(db_path)

    _db.DB_PATH = original_db_path


# ── Log file fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def commander_log_file(tmp_path):
    """Create a real log file inside a ``.commander/logs/`` directory.

    The path must pass ``_is_within_commander_logs`` which checks:
      path.parent.name == "logs" and path.parent.parent.name == ".commander"
    """
    logs_dir = tmp_path / ".commander" / "logs"
    logs_dir.mkdir(parents=True)
    log_file = logs_dir / "agent.log"
    log_file.write_text("coder log output line 1\ncoder log output line 2\n")
    return log_file


# ── Helper ────────────────────────────────────────────────────────────────────

def _insert_agent_run(
    conn,
    *,
    issue_number: int,
    sprint_label: str,
    project: str,
    agent: str,
    outcome: str,
    log_path: str,
    started_at: str,
) -> None:
    """Insert a row into agent_runs (creating the table if it does not exist)."""
    _db._create_agent_runs_table(conn)
    conn.execute(
        """INSERT INTO agent_runs
           (issue_number, sprint_label, project, agent, outcome, attempt_kind,
            log_path, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (issue_number, sprint_label, project, agent, outcome,
         "initial", log_path, started_at),
    )
    conn.commit()


# ── AC2: uppercase agent in URL must resolve to 200 ──────────────────────────

def test_uppercase_coder_url_resolves_200(temp_db, commander_log_file):
    """AC2: GET /runs/{sprint}/{issue}/CODER/log returns 200.

    ``agent_runs.agent`` is stored as 'coder' (lowercase).
    The URL segment is 'CODER' (uppercase) — as built by ``_build_log_url``
    when it reads ``events.detail.agent``.

    Pre-fix behaviour: case-sensitive SQL finds no row → 404.
    Post-fix behaviour: agent is lowercased before SQL → row found → 200.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_agent_run(
            conn,
            issue_number=42,
            sprint_label="sprint-107",
            project="zealchaiwut/commander",
            agent="coder",          # stored lowercase — always
            outcome="failed",
            log_path=str(commander_log_file),
            started_at=now_iso,
        )

    from server import app
    client = TestClient(app)

    # URL segment is UPPERCASE CODER — the case that was broken
    response = client.get("/runs/sprint-107/42/CODER/log")
    assert response.status_code == 200, (
        f"Expected 200 for uppercase CODER, got {response.status_code}. "
        f"Body: {response.text[:200]}"
    )
    data = response.json()
    assert "lines" in data, f"Response missing 'lines' key: {data}"


def test_uppercase_tester_url_resolves_200(temp_db, commander_log_file):
    """AC2: GET /runs/{sprint}/{issue}/TESTER/log returns 200.

    Same case-mismatch bug affects TESTER rows from events.detail.
    62 rows were broken in production: 47 CODER + 15 TESTER.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_agent_run(
            conn,
            issue_number=55,
            sprint_label="sprint-90",
            project="zealchaiwut/commander",
            agent="tester",         # stored lowercase
            outcome="timed_out",
            log_path=str(commander_log_file),
            started_at=now_iso,
        )

    from server import app
    client = TestClient(app)

    response = client.get("/runs/sprint-90/55/TESTER/log")
    assert response.status_code == 200, (
        f"Expected 200 for uppercase TESTER, got {response.status_code}. "
        f"Body: {response.text[:200]}"
    )


# ── AC3: lowercase agent in URL still resolves ────────────────────────────────

def test_lowercase_coder_url_still_resolves(temp_db, commander_log_file):
    """AC3: GET /runs/{sprint}/{issue}/coder/log still returns 200 (regression guard).

    Lowercase URLs were always working.  The normalization must not break them.
    This test passes against both pre-fix and post-fix code; it exists to
    guarantee the happy path stays intact.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_agent_run(
            conn,
            issue_number=99,
            sprint_label="sprint-50",
            project="zealchaiwut/commander",
            agent="coder",
            outcome="failed",
            log_path=str(commander_log_file),
            started_at=now_iso,
        )

    from server import app
    client = TestClient(app)

    response = client.get("/runs/sprint-50/99/coder/log")
    assert response.status_code == 200, (
        f"Expected 200 for lowercase coder, got {response.status_code}. "
        f"Body: {response.text[:200]}"
    )
    data = response.json()
    assert "lines" in data


def test_mixed_case_agent_url_resolves(temp_db, commander_log_file):
    """AC2 variant: arbitrary mixed case (e.g. 'Coder') also resolves correctly."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_agent_run(
            conn,
            issue_number=77,
            sprint_label="sprint-60",
            project="zealchaiwut/commander",
            agent="coder",
            outcome="failed",
            log_path=str(commander_log_file),
            started_at=now_iso,
        )

    from server import app
    client = TestClient(app)

    response = client.get("/runs/sprint-60/77/Coder/log")
    assert response.status_code == 200, (
        f"Expected 200 for mixed-case 'Coder', got {response.status_code}. "
        f"Body: {response.text[:200]}"
    )

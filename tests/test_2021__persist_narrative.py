"""Tests for issue #2021: Persist agent narrative and transcript path (runs against UAT).

Tests the behavioral implementation of persisting the agent run narrative tail
(final_message) and transcript path (transcript_path) in the agent_runs table.
These tests exercise real DB code paths — no source-regex or mocking.
"""
import os
import sys
import tempfile
import pathlib
import pytest
import sqlite3
import importlib

# Add apps/dashboard to path so we can import db module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "dashboard"))

import db


@pytest.fixture
def temp_db():
    """Create a temporary database for each test."""
    tmp_db = tempfile.mktemp(suffix=".db")
    os.environ["DB_PATH"] = tmp_db
    # Re-import db module to pick up the new DB_PATH global
    importlib.reload(db)
    db.init_db()
    yield tmp_db
    # Cleanup
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    if os.path.exists(tmp_db + "-wal"):
        os.remove(tmp_db + "-wal")
    if os.path.exists(tmp_db + "-shm"):
        os.remove(tmp_db + "-shm")


# ─────────────────────────────────────────────────────────────────────────────
# AC1: Schema verification — columns present after init and idempotent on re-init
# ─────────────────────────────────────────────────────────────────────────────

def test_2021__schema_has_final_message_and_transcript_path(temp_db):
    """AC1: After init_db(), PRAGMA table_info includes final_message and transcript_path."""
    with db.get_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}

    assert "final_message" in columns, "final_message column not found in agent_runs"
    assert "transcript_path" in columns, "transcript_path column not found in agent_runs"


def test_2021__schema_idempotent_on_reinit(temp_db):
    """AC1: Running init_db() a second time does not raise; columns still present."""
    # First init already ran via fixture. Run it again.
    db.init_db()

    # Verify columns are still there
    with db.get_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}

    assert "final_message" in columns
    assert "transcript_path" in columns


# ─────────────────────────────────────────────────────────────────────────────
# AC2: Narrative persistence — final_message populated from log tail
# ─────────────────────────────────────────────────────────────────────────────

def test_2021__final_message_populated_from_log_tail(temp_db):
    """AC2: Start + finish a run with a log_path → final_message contains the tail."""
    # Create a fake log file with known content
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_content = "First line.\nSecond line.\nFinal narrative here.\n"
    log_file.write_text(log_content)

    try:
        # Start and finish a run
        run_id = db.record_agent_start(
            issue_number=1,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file)
        )

        db.record_agent_finish(
            issue_number=1,
            sprint_label="sprint-test",
            agent="coder",
            outcome="passed",
            run_id=run_id
        )

        # Query the run
        runs = db.agent_runs_for_issue(1, "sprint-test")
        assert len(runs) == 1
        run = runs[0]

        # Verify final_message was populated
        assert run["final_message"] is not None, "final_message should not be None"
        assert "Final narrative here" in run["final_message"], \
            f"Expected 'Final narrative here' in final_message, got: {run['final_message']}"
    finally:
        if log_file.exists():
            log_file.unlink()


def test_2021__transcript_path_stored_when_provided(temp_db):
    """AC2: Pass transcript_path to record_agent_finish() → assert it's stored."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("Some log content.\n")
    transcript_path = "/tmp/session-2021.jsonl"

    try:
        run_id = db.record_agent_start(
            issue_number=2,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file)
        )

        db.record_agent_finish(
            issue_number=2,
            sprint_label="sprint-test",
            agent="coder",
            outcome="passed",
            run_id=run_id,
            transcript_path=transcript_path
        )

        runs = db.agent_runs_for_issue(2, "sprint-test")
        assert len(runs) == 1
        run = runs[0]

        assert run["transcript_path"] == transcript_path, \
            f"Expected transcript_path={transcript_path}, got {run['transcript_path']}"
    finally:
        if log_file.exists():
            log_file.unlink()


def test_2021__transcript_path_null_when_omitted(temp_db):
    """AC2: Omit transcript_path → assert it's NULL."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("Some log content.\n")

    try:
        run_id = db.record_agent_start(
            issue_number=3,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file)
        )

        # Note: transcript_path not provided
        db.record_agent_finish(
            issue_number=3,
            sprint_label="sprint-test",
            agent="coder",
            outcome="passed",
            run_id=run_id
        )

        runs = db.agent_runs_for_issue(3, "sprint-test")
        assert len(runs) == 1
        run = runs[0]

        assert run["transcript_path"] is None, \
            f"Expected transcript_path=None, got {run['transcript_path']}"
    finally:
        if log_file.exists():
            log_file.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# AC3: Resilience — missing/unreadable log does not raise; other columns written
# ─────────────────────────────────────────────────────────────────────────────

def test_2021__missing_log_path_does_not_raise(temp_db):
    """AC3: A run finished with a MISSING log_path does not raise."""
    run_id = db.record_agent_start(
        issue_number=4,
        sprint_label="sprint-test",
        agent="coder",
        log_path="/nonexistent/path/to/log.log"
    )

    # This should not raise
    db.record_agent_finish(
        issue_number=4,
        sprint_label="sprint-test",
        agent="coder",
        outcome="passed",
        run_id=run_id
    )

    # Verify the run was recorded and finished
    runs = db.agent_runs_for_issue(4, "sprint-test")
    assert len(runs) == 1
    run = runs[0]
    assert run["outcome"] == "passed"
    assert run["finished_at"] is not None


def test_2021__missing_log_leaves_final_message_null(temp_db):
    """AC3: Missing log_path leaves final_message as NULL; other finish columns still written."""
    run_id = db.record_agent_start(
        issue_number=5,
        sprint_label="sprint-test",
        agent="coder",
        log_path="/nonexistent/log.log"
    )

    db.record_agent_finish(
        issue_number=5,
        sprint_label="sprint-test",
        agent="coder",
        outcome="failed",
        run_id=run_id,
        total_tokens=1000
    )

    runs = db.agent_runs_for_issue(5, "sprint-test")
    assert len(runs) == 1
    run = runs[0]

    # final_message should be NULL
    assert run["final_message"] is None, \
        f"Expected final_message=None for missing log, got {run['final_message']}"

    # But other finish columns should be present
    assert run["outcome"] == "failed"
    assert run["total_tokens"] == 1000
    assert run["finished_at"] is not None


def test_2021__unreadable_log_leaves_final_message_null(temp_db):
    """AC3: Unreadable log (permission denied, etc.) leaves final_message NULL."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("Secret content.\n")

    try:
        run_id = db.record_agent_start(
            issue_number=6,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file)
        )

        # Make the log unreadable
        os.chmod(log_file, 0o000)

        db.record_agent_finish(
            issue_number=6,
            sprint_label="sprint-test",
            agent="coder",
            outcome="passed",
            run_id=run_id
        )

        runs = db.agent_runs_for_issue(6, "sprint-test")
        assert len(runs) == 1
        run = runs[0]

        # final_message should be None due to permission denied
        assert run["final_message"] is None, \
            f"Expected final_message=None for unreadable log, got {run['final_message']}"

        # But outcome should be recorded
        assert run["outcome"] == "passed"
    finally:
        # Restore permissions before cleanup
        os.chmod(log_file, 0o644)
        if log_file.exists():
            log_file.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Bounding: _read_log_tail returns bounded tail (~4 KB)
# ─────────────────────────────────────────────────────────────────────────────

def test_2021__read_log_tail_bounds_output(temp_db):
    """Bounding: Large log (>4 KB) → _read_log_tail returns bounded tail (~4 KB)."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))

    # Create a 100 KB log file
    lines = [f"Line {i}: " + ("x" * 50) for i in range(2000)]
    log_file.write_text("\n".join(lines) + "\n")

    try:
        # Call _read_log_tail directly
        tail = db._read_log_tail(str(log_file), max_bytes=4096)

        # Verify the tail is bounded
        assert tail is not None, "tail should not be None for non-empty log"
        assert len(tail.encode("utf-8")) <= 4096, \
            f"tail size {len(tail.encode('utf-8'))} exceeds 4096 bytes"

        # Verify it contains final lines (not the head)
        assert "Line 1999" in tail, "tail should contain final lines, not the head"
    finally:
        if log_file.exists():
            log_file.unlink()


def test_2021__read_log_tail_contains_final_lines(temp_db):
    """Bounding: _read_log_tail with large log contains final lines."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))

    # Create a 50 KB log with distinguishable lines
    lines = [f"Line {i:04d}" for i in range(1000)]
    log_file.write_text("\n".join(lines) + "\n")

    try:
        tail = db._read_log_tail(str(log_file), max_bytes=4096)

        assert tail is not None
        # The tail should contain lines from the end (e.g., Line 0999, 0998, etc.)
        # but NOT from the beginning (e.g., Line 0001)
        assert "Line 099" in tail, "tail should contain later lines"
        # The very first line should not be in the tail
        assert "Line 0001" not in tail or tail.count("Line 0001") == 0, \
            "tail should not contain lines from the very beginning"
    finally:
        if log_file.exists():
            log_file.unlink()


def test_2021__read_log_tail_empty_log_returns_none(temp_db):
    """Bounding: _read_log_tail on an empty log returns None."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("")

    try:
        tail = db._read_log_tail(str(log_file))
        assert tail is None, "tail should be None for empty log"
    finally:
        if log_file.exists():
            log_file.unlink()


def test_2021__read_log_tail_single_line_log(temp_db):
    """Bounding: _read_log_tail on a small single-line log returns it."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("Final narrative message.\n")

    try:
        tail = db._read_log_tail(str(log_file))
        assert tail == "Final narrative message."
    finally:
        if log_file.exists():
            log_file.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Integration: Full lifecycle with narrative persistence
# ─────────────────────────────────────────────────────────────────────────────

def test_2021__full_lifecycle_with_narrative_and_transcript(temp_db):
    """Integration: Full agent run lifecycle with both narrative and transcript path."""
    log_file = pathlib.Path(tempfile.mktemp(suffix=".log"))
    log_file.write_text("Starting agent...\nProcessing...\nAgent finished successfully.\n")
    transcript_path = "/tmp/agent-2021-session.jsonl"

    try:
        # Start the run
        run_id = db.record_agent_start(
            issue_number=100,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file),
            model_used="claude-sonnet-4-6"
        )

        assert run_id is not None, "record_agent_start should return a run_id"

        # Finish the run with both narrative and transcript
        db.record_agent_finish(
            issue_number=100,
            sprint_label="sprint-test",
            agent="coder",
            outcome="passed",
            total_tokens=5000,
            run_id=run_id,
            transcript_path=transcript_path
        )

        # Verify the full record
        runs = db.agent_runs_for_issue(100, "sprint-test")
        assert len(runs) == 1
        run = runs[0]

        assert run["id"] == run_id
        assert run["issue_number"] == 100
        assert run["sprint_label"] == "sprint-test"
        assert run["agent"] == "coder"
        assert run["outcome"] == "passed"
        assert run["total_tokens"] == 5000
        assert run["finished_at"] is not None
        assert run["final_message"] is not None
        assert "successfully" in run["final_message"]
        assert run["transcript_path"] == transcript_path
    finally:
        if log_file.exists():
            log_file.unlink()

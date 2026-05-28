"""Tests for services/logging.py — issue #325."""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_log(tmp_path, monkeypatch):
    """Return (log instance, log_path) scoped to a tmp .commander/logs dir."""
    import services.logging as logging_mod

    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)

    logger = logging_mod._Logger()
    today = __import__("datetime").date.today().isoformat()
    log_path = log_dir / f"structured-{today}.log"
    return logger, log_path


# ---------------------------------------------------------------------------
# (a) Record shape validation
# ---------------------------------------------------------------------------

REQUIRED_KEYS = (
    "ts", "level", "run_id", "source", "agent_role",
    "issue_num", "sprint_label", "project", "git_sha", "event", "message",
)


def test_import_no_side_effects(tmp_path, monkeypatch, capsys):
    """Importing services.logging produces no output and no log files."""
    import services.logging  # noqa: F401
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_record_contains_all_required_keys(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.info("test_event", "hello world")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    for key in REQUIRED_KEYS:
        assert key in record, f"Missing required key: {key}"


def test_missing_optional_fields_serialized_as_null(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.info("test_event", "msg")
    record = json.loads(log_path.read_text().strip())
    for key in ("run_id", "source", "agent_role", "issue_num", "sprint_label", "project"):
        assert key in record
        assert record[key] is None


def test_record_is_valid_json_terminated_with_newline(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.info("evt", "msg")
    raw = log_path.read_text()
    assert raw.endswith("\n")
    json.loads(raw.strip())  # must not raise


def test_ts_is_iso8601(tmp_path, monkeypatch):
    import datetime
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.info("ts_test", "checking timestamp")
    record = json.loads(log_path.read_text().strip())
    dt = datetime.datetime.fromisoformat(record["ts"])
    assert dt.tzinfo is not None


def test_convenience_helpers_set_correct_level(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "DEBUG")
    logger.info("e", "m")
    logger.warn("e", "m")
    logger.error("e", "m")
    logger.debug("e", "m")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 4
    levels = [json.loads(l)["level"] for l in lines]
    assert levels == ["INFO", "WARN", "ERROR", "DEBUG"]


# ---------------------------------------------------------------------------
# (b) Append behavior across multiple calls
# ---------------------------------------------------------------------------

def test_multiple_calls_append_not_overwrite(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    for i in range(5):
        logger.info(f"event_{i}", f"message {i}")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["event"] == f"event_{i}"


def test_log_file_created_if_not_exists(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    assert not log_path.exists()
    logger.info("first_event", "creating file")
    assert log_path.exists()


# ---------------------------------------------------------------------------
# (c) Flush-on-kill: process killed with SIGKILL still leaves record on disk
# ---------------------------------------------------------------------------

def test_flush_on_sigkill(tmp_path):
    """Record written just before SIGKILL must survive on disk."""
    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True)

    repo_root = str(Path(__file__).parent.parent)
    script = textwrap.dedent(f"""\
        import os, signal, sys
        sys.path.insert(0, {repo_root!r})
        os.chdir({str(tmp_path)!r})
        from services.logging import _Logger
        logger = _Logger()
        logger.info("exit_nonzero", "about to die")
        os.kill(os.getpid(), signal.SIGKILL)
    """)

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=10)

    import datetime
    today = datetime.date.today().isoformat()
    log_path = log_dir / f"structured-{today}.log"
    assert log_path.exists(), "Log file not created before SIGKILL"
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["event"] == "exit_nonzero"
    assert record["message"] == "about to die"


# ---------------------------------------------------------------------------
# (d) Level filtering
# ---------------------------------------------------------------------------

def test_debug_suppressed_when_level_is_info(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "INFO")
    logger.debug("probe", "debug message")
    assert not log_path.exists() or log_path.read_text().strip() == ""


def test_debug_written_when_level_is_debug(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "DEBUG")
    logger.debug("probe", "debug message")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["level"] == "DEBUG"


def test_warn_written_at_default_info_level(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "INFO")
    logger.warn("some_warn", "warning msg")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1


def test_info_suppressed_when_level_is_error(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "ERROR")
    logger.info("probe", "info message")
    logger.warn("probe", "warn message")
    assert not log_path.exists() or log_path.read_text().strip() == ""


def test_error_always_written(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    monkeypatch.setenv("COMMANDER_LOG_LEVEL", "ERROR")
    logger.error("critical_err", "something went wrong")
    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Context merging
# ---------------------------------------------------------------------------

def test_set_context_values_appear_in_records(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.set_context(project="my-project", sprint_label="sprint-12", issue_num=170)
    logger.warn("hang_detected", "agent stalled")
    record = json.loads(log_path.read_text().strip())
    assert record["project"] == "my-project"
    assert record["sprint_label"] == "sprint-12"
    assert record["issue_num"] == 170


def test_context_does_not_require_caller_to_repeat_fields(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.set_context(run_id="sprint12-20260529-a3f", issue_num=170)
    logger.info("dispatch_start", "started coder")
    record = json.loads(log_path.read_text().strip())
    assert record["run_id"] == "sprint12-20260529-a3f"
    assert record["issue_num"] == 170


def test_per_call_fields_override_context(tmp_path, monkeypatch):
    logger, log_path = _fresh_log(tmp_path, monkeypatch)
    logger.set_context(agent_role="coder")
    logger.info("override_test", "msg", agent_role="tester")
    record = json.loads(log_path.read_text().strip())
    assert record["agent_role"] == "tester"

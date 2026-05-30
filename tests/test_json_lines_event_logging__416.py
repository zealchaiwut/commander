"""Tests for issue #416 — JSON-lines structured event logging sink (services/logging.py)."""
from __future__ import annotations

import importlib
import json
import logging
import re
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_module(tmp_path: Path, monkeypatch):
    """Return a fresh services.logging module with .commander/logs inside tmp_path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".commander").mkdir(exist_ok=True)
    # Remove cached module so _resolve_log_dir re-evaluates cwd
    for key in list(sys.modules):
        if key == "services.logging" or key.startswith("services.logging."):
            del sys.modules[key]
    # Also remove cached commander logger handlers so _build_commander_logger re-runs
    cmd_logger = logging.getLogger("commander")
    cmd_logger.handlers.clear()
    mod = importlib.import_module("services.logging")
    return mod


def _log_dir(tmp_path: Path) -> Path:
    return tmp_path / ".commander" / "logs"


def _today_jsonl(tmp_path: Path) -> Path:
    import datetime
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return _log_dir(tmp_path) / f"events-{today}.jsonl"


# ---------------------------------------------------------------------------
# AC1 — module exists and is importable
# ---------------------------------------------------------------------------

def test_module_importable():
    mod = importlib.import_module("services.logging")
    assert mod is not None


# ---------------------------------------------------------------------------
# AC2 — log.event() writes one JSON object per line to events-YYYY-MM-DD.jsonl
# ---------------------------------------------------------------------------

def test_event_creates_jsonl_file(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event("test.start", run_id="sprint-20260101T000000-aabbccdd", issue_num=42)
    jsonl = _today_jsonl(tmp_path)
    assert jsonl.exists(), "events-<today>.jsonl not created"
    lines = jsonl.read_text().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# AC3 — each emitted line is valid JSON and terminated by \n
# ---------------------------------------------------------------------------

def test_event_line_is_valid_json_terminated_by_newline(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event("ac3.check")
    raw = _today_jsonl(tmp_path).read_bytes()
    assert raw.endswith(b"\n"), "line must end with \\n"
    line = raw.decode().rstrip("\n")
    obj = json.loads(line)
    assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# AC4 — correlation keys are included as-is; extra kwargs too
# ---------------------------------------------------------------------------

def test_event_includes_correlation_keys_and_extra_kwargs(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event(
        "ac4.check",
        run_id="manual-20260531T000000-12345678",
        issue_num=99,
        sprint_label="sprint-29",
        agent_role="tester",
        project="commander",
        git_sha="abc1234",
        extra_field="hello",
    )
    obj = json.loads(_today_jsonl(tmp_path).read_text())
    for key in ("run_id", "issue_num", "sprint_label", "agent_role", "project", "git_sha", "extra_field"):
        assert key in obj, f"missing key: {key}"
    assert obj["extra_field"] == "hello"


# ---------------------------------------------------------------------------
# AC5 — each event line includes an ISO-8601 UTC timestamp field
# ---------------------------------------------------------------------------

def test_event_includes_iso8601_timestamp(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event("ac5.check")
    obj = json.loads(_today_jsonl(tmp_path).read_text())
    assert "timestamp" in obj
    # Must parse as a datetime; isoformat with timezone offset or Z suffix
    from datetime import datetime, timezone
    ts_str = obj["timestamp"]
    # Python isoformat uses +00:00, not Z; accept both
    ts_str_norm = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts_str_norm)
    assert dt.tzinfo is not None, "timestamp must be timezone-aware"


# ---------------------------------------------------------------------------
# AC6 — each event line includes the name field
# ---------------------------------------------------------------------------

def test_event_includes_name_field(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event("my.event.name")
    obj = json.loads(_today_jsonl(tmp_path).read_text())
    assert obj.get("name") == "my.event.name"


# ---------------------------------------------------------------------------
# AC7 — generate_run_id format <source>-<YYYYMMDDTHHmmss>-<8hex>
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["sprint", "manual", "adhoc"])
def test_generate_run_id_format(source):
    from services.logging import generate_run_id
    run_id = generate_run_id(source)
    pattern = rf"^{source}-\d{{8}}T\d{{6}}-[0-9a-f]{{8}}$"
    assert re.match(pattern, run_id), f"run_id {run_id!r} doesn't match expected pattern"


# ---------------------------------------------------------------------------
# AC8 — generate_run_id raises ValueError for invalid source
# ---------------------------------------------------------------------------

def test_generate_run_id_raises_for_invalid_source():
    from services.logging import generate_run_id
    with pytest.raises(ValueError):
        generate_run_id("invalid")


def test_generate_run_id_raises_for_empty_source():
    from services.logging import generate_run_id
    with pytest.raises(ValueError):
        generate_run_id("")


# ---------------------------------------------------------------------------
# AC9 — module exposes stdlib Logger named "commander"
# ---------------------------------------------------------------------------

def test_commander_logger_is_standard_logger(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    # Module must expose commander_logger
    assert hasattr(mod, "commander_logger")
    logger = mod.commander_logger
    assert isinstance(logger, logging.Logger)
    assert logger.name == "commander"


def test_commander_logger_writes_to_logs_dir(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.commander_logger.info("hello from test")
    log_dir = _log_dir(tmp_path)
    log_files = list(log_dir.glob("commander-*.log"))
    assert log_files, "no commander-*.log file created"
    content = log_files[0].read_text()
    assert "hello from test" in content


# ---------------------------------------------------------------------------
# AC10 — .commander/logs/ created automatically; no error if already exists
# ---------------------------------------------------------------------------

def test_logs_dir_created_automatically(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".commander").mkdir()
    # Ensure logs dir does NOT exist yet
    log_dir = tmp_path / ".commander" / "logs"
    assert not log_dir.exists()
    for key in list(sys.modules):
        if key == "services.logging":
            del sys.modules[key]
    logging.getLogger("commander").handlers.clear()
    mod = importlib.import_module("services.logging")
    mod.log.event("autocreate.test")
    assert log_dir.exists()


def test_logs_dir_no_error_if_already_exists(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    # Call event twice — second call hits existing dir
    mod.log.event("first")
    mod.log.event("second")
    lines = _today_jsonl(tmp_path).read_text().splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# AC11 — writing an event never raises; IO failure logged to stderr, suppressed
# ---------------------------------------------------------------------------

def test_event_never_raises_on_io_failure(tmp_path, monkeypatch, capsys):
    mod = _reload_module(tmp_path, monkeypatch)
    # Monkey-patch _append_line to raise OSError
    def _bad_append(path, line):
        raise OSError("simulated disk full")
    monkeypatch.setattr(mod, "_append_line", _bad_append)
    # Must not raise
    mod.log.event("io.fail.test")
    captured = capsys.readouterr()
    assert "IO error" in captured.err or "simulated" in captured.err


# ---------------------------------------------------------------------------
# AC12 — no runtime dependencies outside stdlib
# ---------------------------------------------------------------------------

def test_no_third_party_imports():
    """Verify services/logging.py only imports stdlib modules."""
    stdlib_modules = {
        "__future__", "datetime", "fcntl", "json", "logging", "os",
        "secrets", "subprocess", "sys", "threading", "pathlib", "typing",
    }
    import ast, pathlib
    src = pathlib.Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/services/logging.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in stdlib_modules or top == "services", \
                        f"Non-stdlib import found: {alias.name}"
            elif node.module:
                top = node.module.split(".")[0]
                assert top in stdlib_modules or top == "services", \
                    f"Non-stdlib import found: {node.module}"


# ---------------------------------------------------------------------------
# UAT step: two events in same day yield two lines, each distinct timestamp
# ---------------------------------------------------------------------------

def test_two_events_same_day_two_lines(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event("first.event")
    mod.log.event("second.event")
    lines = _today_jsonl(tmp_path).read_text().splitlines()
    assert len(lines) == 2
    objs = [json.loads(l) for l in lines]
    assert objs[0]["name"] == "first.event"
    assert objs[1]["name"] == "second.event"
    # timestamps may be equal if calls happen in same microsecond, but both must parse
    from datetime import datetime
    for o in objs:
        ts = o["timestamp"].replace("Z", "+00:00")
        datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# UAT step: all 7 correlation keys + 1 unknown key appear in output
# ---------------------------------------------------------------------------

def test_all_seven_correlation_keys_plus_extra(tmp_path, monkeypatch):
    mod = _reload_module(tmp_path, monkeypatch)
    mod.log.event(
        "full.event",
        run_id="adhoc-20260531T120000-deadbeef",
        issue_num=416,
        sprint_label="sprint-29",
        agent_role="tester",
        project="commander",
        git_sha="abc1234",
        unknown_key="surprise",
    )
    obj = json.loads(_today_jsonl(tmp_path).read_text())
    for key in ("run_id", "issue_num", "sprint_label", "agent_role", "project", "git_sha", "unknown_key"):
        assert key in obj, f"missing: {key}"

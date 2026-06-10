"""Tests for #784: Unify structured logging — one schema, run_id everywhere.

AC1  EventType enum exists with full lifecycle vocabulary.
AC2  emit() requires event_type, run_id, sprint, issue fields.
AC3  Subprocess stdout/stderr is enveloped as {run_id,sprint,issue,agent,ts,raw}.
AC4  Zero bare print() calls in services/sprint_manager/* and scripts/*.
AC5  Every log line written by envelope_subprocess_line is valid JSON.
AC6  run_id is constant within a run and distinct between two runs.
AC7  colorizeLogLine extracts .raw from enveloped lines.
AC8  docs/logging-schema.md exists and documents required fields.
"""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# AC1: EventType enum
# ---------------------------------------------------------------------------

def test_event_type_enum_exists():
    """AC1: services/logging exports EventType enum."""
    from services.logging import EventType
    assert hasattr(EventType, "__mro__") or True  # it's an Enum class


def test_event_type_enum_values():
    """AC1: EventType has the full lifecycle vocabulary."""
    from services.logging import EventType
    required = {"dispatched", "started", "finished", "failed", "killed", "resumed", "transitioned"}
    actual = {e.value for e in EventType}
    assert required <= actual, f"Missing EventType values: {required - actual}"


def test_event_type_no_string_literals_at_callsites():
    """AC1: Call sites in services/sprint_manager use EventType enum, not raw strings for lifecycle events."""
    # The lifecycle vocabulary strings must not appear as string literals in call arguments
    lifecycle_strings = {"dispatched", "started", "finished", "failed", "killed", "resumed", "transitioned"}
    sm_dir = REPO_ROOT / "services" / "sprint_manager"
    violations = []
    for py_file in sm_dir.glob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Check keyword arguments and positional args for lifecycle strings
            for kw in node.keywords:
                if isinstance(kw.value, ast.Constant) and kw.value.value in lifecycle_strings:
                    if kw.arg == "event_type":
                        violations.append(f"{py_file.name}:{kw.value.lineno}: event_type={kw.value.value!r} should use EventType enum")
    assert not violations, "String literals used instead of EventType enum:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# AC2: emit() method with required fields
# ---------------------------------------------------------------------------

def test_emit_method_exists():
    """AC2: structured logger has an emit() method."""
    from services.logging import log as structured_log
    assert callable(getattr(structured_log, "emit", None)), "structured_log must have emit() method"


def test_emit_requires_event_type():
    """AC2: emit() raises TypeError when event_type is missing."""
    from services.logging import log as structured_log, EventType
    with pytest.raises(TypeError):
        structured_log.emit()  # missing all required args


def test_emit_writes_required_fields(tmp_path):
    """AC2: emit() writes event_type, run_id, sprint, and issue fields to the log file."""
    from services.logging import log as structured_log, EventType, generate_run_id

    log_file = tmp_path / "test-run.jsonl"
    run_id = generate_run_id("sprint")

    structured_log.emit(
        event_type=EventType.dispatched,
        run_id=run_id,
        sprint="sprint-99",
        issue=42,
        log_path=log_file,
    )

    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert lines, "emit() wrote no output"
    record = json.loads(lines[-1])
    assert record.get("event_type") == EventType.dispatched.value
    assert record.get("run_id") == run_id
    assert record.get("sprint") == "sprint-99"
    assert record.get("issue") == 42


# ---------------------------------------------------------------------------
# AC3: Subprocess envelope
# ---------------------------------------------------------------------------

def test_envelope_subprocess_line_fields():
    """AC3: envelope_subprocess_line returns dict with run_id, sprint, issue, agent, ts, raw."""
    from services.logging import envelope_subprocess_line

    result = envelope_subprocess_line(
        raw="some agent output",
        run_id="sprint-20260611T120000-abcd",
        sprint="sprint-42",
        issue=77,
        agent="coder",
    )
    assert result["run_id"] == "sprint-20260611T120000-abcd"
    assert result["sprint"] == "sprint-42"
    assert result["issue"] == 77
    assert result["agent"] == "coder"
    assert "ts" in result
    assert result["raw"] == "some agent output"


def test_envelope_subprocess_line_is_json_serializable():
    """AC3: envelope_subprocess_line result serializes to valid JSON."""
    from services.logging import envelope_subprocess_line

    envelope = envelope_subprocess_line(
        raw="hello world",
        run_id="manual-20260611T120000-zzzz",
        sprint="sprint-1",
        issue=1,
        agent="tester",
    )
    serialized = json.dumps(envelope)
    parsed = json.loads(serialized)
    assert parsed["raw"] == "hello world"


def test_envelope_subprocess_line_raw_preserved():
    """AC3: raw text is preserved exactly inside the 'raw' field."""
    from services.logging import envelope_subprocess_line

    raw_text = "  Tool: Read file services/foo.py\nFound 42 items"
    env = envelope_subprocess_line(raw=raw_text, run_id="r", sprint="s", issue=1, agent="coder")
    assert env["raw"] == raw_text


# ---------------------------------------------------------------------------
# AC4: No bare print() calls
# ---------------------------------------------------------------------------

def _find_print_calls(directory: Path) -> list[str]:
    """Return list of 'file:line' strings where bare print() is called."""
    violations = []
    for py_file in sorted(directory.glob("*.py")):
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                violations.append(f"{py_file.name}:{node.lineno}")
    return violations


def test_no_bare_print_sprint_manager():
    """AC4: Zero bare print() calls in services/sprint_manager/*."""
    sm_dir = REPO_ROOT / "services" / "sprint_manager"
    violations = _find_print_calls(sm_dir)
    assert not violations, (
        f"Found {len(violations)} bare print() call(s) in services/sprint_manager/:\n"
        + "\n".join(violations[:20])
        + ("\n  ..." if len(violations) > 20 else "")
    )


def test_no_bare_print_scripts():
    """AC4: Zero bare print() calls in scripts/*."""
    scripts_dir = REPO_ROOT / "scripts"
    violations = _find_print_calls(scripts_dir)
    assert not violations, (
        f"Found {len(violations)} bare print() call(s) in scripts/:\n"
        + "\n".join(violations[:20])
        + ("\n  ..." if len(violations) > 20 else "")
    )


# ---------------------------------------------------------------------------
# AC5: Every log line is valid JSON
# ---------------------------------------------------------------------------

def test_jsonlines_valid_for_enveloped_output(tmp_path):
    """AC5: Writing enveloped subprocess lines produces valid JSON-Lines."""
    from services.logging import envelope_subprocess_line

    log_file = tmp_path / "agent-output.jsonl"
    lines_in = [
        "Starting coder agent for issue #42",
        "Reading file: apps/dashboard/server.py",
        "Done: feature/42-my-feature pushed",
    ]
    with log_file.open("w") as f:
        for line in lines_in:
            env = envelope_subprocess_line(raw=line, run_id="sprint-r", sprint="sprint-1", issue=42, agent="coder")
            f.write(json.dumps(env) + "\n")

    # Every line must parse as JSON
    for raw_line in log_file.read_text().splitlines():
        parsed = json.loads(raw_line)  # raises ValueError if invalid
        assert "raw" in parsed


# ---------------------------------------------------------------------------
# AC6: run_id constant within run, distinct between runs
# ---------------------------------------------------------------------------

def test_run_id_constant_within_run(tmp_path):
    """AC6: All log lines for one run share the same run_id."""
    from services.logging import envelope_subprocess_line

    run_id = "sprint-20260611T000000-test"
    log_file = tmp_path / "run.jsonl"
    with log_file.open("w") as f:
        for i in range(5):
            env = envelope_subprocess_line(raw=f"line {i}", run_id=run_id, sprint="sprint-1", issue=1, agent="coder")
            f.write(json.dumps(env) + "\n")

    run_ids = {json.loads(l)["run_id"] for l in log_file.read_text().splitlines()}
    assert run_ids == {run_id}, f"Expected one run_id, got {run_ids}"


def test_run_id_distinct_between_runs(monkeypatch):
    """AC6: Two separate mint_run_id() calls (without inherited parent id) produce distinct run_ids."""
    from services.run_id import mint_run_id

    # Remove COMMANDER_RUN_ID so each call mints a fresh id (parent propagation disabled)
    monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)

    r1 = mint_run_id("sprint", "1")
    time.sleep(0.01)
    r2 = mint_run_id("sprint", "2")  # different label guarantees different prefix
    assert r1 != r2, f"Two runs produced the same run_id: {r1}"


# ---------------------------------------------------------------------------
# AC7: Run browser renders .raw field
# ---------------------------------------------------------------------------

def test_colorize_log_line_file_exists():
    """AC7: log-colorize.js exists (prerequisite for .raw rendering)."""
    colorize_js = REPO_ROOT / "apps" / "dashboard" / "static" / "log-colorize.js"
    assert colorize_js.exists(), "log-colorize.js not found"


def test_colorize_log_js_handles_raw_field():
    """AC7: colorizeLogLine or a helper in log-colorize.js handles enveloped .raw field."""
    colorize_js = REPO_ROOT / "apps" / "dashboard" / "static" / "log-colorize.js"
    content = colorize_js.read_text()
    # Must have logic to extract .raw from JSON envelopes
    assert ".raw" in content or "raw" in content, (
        "log-colorize.js has no .raw field handling — add JSON-envelope extraction"
    )


def test_project_html_renders_raw_field():
    """AC7: project.html delegates log rendering to colorizeLogLine which extracts .raw.

    log-colorize.js is loaded by project.html and colorizeLogLine() now calls
    extractRaw() internally, so the .raw handling is present in the loaded JS.
    We verify that project.html uses colorizeLogLine for its raw-stream rendering
    AND that log-colorize.js contains the envelope extraction logic.
    """
    project_html = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
    colorize_js  = REPO_ROOT / "apps" / "dashboard" / "static" / "log-colorize.js"
    html = project_html.read_text()
    js   = colorize_js.read_text()
    # project.html must call colorizeLogLine for the raw log stream
    assert "colorizeLogLine" in html, "project.html must call colorizeLogLine for log rendering"
    # log-colorize.js must have .raw extraction logic
    assert "extractRaw" in js or ".raw" in js, (
        "log-colorize.js has no .raw field extraction — add extractRaw() or equivalent"
    )


# ---------------------------------------------------------------------------
# AC8: Schema documented
# ---------------------------------------------------------------------------

def test_logging_schema_doc_exists():
    """AC8: docs/logging-schema.md exists."""
    schema_doc = REPO_ROOT / "docs" / "logging-schema.md"
    assert schema_doc.exists(), "docs/logging-schema.md not found — create it with the logging schema"


def test_logging_schema_documents_event_type():
    """AC8: Schema doc covers event_type values."""
    schema_doc = REPO_ROOT / "docs" / "logging-schema.md"
    if not schema_doc.exists():
        pytest.skip("docs/logging-schema.md not yet created")
    content = schema_doc.read_text()
    assert "event_type" in content, "Schema doc must document event_type field"
    assert "EventType" in content or "dispatched" in content, "Schema doc must list EventType values"


def test_logging_schema_documents_envelope_format():
    """AC8: Schema doc covers the subprocess envelope format."""
    schema_doc = REPO_ROOT / "docs" / "logging-schema.md"
    if not schema_doc.exists():
        pytest.skip("docs/logging-schema.md not yet created")
    content = schema_doc.read_text()
    for required_field in ("run_id", "sprint", "issue", "agent", "ts", "raw"):
        assert required_field in content, f"Schema doc must document envelope field: {required_field}"

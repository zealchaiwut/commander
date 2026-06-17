"""Tests for issue #1268: Extract sprint_manager data classes to state.py.

AC coverage:
  AC1 - state.py exists and contains IssueState, SprintState, GateResult, SprintSummary
  AC2 - sprint_manager.py imports all four from services.sprint_manager.state, not defined locally
  AC3 - state.py has zero imports from other sprint_manager modules (true leaf)
  AC4 - All consumer files that previously imported these classes have updated imports
  AC5 - python -m py_compile services/sprint_manager/state.py exits 0
  AC6 - python -m py_compile sprint_manager.py (entry point) exits 0
  AC7 - sprint_manager.py --help produces output without error
  AC8 - No logic, docstrings, or field defaults are altered — pure move only
"""
from __future__ import annotations

import ast
import importlib
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
STATE_PY = SM_DIR / "state.py"
SPRINT_MANAGER_PY = SM_DIR / "sprint_manager.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SM_DIR))


# ── AC1: state.py exists with all four classes ────────────────────────────────

def test_ac1_state_py_exists():
    assert STATE_PY.exists(), "services/sprint_manager/state.py must exist"


def test_ac1_state_py_has_issue_state():
    src = STATE_PY.read_text()
    assert "class IssueState" in src, "state.py must define IssueState"


def test_ac1_state_py_has_sprint_state():
    src = STATE_PY.read_text()
    assert "class SprintState" in src, "state.py must define SprintState"


def test_ac1_state_py_has_gate_result():
    src = STATE_PY.read_text()
    assert "class GateResult" in src, "state.py must define GateResult"


def test_ac1_state_py_has_sprint_summary():
    src = STATE_PY.read_text()
    assert "class SprintSummary" in src, "state.py must define SprintSummary"


# ── AC2: sprint_manager.py imports from state, not defined locally ─────────────

def test_ac2_sprint_manager_imports_from_state():
    src = SPRINT_MANAGER_PY.read_text()
    assert "from services.sprint_manager.state import" in src or \
           "from .state import" in src, \
        "sprint_manager.py must import data classes from services.sprint_manager.state"


def test_ac2_sprint_manager_no_local_issue_state():
    src = SPRINT_MANAGER_PY.read_text()
    assert "class IssueState" not in src, \
        "IssueState must not be defined in sprint_manager.py"


def test_ac2_sprint_manager_no_local_sprint_state():
    src = SPRINT_MANAGER_PY.read_text()
    assert "class SprintState" not in src, \
        "SprintState must not be defined in sprint_manager.py"


def test_ac2_sprint_manager_no_local_gate_result():
    src = SPRINT_MANAGER_PY.read_text()
    assert "class GateResult" not in src, \
        "GateResult must not be defined in sprint_manager.py"


def test_ac2_sprint_manager_no_local_sprint_summary():
    src = SPRINT_MANAGER_PY.read_text()
    assert "class SprintSummary" not in src, \
        "SprintSummary must not be defined in sprint_manager.py"


# ── AC3: state.py is a true leaf — no imports from sprint_manager modules ────

def test_ac3_state_py_no_sprint_manager_imports():
    """state.py must not import from any other services/sprint_manager/*.py module."""
    tree = ast.parse(STATE_PY.read_text())
    sm_imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                # Flag any import from within the sprint_manager package
                if "sprint_manager" in mod and mod != "services.sprint_manager.state":
                    sm_imports.append(mod)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "sprint_manager" in alias.name:
                        sm_imports.append(alias.name)
    assert sm_imports == [], \
        f"state.py must not import from sprint_manager modules, found: {sm_imports}"


# ── AC4: consumer files have valid imports (no broken direct imports) ──────────

def test_ac4_test_739_can_import_classes():
    """test_739 imports SprintState and IssueState directly — must still work."""
    # The import path in test_739 is: from services.sprint_manager.sprint_manager import SprintState, IssueState
    # After the refactor, sprint_manager.py re-exports these so the import still resolves.
    import importlib
    mod = importlib.import_module("services.sprint_manager.sprint_manager")
    assert hasattr(mod, "SprintState"), "SprintState must be importable from sprint_manager module"
    assert hasattr(mod, "IssueState"), "IssueState must be importable from sprint_manager module"


def test_ac4_state_module_exports_all_four():
    """state.py module must export all four classes directly."""
    mod = importlib.import_module("services.sprint_manager.state")
    for cls_name in ("IssueState", "SprintState", "GateResult", "SprintSummary"):
        assert hasattr(mod, cls_name), f"state.py must export {cls_name}"


# ── AC5: py_compile state.py exits 0 ─────────────────────────────────────────

def test_ac5_py_compile_state_py():
    py_compile.compile(str(STATE_PY), doraise=True)


# ── AC6: py_compile sprint_manager.py exits 0 ────────────────────────────────

def test_ac6_py_compile_sprint_manager_py():
    py_compile.compile(str(SPRINT_MANAGER_PY), doraise=True)


# ── AC7: sprint_manager.py --help runs without error ─────────────────────────

def test_ac7_sprint_manager_help():
    result = subprocess.run(
        [sys.executable, str(SPRINT_MANAGER_PY), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, \
        f"sprint_manager.py --help failed:\n{result.stderr}"
    assert len(result.stdout) > 0, "sprint_manager.py --help must produce output"


# ── AC8: class definitions are identical (field names preserved) ───────────────

def test_ac8_issue_state_fields():
    mod = importlib.import_module("services.sprint_manager.state")
    cls = mod.IssueState
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    expected = {
        "number", "title", "status", "skip_reason", "category",
        "tokens_in", "tokens_out", "agent_status", "status_changed_at",
        "coder_started_at", "coder_finished_at", "tester_started_at",
        "tester_finished_at", "failure_reason", "dispatch_level",
        "tester_attempt_count", "coder_model", "coder_backend",
    }
    assert fields == expected, f"IssueState fields changed. Got: {fields}"


def test_ac8_sprint_state_fields():
    mod = importlib.import_module("services.sprint_manager.state")
    cls = mod.SprintState
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    expected = {
        "sprint_label", "sprint_number", "project", "issues",
        "start_timestamp", "total_tokens_in", "total_tokens_out",
        "wall_clock_secs", "token_budget", "rate_limit_events",
        "reviewer_status", "reviewer_comment_url", "reviewer_findings",
        "documenter_status", "documenter_files_touched", "documenter_commit_sha",
        "estimator_status", "estimator_total_minutes", "estimates",
        "pipeline_mode", "reconciliation", "summary_issue_url",
    }
    assert fields == expected, f"SprintState fields changed. Got: {fields}"


def test_ac8_gate_result_fields():
    mod = importlib.import_module("services.sprint_manager.state")
    cls = mod.GateResult
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    assert fields == {"gate", "passed", "skipped", "output"}, \
        f"GateResult fields changed. Got: {fields}"


def test_ac8_sprint_summary_fields():
    mod = importlib.import_module("services.sprint_manager.state")
    cls = mod.SprintSummary
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    assert fields == {"processed", "merged", "gate_failures", "skipped"}, \
        f"SprintSummary fields changed. Got: {fields}"


def test_ac8_issue_state_defaults():
    mod = importlib.import_module("services.sprint_manager.state")
    iss = mod.IssueState(number=1, title="test")
    assert iss.status == "pending"
    assert iss.tokens_in == 0
    assert iss.tokens_out == 0
    assert iss.dispatch_level == 0
    assert iss.tester_attempt_count == 0
    assert iss.skip_reason is None
    assert iss.coder_model is None
    assert iss.coder_backend is None


def test_ac8_gate_result_defaults():
    mod = importlib.import_module("services.sprint_manager.state")
    gr = mod.GateResult(gate="pytest", passed=True)
    assert gr.skipped is False
    assert gr.output == ""


def test_ac8_gate_result_symbol_property():
    mod = importlib.import_module("services.sprint_manager.state")
    assert mod.GateResult(gate="x", passed=True).symbol == "PASS"
    assert mod.GateResult(gate="x", passed=False).symbol == "FAIL"
    assert mod.GateResult(gate="x", passed=True, skipped=True).symbol == "skipped"


def test_ac8_issue_state_to_dict_round_trip():
    mod = importlib.import_module("services.sprint_manager.state")
    iss = mod.IssueState(number=42, title="hello", status="done", tokens_in=10, tokens_out=20)
    restored = mod.IssueState.from_dict(iss.to_dict())
    assert restored.number == 42
    assert restored.title == "hello"
    assert restored.status == "done"
    assert restored.tokens_in == 10
    assert restored.tokens_out == 20


def test_ac8_sprint_state_to_dict_round_trip():
    mod = importlib.import_module("services.sprint_manager.state")
    s = mod.SprintState(sprint_label="sprint-99", sprint_number=99, project="x/y")
    s.issues = [mod.IssueState(number=1, title="t")]
    s.pipeline_mode = True
    restored = mod.SprintState.from_dict(s.to_dict())
    assert restored.sprint_label == "sprint-99"
    assert restored.sprint_number == 99
    assert restored.project == "x/y"
    assert restored.pipeline_mode is True
    assert len(restored.issues) == 1
    assert restored.issues[0].number == 1

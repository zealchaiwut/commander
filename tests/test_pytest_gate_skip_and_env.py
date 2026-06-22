"""Pytest gate: skip-before-binary (A) and ENV_ERROR classification (B).

Regression for crux sprint-9 #90/#91, where the pytest gate hard-failed with
"pytest binary not found" — even for a frontend ticket that changed no test
files — because it resolved the pytest binary BEFORE checking whether anything
needed to run, and classified a missing binary as a PYTEST_FAIL code defect.
"""
import services.sprint_manager.gates as gates
from services.sprint_manager.failures import FailureCategory


def _no_op(*a, **k):
    return None


def test_pytest_gate_skips_when_no_test_files_changed(monkeypatch, tmp_path):
    """A: a ticket that changed no tests/ files skips — never requires pytest."""
    monkeypatch.setattr(gates, "_post_agent_event", _no_op)
    monkeypatch.setattr(gates, "_changed_py_files", lambda *a, **k: ["src/app.py"])

    # If pytest resolution is reached, fail loudly — it must not be.
    def _boom(*a, **k):
        raise AssertionError("pytest binary must not be resolved when no tests changed")
    monkeypatch.setattr(gates, "_try", _boom)

    result = gates._gate_pytest(
        issue_num=90,
        worktester_dashboard=tmp_path,
        skip=False,
        gate_scope="changed",
        worktester_root=tmp_path,
    )
    assert result.passed is True
    assert "no test files changed" in (result.output or "")
    assert result.category is None


def test_pytest_gate_missing_binary_is_env_error(monkeypatch, tmp_path):
    """B: tests changed but pytest is unavailable → ENV_ERROR, not PYTEST_FAIL."""
    monkeypatch.setattr(gates, "_post_agent_event", _no_op)
    monkeypatch.setattr(gates, "_changed_py_files", lambda *a, **k: ["tests/test_x.py"])
    # git rev-parse (changed-scope cwd resolution) succeeds; pytest never runs.
    monkeypatch.setattr(gates, "_run_timed", lambda *a, **k: (0, str(tmp_path), ""))
    # `which pytest` finds nothing, and tmp_path has no venv/bin/pytest.
    monkeypatch.setattr(gates, "_try", lambda *a, **k: (False, "", ""))

    result = gates._gate_pytest(
        issue_num=91,
        worktester_dashboard=tmp_path,
        skip=False,
        gate_scope="changed",
        worktester_root=tmp_path,
    )
    assert result.passed is False
    assert result.category == FailureCategory.ENV_ERROR
    assert "environment problem" in (result.output or "").lower()


def test_env_error_is_not_a_logic_failure_category():
    """ENV_ERROR must stay out of the needs-rework / coder-requeue set."""
    from services.sprint_manager.sprint_manager import _LOGIC_FAILURE_CATEGORIES
    assert FailureCategory.ENV_ERROR not in _LOGIC_FAILURE_CATEGORIES
    assert FailureCategory.PYTEST_FAIL in _LOGIC_FAILURE_CATEGORIES

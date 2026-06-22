"""Tests for issue #1289: Extract sprint_manager pipeline dispatch to services module"""
import os
import sys
import subprocess
from pathlib import Path

# Find project root
PROJECT_ROOT = Path(__file__).parent.parent


def test_pipeline_module_exists():
    """AC: services/sprint_manager/pipeline.py exists"""
    pipeline_path = PROJECT_ROOT / "services" / "sprint_manager" / "pipeline.py"
    assert pipeline_path.exists(), f"pipeline.py not found at {pipeline_path}"


def test_all_five_functions_present_in_pipeline():
    """AC: pipeline.py contains all five functions with correct definitions"""
    pipeline_path = PROJECT_ROOT / "services" / "sprint_manager" / "pipeline.py"
    content = pipeline_path.read_text()

    required_functions = [
        "_run_pipeline_dispatch",
        "_compute_dispatch_levels",
        "_build_sprint_dag_layers",
        "_warn_file_conflicts",
        "list_backlog_issues",
    ]

    for func_name in required_functions:
        # Check for function definition
        assert f"def {func_name}" in content, f"Function {func_name} not found in pipeline.py"


def test_functions_removed_from_sprint_manager():
    """AC: Original sprint_manager.py has no moved function definitions"""
    sprint_manager_path = PROJECT_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    moved_functions = [
        "_run_pipeline_dispatch",
        "_compute_dispatch_levels",
        "_build_sprint_dag_layers",
        "_warn_file_conflicts",
        "list_backlog_issues",
    ]

    for func_name in moved_functions:
        # Check that function definition is NOT in sprint_manager.py
        assert f"def {func_name}" not in content, f"Function {func_name} still in sprint_manager.py"


def test_pipeline_py_compiles():
    """AC: python -m py_compile services/sprint_manager/pipeline.py exits 0"""
    pipeline_path = PROJECT_ROOT / "services" / "sprint_manager" / "pipeline.py"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(pipeline_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"pipeline.py compilation failed:\n{result.stderr}"


def test_sprint_manager_py_compiles():
    """AC: python -m py_compile services/sprint_manager/sprint_manager.py exits 0"""
    sprint_manager_path = PROJECT_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(sprint_manager_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sprint_manager.py compilation failed:\n{result.stderr}"


def test_functions_importable_from_pipeline():
    """AC: All five functions are importable from services.sprint_manager.pipeline"""
    try:
        from services.sprint_manager.pipeline import (  # noqa: E402, F401
            _run_pipeline_dispatch,
            _compute_dispatch_levels,
            _build_sprint_dag_layers,
            _warn_file_conflicts,
            list_backlog_issues,
        )
    except ImportError as e:
        raise AssertionError(f"Failed to import functions from pipeline: {e}")


def test_no_duplicate_function_definitions():
    """AC: Each of the five functions is defined exactly once in the codebase"""
    import subprocess

    functions = [
        "_run_pipeline_dispatch",
        "_compute_dispatch_levels",
        "_build_sprint_dag_layers",
        "_warn_file_conflicts",
        "list_backlog_issues",
    ]

    for func_name in functions:
        # Search for all definitions of this function
        result = subprocess.run(
            ["grep", "-r", f"^def {func_name}",
             str(PROJECT_ROOT / "services" / "sprint_manager"),
             "--include=*.py"],
            capture_output=True,
            text=True,
        )

        definition_count = len([line for line in result.stdout.strip().split('\n') if line])
        assert definition_count == 1, (
            f"Function {func_name} defined {definition_count} times "
            f"(expected 1):\n{result.stdout}"
        )


def test_sprint_manager_imports_from_pipeline():
    """AC: sprint_manager.py imports the five functions from pipeline module"""
    sprint_manager_path = PROJECT_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    # Check for import statement
    assert "from services.sprint_manager.pipeline import" in content, \
        "sprint_manager.py does not import from pipeline"

    # Check for each function in imports
    functions = [
        "_run_pipeline_dispatch",
        "_compute_dispatch_levels",
        "_build_sprint_dag_layers",
        "_warn_file_conflicts",
        "list_backlog_issues",
    ]

    for func_name in functions:
        assert func_name in content, f"{func_name} not imported in sprint_manager.py"


def test_no_unused_imports_in_sprint_manager():
    """AC: No dead imports remain in sprint_manager.py (ruff F401 check)"""
    import subprocess

    sprint_manager_path = PROJECT_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(sprint_manager_path), "--select=F401"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    # ruff exit 0 means all checks passed (no unused imports)
    assert result.returncode == 0, f"Unused imports detected:\n{result.stdout}\n{result.stderr}"


def test_no_unused_imports_in_pipeline():
    """AC: No dead imports in pipeline.py (ruff F401 check)"""
    import subprocess

    pipeline_path = PROJECT_ROOT / "services" / "sprint_manager" / "pipeline.py"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(pipeline_path), "--select=F401"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    # ruff exit 0 means all checks passed (no unused imports)
    assert result.returncode == 0, f"Unused imports detected:\n{result.stdout}\n{result.stderr}"

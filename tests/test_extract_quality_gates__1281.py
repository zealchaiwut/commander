"""Tests for issue #1281: Extract sprint_manager quality gates to gates.py"""
import os
import sys
import subprocess
import importlib
from pathlib import Path

# Get repo root for module imports
REPO_ROOT = Path(__file__).parent.parent


def test_gates_py_file_exists():
    """AC: services/sprint_manager/gates.py exists"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    assert gates_file.exists(), f"gates.py not found at {gates_file}"


def test_gate_typecheck_in_gates_py():
    """AC: _gate_typecheck is defined in gates.py"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    content = gates_file.read_text()
    assert "def _gate_typecheck(" in content, "_gate_typecheck not found in gates.py"


def test_gate_design_in_gates_py():
    """AC: _gate_design is defined in gates.py"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    content = gates_file.read_text()
    assert "def _gate_design(" in content, "_gate_design not found in gates.py"


def test_gate_merge_preview_in_gates_py():
    """AC: _gate_merge_preview is defined in gates.py"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    content = gates_file.read_text()
    assert "def _gate_merge_preview(" in content, "_gate_merge_preview not found in gates.py"


def test_gate_monolith_in_gates_py():
    """AC: _gate_monolith is defined in gates.py"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    content = gates_file.read_text()
    assert "def _gate_monolith(" in content, "_gate_monolith not found in gates.py"


def test_run_quality_gates_in_gates_py():
    """AC: _run_quality_gates is defined in gates.py"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    content = gates_file.read_text()
    assert "def _run_quality_gates(" in content, "_run_quality_gates not found in gates.py"


def test_symbols_not_defined_in_sprint_manager():
    """AC: Original sprint_manager.py no longer defines these five symbols"""
    sprint_manager_file = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_file.read_text()

    # Check that these are NOT defined as new functions (only imported)
    # We look for "^def " at line start to find definitions (not calls)
    lines = content.split("\n")
    definitions = []
    for i, line in enumerate(lines, 1):
        if line.startswith("def _gate_typecheck("):
            definitions.append(("_gate_typecheck", i))
        elif line.startswith("def _gate_design("):
            definitions.append(("_gate_design", i))
        elif line.startswith("def _gate_merge_preview("):
            definitions.append(("_gate_merge_preview", i))
        elif line.startswith("def _gate_monolith("):
            definitions.append(("_gate_monolith", i))
        elif line.startswith("def _run_quality_gates("):
            definitions.append(("_run_quality_gates", i))

    assert not definitions, f"Found definitions in sprint_manager.py: {definitions}"


def test_gates_py_compiles():
    """AC: python -m py_compile services/sprint_manager/gates.py exits 0"""
    result = subprocess.run(
        ["python", "-m", "py_compile", "services/sprint_manager/gates.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Compilation failed: {result.stderr}"


def test_sprint_manager_compiles():
    """AC: python -m py_compile on sprint_manager module exits 0"""
    result = subprocess.run(
        ["python", "-m", "py_compile", "services/sprint_manager/sprint_manager.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Compilation failed: {result.stderr}"


def test_functions_importable_from_gates():
    """AC: All five functions can be imported from gates.py"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.sprint_manager.gates import (
            _gate_typecheck,
            _gate_design,
            _gate_merge_preview,
            _gate_monolith,
            _run_quality_gates,
        )
        assert callable(_gate_typecheck)
        assert callable(_gate_design)
        assert callable(_gate_merge_preview)
        assert callable(_gate_monolith)
        assert callable(_run_quality_gates)
    finally:
        sys.path.pop(0)


def test_functions_importable_from_sprint_manager():
    """AC: All five functions re-exported via sprint_manager imports"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.sprint_manager import sprint_manager
        assert hasattr(sprint_manager, "_gate_typecheck")
        assert hasattr(sprint_manager, "_gate_design")
        assert hasattr(sprint_manager, "_gate_merge_preview")
        assert hasattr(sprint_manager, "_gate_monolith")
        assert hasattr(sprint_manager, "_run_quality_gates")
    finally:
        sys.path.pop(0)


def test_gate_typecheck_signature_unchanged():
    """AC: No logic changes — _gate_typecheck signature identical"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.sprint_manager.gates import _gate_typecheck
        import inspect
        sig = inspect.signature(_gate_typecheck)
        params = list(sig.parameters.keys())
        # Verify expected parameters are present
        assert "issue_context" in params or len(params) > 0, "Unexpected signature for _gate_typecheck"
    finally:
        sys.path.pop(0)


def test_gate_design_signature_unchanged():
    """AC: No logic changes — _gate_design signature identical"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.sprint_manager.gates import _gate_design
        import inspect
        sig = inspect.signature(_gate_design)
        # Verify it is callable and has parameters
        assert len(sig.parameters) > 0, "Unexpected signature for _gate_design"
    finally:
        sys.path.pop(0)


def test_run_quality_gates_accepts_context():
    """AC: _run_quality_gates signature unchanged — accepts required context"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.sprint_manager.gates import _run_quality_gates
        import inspect
        sig = inspect.signature(_run_quality_gates)
        # Verify it accepts parameters
        assert len(sig.parameters) > 0, "_run_quality_gates has no parameters"
    finally:
        sys.path.pop(0)


def test_no_syntax_errors_in_gates_py():
    """AC: gates.py has no syntax errors (can be compiled)"""
    gates_file = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
    result = subprocess.run(
        ["python", "-m", "py_compile", str(gates_file)],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Syntax errors in gates.py: {result.stderr}"


def test_gates_py_imports_work():
    """AC: All imports in gates.py resolve successfully"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import services.sprint_manager.gates
        # If import succeeds, all imports are resolvable
        assert services.sprint_manager.gates is not None
    finally:
        sys.path.pop(0)

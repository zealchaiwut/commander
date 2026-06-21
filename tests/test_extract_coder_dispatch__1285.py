"""Tests for issue #1285: Extract coder dispatch logic to dispatch.py (runs against UAT)"""
import subprocess
import sys
from pathlib import Path


# Resolved from environment at runtime — should be set by pytest conftest or sprint_manager.
# For local runs, falls back to REPO_ROOT inference.
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


DISPATCH_PATH = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# --- Acceptance Criteria ---


def test_extract_coder_dispatch__dispatch_file_exists():
    """AC: File services/sprint_manager/dispatch.py exists."""
    assert DISPATCH_PATH.exists(), f"{DISPATCH_PATH} must exist"


def test_extract_coder_dispatch__dispatch_contains_functions():
    """AC: dispatch.py contains _dispatch_coder, _load_agent_persona, _agent_identity_env as definitions."""
    source = DISPATCH_PATH.read_text(encoding="utf-8")

    # Check for function definitions (not just imports)
    assert "def _dispatch_coder(" in source, "_dispatch_coder must be defined in dispatch.py"
    assert "def _load_agent_persona(" in source, "_load_agent_persona must be defined in dispatch.py"
    assert "def _agent_identity_env(" in source, "_agent_identity_env must be defined in dispatch.py"


def test_extract_coder_dispatch__sprint_manager_no_duplicates():
    """AC: sprint_manager.py no longer defines those three functions (only imports them)."""
    source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")

    # None of the three should be defined (def statements) in sprint_manager.py
    assert source.count("def _dispatch_coder(") == 0, "sprint_manager.py must not define _dispatch_coder"
    assert source.count("def _load_agent_persona(") == 0, "sprint_manager.py must not define _load_agent_persona"
    assert source.count("def _agent_identity_env(") == 0, "sprint_manager.py must not define _agent_identity_env"


def test_extract_coder_dispatch__functions_importable():
    """AC: All call sites can import the three functions (re-exported from sprint_manager)."""
    # Import from dispatch directly (the primary path)
    from services.sprint_manager.dispatch import (
        _dispatch_coder,
        _load_agent_persona,
        _agent_identity_env,
    )

    # Verify they are callable
    assert callable(_dispatch_coder), "_dispatch_coder must be callable"
    assert callable(_load_agent_persona), "_load_agent_persona must be callable"
    assert callable(_agent_identity_env), "_agent_identity_env must be callable"


def test_extract_coder_dispatch__dispatch_py_compiles():
    """AC: python -m py_compile services/sprint_manager/dispatch.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(DISPATCH_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Compilation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_extract_coder_dispatch__sprint_manager_py_compiles():
    """AC: python -m py_compile on sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Compilation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_extract_coder_dispatch__private_api_unchanged():
    """AC: No new public API introduced; extracted symbols remain private (_ prefix)."""
    # Verify the functions have the underscore prefix (already checked above in other tests)
    source = DISPATCH_PATH.read_text(encoding="utf-8")

    # All three must start with underscore
    assert "def _dispatch_coder(" in source
    assert "def _load_agent_persona(" in source
    assert "def _agent_identity_env(" in source

    # Verify no "def dispatch_coder(" (public version)
    assert "def dispatch_coder(" not in source, "Public variant dispatch_coder must not exist"
    assert "def load_agent_persona(" not in source, "Public variant load_agent_persona must not exist"
    assert "def agent_identity_env(" not in source, "Public variant agent_identity_env must not exist"


def test_extract_coder_dispatch__line_count():
    """AC: dispatch.py contains the extracted functions and their helpers (~814 lines total)."""
    source = DISPATCH_PATH.read_text(encoding="utf-8")
    line_count = len(source.splitlines())

    # Accept within reasonable range (814 ± 20) — includes imports, helpers, and the three main functions
    assert 790 <= line_count <= 830, (
        f"dispatch.py has {line_count} lines; expected ~814 (±20)"
    )


def test_extract_coder_dispatch__no_circular_imports():
    """AC: dispatch.py does not import sprint_manager (the parent module) at module level (no circular dep)."""
    source = DISPATCH_PATH.read_text(encoding="utf-8")

    # Check that sprint_manager.py itself is not imported directly
    # (imports from sprint_manager.config, sprint_manager.worktree, etc. are OK)
    lines = source.splitlines()
    for i, line in enumerate(lines[:100]):  # Check first 100 lines (imports section)
        # Look for "import sprint_manager" or "from sprint_manager import" (but not "from services.sprint_manager.X")
        stripped = line.strip()
        if stripped.startswith("import sprint_manager") or (
            stripped.startswith("from sprint_manager import") and "services" not in line
        ):
            assert False, (
                f"Line {i+1}: dispatch.py must not import sprint_manager itself at module level: {line}"
            )

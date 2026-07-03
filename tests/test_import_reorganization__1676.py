"""Tests for issue #1676: Move mid-file api_client import to top-of-file imports in sprint_manager.py

This test suite verifies:
- The `is_retryable_rate_limit` import is placed in the top-of-file import block
- The `# noqa: E402` suppression is removed from the import
- No circular import errors occur at runtime
- Existing tests continue to pass
"""
import os
import sys
import subprocess
from pathlib import Path


def test_import_in_top_of_file():
    """AC: The import is located in the top-of-file import block, not at mid-file position."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sm_path.read_text()

    lines = content.split("\n")

    # Find the import statement (may be on multiple lines with parentheses)
    import_start_line = None
    for i, line in enumerate(lines):
        if "from services.sprint_manager.api_client import" in line:
            import_start_line = i + 1
            break

    assert import_start_line is not None, "is_retryable_rate_limit import not found in sprint_manager.py"

    # Verify the full import block contains is_retryable_rate_limit
    import_block = []
    for i in range(import_start_line - 1, min(import_start_line + 5, len(lines))):
        import_block.append(lines[i])
        if ")" in lines[i]:
            break

    import_text = "\n".join(import_block)
    assert "is_retryable_rate_limit" in import_text, "is_retryable_rate_limit not found in import block"

    # Verify it's in the top-of-file import block (before any code functions)
    # Top imports section ends before the first function definition or major code block
    # We check that the import comes before line 400 (where code starts after imports)
    assert import_start_line < 400, f"Import found at line {import_start_line}, expected in top-of-file import block (< 400)"

    # Verify the import appears after other service imports (organized grouping)
    # Should be after post_sprint imports
    post_sprint_import_line = None
    for i, line in enumerate(lines):
        if "from services.sprint_manager.post_sprint import" in line:
            post_sprint_import_line = i + 1
            break

    assert post_sprint_import_line is not None, "post_sprint import not found"
    assert import_start_line > post_sprint_import_line, "api_client import should be after post_sprint import"


def test_noqa_suppression_removed():
    """AC: The # noqa: E402 suppression comment is removed from the import line."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sm_path.read_text()

    lines = content.split("\n")

    # Find the import statement
    import_start_line = None
    for i, line in enumerate(lines):
        if "from services.sprint_manager.api_client import" in line:
            import_start_line = i
            break

    assert import_start_line is not None, "api_client import not found"

    # Check the import block (may span multiple lines with parentheses)
    import_block = []
    for i in range(import_start_line, min(import_start_line + 5, len(lines))):
        import_block.append(lines[i])
        if ")" in lines[i]:
            break

    import_text = "\n".join(import_block)

    # Verify the import block contains is_retryable_rate_limit
    assert "is_retryable_rate_limit" in import_text, "is_retryable_rate_limit not found in import block"

    # Verify no E402 noqa suppression
    assert "noqa: E402" not in import_text, (
        f"# noqa: E402 suppression found in import block at line {import_start_line + 1}. "
        "It should be removed when the import is hoisted to top-of-file."
    )


def test_no_mid_file_import():
    """Verify the import is not present in the mid-file position (old location ~line 870+)."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sm_path.read_text()

    lines = content.split("\n")

    # Check that there's no import statement around the old location (lines 860-880)
    for i in range(859, min(880, len(lines))):
        line = lines[i]
        if "from services.sprint_manager.api_client import" in line:
            raise AssertionError(
                f"Mid-file import found at line {i + 1}. "
                "The import should be moved to top-of-file, not present in mid-file location."
            )


def test_module_import_no_error():
    """AC: The module imports without error (no ImportError or CircularImportError)."""
    # Get the repo root and add to sys.path
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        import services.sprint_manager.sprint_manager
        # If we get here, the import succeeded
        assert True, "Module imported successfully"
    except ImportError as e:
        raise AssertionError(f"ImportError when importing sprint_manager: {e}")
    except Exception as e:
        raise AssertionError(f"Unexpected error when importing sprint_manager: {e}")


def test_is_rate_limit_function_available():
    """Verify the _is_retryable_rate_limit function is accessible and callable."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from services.sprint_manager import sprint_manager

    # Check that the function is available
    assert hasattr(sprint_manager, "_is_retryable_rate_limit"), (
        "_is_retryable_rate_limit not found as an attribute of sprint_manager module"
    )

    # Verify it's callable
    assert callable(sprint_manager._is_retryable_rate_limit), (
        "_is_retryable_rate_limit exists but is not callable"
    )


def test_api_client_import_standalone():
    """Verify api_client module itself has no circular dependencies."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from services.sprint_manager import api_client
        assert hasattr(api_client, "is_retryable_rate_limit"), (
            "is_retryable_rate_limit not found in api_client module"
        )
    except ImportError as e:
        raise AssertionError(f"Failed to import api_client: {e}")


def test_existing_sprint_manager_tests_pass():
    """AC: Run a quick smoke test on sprint_manager import and basic functionality."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Simple test: import and verify the _is_rate_limit_error function works
    from services.sprint_manager.sprint_manager import _is_rate_limit_error

    # Test with a normal error message (should return False)
    result = _is_rate_limit_error("Some normal error message")
    assert result == (False, None), f"Expected (False, None) for normal error, got {result}"

    # Test with a rate limit signal (should return True)
    result = _is_rate_limit_error("Error: 429 rate limit exceeded")
    assert result[0] is True, f"Expected rate limit detection for '429' signal, got {result}"

    # Test with quota exceeded (ICA gateway format)
    result = _is_rate_limit_error("quota_exceeded: Usage limit exceeded")
    assert result[0] is True, f"Expected rate limit detection for 'quota_exceeded' signal, got {result}"

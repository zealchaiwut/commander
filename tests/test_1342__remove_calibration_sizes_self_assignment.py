"""
Test for issue #1342: remove no-op self-assignment _CALIBRATION_SIZES = _CALIBRATION_SIZES

AC1: The self-assignment line is removed from server.py
AC2: _CALIBRATION_SIZES remains accessible via original import
AC3: No references to _CALIBRATION_SIZES in server.py are broken
AC4: Dashboard server starts without errors
"""

import sys
from pathlib import Path


def test_calibration_sizes_no_self_assignment():
    """AC1: The self-assignment line _CALIBRATION_SIZES = _CALIBRATION_SIZES is removed."""
    server_path = Path(__file__).parent.parent / "apps" / "dashboard" / "server.py"
    content = server_path.read_text()

    # The line should not exist
    assert "_CALIBRATION_SIZES = _CALIBRATION_SIZES" not in content, \
        "Self-assignment line should be removed"


def test_calibration_sizes_still_imported():
    """AC2: _CALIBRATION_SIZES is still imported from calibration_cache_service."""
    server_path = Path(__file__).parent.parent / "apps" / "dashboard" / "server.py"
    content = server_path.read_text()

    # Verify the import statement exists
    assert "from calibration_cache_service import (" in content, \
        "calibration_cache_service import should exist"
    assert "_CALIBRATION_SIZES," in content, \
        "_CALIBRATION_SIZES should be in the import list"


def test_calibration_sizes_accessible_in_module():
    """AC2: _CALIBRATION_SIZES is accessible in the server module."""
    # Import the module to verify _CALIBRATION_SIZES is accessible
    sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
    try:
        from server import _CALIBRATION_SIZES
        assert _CALIBRATION_SIZES is not None, \
            "_CALIBRATION_SIZES should be accessible and not None"
        assert isinstance(_CALIBRATION_SIZES, (list, tuple, dict)), \
            "_CALIBRATION_SIZES should be a collection type"
    finally:
        sys.path.pop(0)


def test_no_broken_references_after_removal():
    """AC3: No syntax errors or broken references after removing the line."""
    server_path = Path(__file__).parent.parent / "apps" / "dashboard" / "server.py"

    # The file should be syntactically valid Python
    try:
        with open(server_path, 'r') as f:
            compile(f.read(), str(server_path), 'exec')
    except SyntaxError as e:
        raise AssertionError(f"server.py has syntax error: {e}")


def test_calibration_sizes_used_elsewhere():
    """AC3: Verify _CALIBRATION_SIZES is used elsewhere in the file (not dead code)."""
    server_path = Path(__file__).parent.parent / "apps" / "dashboard" / "server.py"
    content = server_path.read_text()

    # Count occurrences of _CALIBRATION_SIZES (should be multiple after removing the self-assignment)
    count = content.count("_CALIBRATION_SIZES")
    # At least: 1 import + multiple uses in functions
    assert count >= 3, \
        f"_CALIBRATION_SIZES should be used multiple times, found {count} occurrences"

"""Tests for issue #1930: remove unused `import db as _db` in test_merge_conflict_auto_resolve__1898.py"""
import os
import subprocess


def test_1930__test_file_syntax_valid():
    """AC: Unused import line removed and test file parses correctly."""
    # Verify the modified test file has valid Python syntax
    result = subprocess.run(
        ["python3", "-m", "py_compile", "tests/test_merge_conflict_auto_resolve__1898.py"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    assert result.returncode == 0, f"Syntax error in test file: {result.stderr}"


def test_1930__no_db_import_in_method():
    """AC: The unused `import db as _db` line is removed from test_not_blocked_after_clear."""
    with open("tests/test_merge_conflict_auto_resolve__1898.py", "r") as f:
        content = f.read()

    # Find the test_not_blocked_after_clear method
    method_start = content.find("def test_not_blocked_after_clear(")
    method_end = content.find("\n    def ", method_start + 1)  # Find next method
    if method_end == -1:
        method_end = len(content)

    method_content = content[method_start:method_end]

    # Verify the import db as _db line is NOT in the method
    assert "import db as _db" not in method_content, "Unused 'import db as _db' should be removed"


def test_1930__required_imports_present():
    """AC: Required imports (server, TestClient) are still present."""
    with open("tests/test_merge_conflict_auto_resolve__1898.py", "r") as f:
        content = f.read()

    method_start = content.find("def test_not_blocked_after_clear(")
    method_end = content.find("\n    def ", method_start + 1)
    if method_end == -1:
        method_end = len(content)

    method_content = content[method_start:method_end]

    # Verify required imports are still there
    assert "import server as srv" in method_content, "Required 'import server as srv' missing"
    assert "from fastapi.testclient import TestClient" in method_content, "Required TestClient import missing"

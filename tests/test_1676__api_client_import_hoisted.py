"""Tests for issue #1676 — api_client import hoisted to top-of-file block.

AC1: is_retryable_rate_limit import is in the top-of-file import block.
AC2: The hoisted import line has no # noqa: E402 suppressor.
AC4: The module imports without error.
AC5: No other import statements were relocated.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SM_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

# Line beyond which a sprint_manager import is considered "mid-file" (i.e. after
# the main import block ends and runtime constants / functions begin).
_IMPORT_BLOCK_END_LINE = 370


def _read_lines() -> list[str]:
    return SM_PATH.read_text().splitlines()


def _find_api_client_import_lines(lines: list[str]) -> list[int]:
    """Return 1-based line numbers where the api_client is_retryable import statement appears.

    Only matches actual import lines, not docstrings or comments.
    """
    results = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # `from services.sprint_manager.api_client import (` opener
        if stripped.startswith("from services.sprint_manager.api_client import"):
            results.append(i)
        # Continuation line `is_retryable_rate_limit as _is_retryable_rate_limit,`
        elif stripped == "is_retryable_rate_limit as _is_retryable_rate_limit,":
            results.append(i)
    return results


def test_ac1_import_in_top_block():
    """AC1: The api_client is_retryable_rate_limit import must appear in the
    top-of-file import block, before runtime constants begin (~line 370)."""
    lines = _read_lines()
    import_lines = _find_api_client_import_lines(lines)

    assert import_lines, (
        "Could not find 'api_client'/'is_retryable_rate_limit' import in sprint_manager.py"
    )

    # At least one occurrence must be in the top import block
    in_block = [ln for ln in import_lines if ln < _IMPORT_BLOCK_END_LINE]
    assert in_block, (
        f"is_retryable_rate_limit import found only at lines {import_lines}, "
        f"all of which are >= {_IMPORT_BLOCK_END_LINE} (mid-file). "
        "It must be hoisted to the top-of-file import block."
    )


def test_ac1_no_mid_file_occurrence():
    """AC1 (negative): The api_client import must NOT appear mid-file (>= line 370)."""
    lines = _read_lines()
    import_lines = _find_api_client_import_lines(lines)

    mid_file = [ln for ln in import_lines if ln >= _IMPORT_BLOCK_END_LINE]
    assert not mid_file, (
        f"api_client/is_retryable_rate_limit import still present mid-file at lines "
        f"{mid_file}. Remove the mid-file occurrence after hoisting."
    )


def test_ac2_no_noqa_e402_on_hoisted_line():
    """AC2: The hoisted `from services.sprint_manager.api_client import (` line
    must not carry a # noqa: E402 suppressor."""
    lines = _read_lines()

    for i, line in enumerate(lines, start=1):
        if i >= _IMPORT_BLOCK_END_LINE:
            break
        # The `from … import (` opener is the line that carried # noqa: E402
        if "from services.sprint_manager.api_client import" in line:
            assert "noqa: E402" not in line, (
                f"Line {i} still has '# noqa: E402': {line!r}. "
                "Remove the suppressor now that the import is in the top block."
            )


def test_ac4_module_imports_without_error():
    """AC4: `import services.sprint_manager.sprint_manager` must succeed."""
    result = subprocess.run(
        [sys.executable, "-c", "import services.sprint_manager.sprint_manager"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Module import failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_ac5_no_other_imports_moved():
    """AC5: The only change to import positions is the api_client line.
    Verify key imports that were in the top block remain in the top block."""
    lines = _read_lines()
    expected_top_imports = [
        ("services.sprint_manager.pipeline", "pipeline_mode_enabled"),
        ("services.sprint_manager.config", "SprintConfig"),
        ("services.sprint_manager.failures", "FailureCategory"),
        ("services.sprint_manager.post_sprint", "DEFAULT_REVIEWER_PROMPT"),
    ]
    for module, symbol in expected_top_imports:
        found = any(
            module in line or symbol in line
            for line in lines[: _IMPORT_BLOCK_END_LINE - 1]
        )
        assert found, (
            f"Expected import of '{symbol}' from '{module}' to remain in top "
            f"block (first {_IMPORT_BLOCK_END_LINE} lines) but it was not found there."
        )

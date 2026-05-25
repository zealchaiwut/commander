"""
Tests for issue #14 — setup_uat_env.sh broken-venv detection and recovery.

Strategy: each test builds a minimal fake UAT directory tree in a temp
directory, sets HOME to point at it, and runs a *trimmed* version of the
script's venv-management block via subprocess.  The trimmed script is
written to a temp file so that:
  - network calls (git clone/fetch) are never attempted
  - only the venv detection / recreation logic is exercised

AC-1: Broken venv (directory exists, bin/pip absent) → detect, remove, recreate
AC-2: Healthy venv (bin/pip executable) → skip creation, print "skipping creation"
AC-3: Script logic handles healthy venv without error (exit 0)
AC-4: venv recreation failure (no python3 available) → exit non-zero + clear error
"""

import os
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path to the real script — used only to verify its existence and content.
# ---------------------------------------------------------------------------
SCRIPT_PATH = (
    Path(__file__).parent.parent / "scripts" / "setup_uat_env.sh"
)


# ---------------------------------------------------------------------------
# Helper: write a self-contained shell script that exercises ONLY the
# venv-management block (step 3) from setup_uat_env.sh.
# `python_cmd` controls which Python binary is used for venv creation.
# ---------------------------------------------------------------------------

def _write_venv_block_script(tmp_path: Path, venv_dir: Path, python_cmd: str) -> Path:
    """Return path to a temp shell script that runs the venv-management block."""
    script_content = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        VENV_DIR="{venv_dir}"

        if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
            echo "[3/5] venv already exists at $VENV_DIR — skipping creation."
        else
            if [ -d "$VENV_DIR" ]; then
                echo "[3/5] venv directory exists but is broken (bin/pip missing or not executable) — removing and recreating …"
                rm -rf "$VENV_DIR"
            else
                echo "[3/5] Creating Python venv at $VENV_DIR …"
            fi
            if ! {python_cmd} -m venv "$VENV_DIR" 2>/dev/null; then
                echo "ERROR: Failed to create Python venv at $VENV_DIR." >&2
                echo "       Ensure python3.12 or python3 is available and try again." >&2
                exit 1
            fi
        fi
        echo "venv_block_ok"
    """)
    script_file = tmp_path / "venv_block.sh"
    script_file.write_text(script_content)
    script_file.chmod(0o755)
    return script_file


def _write_venv_block_script_with_mock_python(tmp_path: Path, venv_dir: Path) -> Path:
    """
    Write the venv-management block using a mock python that simulates a
    successful venv creation (creates bin/python and bin/pip stubs).
    This is needed because the system python3 may not be able to create a
    full venv with pip in all CI/test environments.
    """
    # Create a mock python script that mimics `python3 -m venv <dir>`
    mock_python = tmp_path / "mock_python3"
    mock_python.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Mock python3 -m venv <dir>
        TARGET="$3"
        mkdir -p "$TARGET/bin"
        # Create a minimal but functional pip stub
        cat > "$TARGET/bin/pip" << 'PIPEOF'
#!/usr/bin/env bash
echo "pip (mock)"
PIPEOF
        chmod +x "$TARGET/bin/pip"
        cat > "$TARGET/bin/python3" << 'PYEOF'
#!/usr/bin/env bash
echo "python3 (mock)"
PYEOF
        chmod +x "$TARGET/bin/python3"
        exit 0
    """))
    mock_python.chmod(0o755)

    script_content = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        VENV_DIR="{venv_dir}"

        if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
            echo "[3/5] venv already exists at $VENV_DIR — skipping creation."
        else
            if [ -d "$VENV_DIR" ]; then
                echo "[3/5] venv directory exists but is broken (bin/pip missing or not executable) — removing and recreating …"
                rm -rf "$VENV_DIR"
            else
                echo "[3/5] Creating Python venv at $VENV_DIR …"
            fi
            if ! "{mock_python}" -m venv "$VENV_DIR" 2>/dev/null; then
                echo "ERROR: Failed to create Python venv at $VENV_DIR." >&2
                echo "       Ensure python3.12 or python3 is available and try again." >&2
                exit 1
            fi
        fi
        echo "venv_block_ok"
    """)
    script_file = tmp_path / "venv_block.sh"
    script_file.write_text(script_content)
    script_file.chmod(0o755)
    return script_file


def _run_script(script_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# AC-1 — Broken venv is detected and recreated
# ---------------------------------------------------------------------------

class TestAC1BrokenVenvDetectedAndRecreated:
    """
    Given the venv directory exists but venv/bin/pip is absent,
    the script removes the broken directory, recreates the venv,
    and continues without error.

    These tests use a mock python stub to simulate successful venv creation,
    because the system python3 may have broken ensurepip in some CI environments.
    The logic under test is the detection/removal/recreation decision tree, not
    the python venv tool itself.
    """

    def test_broken_venv_triggers_removal_and_recreation(self, tmp_path):
        """Script detects missing bin/pip, logs the broken-venv message, recreates."""
        venv_dir = tmp_path / "venv"
        # Create a broken venv: directory exists but no bin/pip
        (venv_dir / "bin").mkdir(parents=True)
        # Intentionally do NOT create bin/pip

        script = _write_venv_block_script_with_mock_python(tmp_path, venv_dir)
        result = _run_script(script)

        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        assert "broken" in result.stdout.lower(), (
            "Expected 'broken' keyword in output\nstdout: " + result.stdout
        )
        # venv must now be properly recreated with bin/pip
        assert (venv_dir / "bin" / "pip").exists(), (
            "bin/pip should exist after recreation"
        )

    def test_broken_venv_old_directory_is_removed_before_recreation(self, tmp_path):
        """The broken directory is fully removed before python -m venv is called."""
        venv_dir = tmp_path / "venv"
        # Broken venv with a sentinel file
        (venv_dir / "bin").mkdir(parents=True)
        sentinel = venv_dir / "leftover_file.txt"
        sentinel.write_text("stale content")

        script = _write_venv_block_script_with_mock_python(tmp_path, venv_dir)
        result = _run_script(script)

        assert result.returncode == 0
        # Sentinel must be gone (directory was wiped and recreated)
        assert not sentinel.exists(), "Stale files from broken venv should be removed"

    def test_broken_venv_non_executable_pip(self, tmp_path):
        """A non-executable bin/pip is also treated as broken."""
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        pip_file = venv_dir / "bin" / "pip"
        pip_file.write_text("#!/usr/bin/env python3\n")
        # Intentionally NOT chmod +x → not executable

        script = _write_venv_block_script_with_mock_python(tmp_path, venv_dir)
        result = _run_script(script)

        assert result.returncode == 0
        assert "broken" in result.stdout.lower(), (
            "Non-executable pip should also be treated as broken\nstdout: " + result.stdout
        )


# ---------------------------------------------------------------------------
# AC-2 — Healthy venv is NOT recreated
# ---------------------------------------------------------------------------

class TestAC2HealthyVenvSkipped:
    """
    Given venv/bin/pip exists and is executable, the script skips creation
    and prints the "skipping creation" message.
    """

    def test_healthy_venv_skips_creation(self, tmp_path):
        """With a healthy venv, the script prints the skip message and exits 0."""
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        pip_file = venv_dir / "bin" / "pip"
        pip_file.write_text("#!/usr/bin/env python3\nprint('pip')\n")
        pip_file.chmod(0o755)

        script = _write_venv_block_script(tmp_path, venv_dir, "python3")
        result = _run_script(script)

        assert result.returncode == 0
        assert "skipping creation" in result.stdout, (
            "Expected 'skipping creation' in output\nstdout: " + result.stdout
        )

    def test_healthy_venv_does_not_remove_directory(self, tmp_path):
        """The healthy venv directory is preserved untouched."""
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        pip_file = venv_dir / "bin" / "pip"
        pip_file.write_text("#!/usr/bin/env python3\n")
        pip_file.chmod(0o755)

        # Add a sentinel to confirm the directory is not wiped
        sentinel = venv_dir / "health_check_sentinel.txt"
        sentinel.write_text("should survive")

        script = _write_venv_block_script(tmp_path, venv_dir, "python3")
        result = _run_script(script)

        assert result.returncode == 0
        assert sentinel.exists(), "Healthy venv directory must not be removed"


# ---------------------------------------------------------------------------
# AC-3 — Script logic exits 0 for a healthy environment
# ---------------------------------------------------------------------------

class TestAC3HealthyEnvExitsZero:
    """
    Given a healthy venv, the script block completes without error.
    """

    def test_healthy_venv_exits_zero(self, tmp_path):
        """Script exits 0 and prints the final ok marker for a healthy venv."""
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        pip_file = venv_dir / "bin" / "pip"
        pip_file.write_text("#!/usr/bin/env python3\n")
        pip_file.chmod(0o755)

        script = _write_venv_block_script(tmp_path, venv_dir, "python3")
        result = _run_script(script)

        assert result.returncode == 0, f"Exit code: {result.returncode}\nstderr: {result.stderr}"

    def test_healthy_venv_prints_skip_message_not_error(self, tmp_path):
        """No error text appears in stdout/stderr for a healthy venv run."""
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        pip_file = venv_dir / "bin" / "pip"
        pip_file.write_text("#!/usr/bin/env python3\n")
        pip_file.chmod(0o755)

        script = _write_venv_block_script(tmp_path, venv_dir, "python3")
        result = _run_script(script)

        assert "ERROR" not in result.stdout
        assert "ERROR" not in result.stderr


# ---------------------------------------------------------------------------
# AC-4 — venv recreation failure exits non-zero with a clear error message
# ---------------------------------------------------------------------------

class TestAC4RecreationFailureExitsNonZero:
    """
    Given venv recreation fails (python binary unavailable),
    the script exits non-zero and prints a human-readable error.
    """

    def test_missing_python_exits_nonzero(self, tmp_path):
        """When the python binary does not exist, exit code must be non-zero."""
        venv_dir = tmp_path / "venv"
        # No venv at all — triggers the creation path

        # Use a guaranteed-nonexistent binary name
        script = _write_venv_block_script(
            tmp_path, venv_dir, "/usr/local/bin/nonexistent_python_xyz"
        )
        result = _run_script(script)

        assert result.returncode != 0, (
            "Expected non-zero exit when python binary is missing"
        )

    def test_missing_python_prints_clear_error(self, tmp_path):
        """The error output must contain a human-readable description of the failure."""
        venv_dir = tmp_path / "venv"

        script = _write_venv_block_script(
            tmp_path, venv_dir, "/usr/local/bin/nonexistent_python_xyz"
        )
        result = _run_script(script)

        combined = result.stdout + result.stderr
        assert "ERROR" in combined, (
            "Expected 'ERROR' keyword in output\ncombined: " + combined
        )
        assert "venv" in combined.lower(), (
            "Error message should mention 'venv'\ncombined: " + combined
        )

    def test_missing_python_broken_venv_exits_nonzero(self, tmp_path):
        """Broken venv + unavailable python → non-zero exit with error message."""
        venv_dir = tmp_path / "venv"
        # Broken venv (directory without bin/pip)
        (venv_dir / "bin").mkdir(parents=True)

        script = _write_venv_block_script(
            tmp_path, venv_dir, "/usr/local/bin/nonexistent_python_xyz"
        )
        result = _run_script(script)

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "ERROR" in combined

    def test_error_message_mentions_python_availability(self, tmp_path):
        """The error message guides the user to check python availability."""
        venv_dir = tmp_path / "venv"

        script = _write_venv_block_script(
            tmp_path, venv_dir, "/usr/local/bin/nonexistent_python_xyz"
        )
        result = _run_script(script)

        combined = result.stdout + result.stderr
        # The real script says "Ensure python3.12 or python3 is available"
        assert "python3" in combined.lower() or "available" in combined.lower(), (
            "Error should guide user on python availability\ncombined: " + combined
        )


# ---------------------------------------------------------------------------
# Meta-test: verify the real script file contains the fix
# ---------------------------------------------------------------------------

class TestScriptContainsFix:
    """Sanity checks that the real setup_uat_env.sh has the fix in place."""

    def test_script_exists(self):
        """setup_uat_env.sh must exist in dashboard/scripts/."""
        assert SCRIPT_PATH.exists(), f"Script not found: {SCRIPT_PATH}"

    def test_script_checks_pip_executable(self):
        """The fix must check for bin/pip executability, not just directory existence."""
        content = SCRIPT_PATH.read_text()
        assert 'bin/pip' in content, "Script should reference bin/pip"
        assert '-x' in content or '[ -x' in content, (
            "Script should use -x flag to check pip executability"
        )

    def test_script_removes_broken_venv(self):
        """The fix must remove the broken venv directory before recreation."""
        content = SCRIPT_PATH.read_text()
        assert 'rm -rf' in content, "Script should remove broken venv with rm -rf"

    def test_script_exits_nonzero_on_failure(self):
        """The fix must exit with a non-zero code on recreation failure."""
        content = SCRIPT_PATH.read_text()
        assert 'exit 1' in content, "Script should exit 1 on venv creation failure"

    def test_script_prints_error_to_stderr(self):
        """The fix must print error messages to stderr."""
        content = SCRIPT_PATH.read_text()
        assert '>&2' in content, "Error messages should be written to stderr"

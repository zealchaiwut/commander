"""Tests for issue #1312 — _get_env_val must not treat commented-out values as already-set.

Acceptance criteria:

  AC1 — _get_env_val returns a value only when the matching KEY=value line is
        uncommented (no leading #).
  AC2 — A commented-out line with a real value (# GH_TOKEN=oldtoken) is NOT
        returned as the effective value; _get_env_val returns empty for that key.
  AC3 — A commented-out placeholder line (# GH_TOKEN=) is still recognised as
        needing a prompt (_is_placeholder returns true / prompt is shown).
  AC4 — An uncommented placeholder line (GH_TOKEN=) still triggers the setup
        prompt as before.
  AC5 — An uncommented line with a real value (GH_TOKEN=realtoken) still
        suppresses the prompt and is returned as the effective value.
  AC6 — No regression: setup_env correctly skips prompts only when an
        uncommented, non-placeholder value exists in the active .env.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "setup_machine.sh"


def _run_get_env_val(env_content: str, key: str) -> str:
    """Source _get_env_val from setup_machine.sh and call it with a temp .env file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, dir="/tmp"
    ) as f:
        f.write(env_content)
        env_file = f.name

    # Extract the function to a temp file so heredoc sourcing works correctly
    # (process substitution `source <(...)` conflicts with heredocs in bash).
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, dir="/tmp"
    ) as fsh:
        result_extract = subprocess.run(
            ["sed", "-n", r"/^_get_env_val()/,/^}$/p", str(_SCRIPT)],
            capture_output=True,
            text=True,
        )
        fsh.write(result_extract.stdout)
        func_file = fsh.name

    try:
        bash_code = f'source "{func_file}"; _get_env_val "{env_file}" "{key}"'
        result = subprocess.run(
            ["bash", "-c", bash_code],
            capture_output=True,
            text=True,
        )
        return result.stdout
    finally:
        os.unlink(env_file)
        os.unlink(func_file)


def _is_placeholder(val: str) -> bool:
    """Python mirror of _is_placeholder in setup_machine.sh."""
    if not val:
        return True
    if val.startswith("<") and val.endswith(">"):
        return True
    return False


# ── AC1: uncommented KEY=value returns the value ─────────────────────────────

def test_ac1_uncommented_real_value_is_returned():
    """_get_env_val returns the value for an uncommented KEY=value line."""
    result = _run_get_env_val("GH_TOKEN=realtoken\n", "GH_TOKEN")
    assert result == "realtoken"


# ── AC2: commented-out real value returns empty ───────────────────────────────

def test_ac2_commented_real_value_returns_empty():
    """# GH_TOKEN=oldtoken must NOT be returned as the effective value."""
    result = _run_get_env_val("# GH_TOKEN=oldtoken\n", "GH_TOKEN")
    assert result == "", (
        f"Expected empty string for commented-out value, got: {result!r}"
    )


def test_ac2_commented_real_value_is_placeholder():
    """setup_env must prompt when only a commented real value exists."""
    val = _run_get_env_val("# GH_TOKEN=oldtoken\n", "GH_TOKEN")
    assert _is_placeholder(val), (
        f"_is_placeholder should be true for commented-out value, val={val!r}"
    )


# ── AC3: commented placeholder returns empty (still triggers prompt) ──────────

def test_ac3_commented_placeholder_returns_empty():
    """# GH_TOKEN= (commented placeholder) should return empty."""
    result = _run_get_env_val("# GH_TOKEN=\n", "GH_TOKEN")
    assert result == ""


def test_ac3_commented_placeholder_triggers_prompt():
    """_is_placeholder must be true for a commented placeholder."""
    val = _run_get_env_val("# GH_TOKEN=\n", "GH_TOKEN")
    assert _is_placeholder(val)


# ── AC4: uncommented placeholder triggers prompt ──────────────────────────────

def test_ac4_uncommented_placeholder_returns_empty():
    """GH_TOKEN= (uncommented, empty) returns empty string."""
    result = _run_get_env_val("GH_TOKEN=\n", "GH_TOKEN")
    assert result == ""


def test_ac4_uncommented_placeholder_triggers_prompt():
    """_is_placeholder must be true for an uncommented placeholder."""
    val = _run_get_env_val("GH_TOKEN=\n", "GH_TOKEN")
    assert _is_placeholder(val)


# ── AC5: uncommented real value suppresses prompt ─────────────────────────────

def test_ac5_uncommented_real_value_suppresses_prompt():
    """GH_TOKEN=realtoken → _get_env_val returns realtoken → prompt skipped."""
    val = _run_get_env_val("GH_TOKEN=realtoken\n", "GH_TOKEN")
    assert val == "realtoken"
    assert not _is_placeholder(val)


# ── AC6: no regression — mixed .env file ─────────────────────────────────────

def test_ac6_mixed_env_commented_before_uncommented():
    """When both commented and uncommented lines exist, only uncommented wins."""
    env_content = "# GH_TOKEN=oldtoken\nGH_TOKEN=newtoken\n"
    result = _run_get_env_val(env_content, "GH_TOKEN")
    assert result == "newtoken"


def test_ac6_absent_key_returns_empty():
    """A key that doesn't appear in .env at all returns empty."""
    result = _run_get_env_val("OTHER_KEY=value\n", "GH_TOKEN")
    assert result == ""


def test_ac6_angle_bracket_placeholder_triggers_prompt():
    """GH_TOKEN=<your-token-here> is treated as a placeholder."""
    val = _run_get_env_val("GH_TOKEN=<your-token-here>\n", "GH_TOKEN")
    assert val == "<your-token-here>"
    assert _is_placeholder(val)


def test_ac6_commented_with_hash_space_real_value_returns_empty():
    """'# GH_TOKEN=oldtoken' (hash then space) must also return empty."""
    result = _run_get_env_val("# GH_TOKEN=oldtoken\n", "GH_TOKEN")
    assert result == ""

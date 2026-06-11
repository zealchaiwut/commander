"""Tests for issue #827 — wire CLAUDE_CODE_OAUTH_TOKEN into the launchd plist
for fully headless agent dispatch (alongside the GH_TOKEN wiring from #772).

A launchd service is detached from the login session: it cannot read the login
keychain (where `gh` stores creds) nor the shell rc files (where `claude`
subscription auth lives). Supplying CLAUDE_CODE_OAUTH_TOKEN and GH_TOKEN via the
plist EnvironmentVariables block lets sprints dispatched by the launchd-run
dashboard authenticate both tools with no interactive session.

AC coverage (code-testable subset; the physical Mac-mini launchctl / sprint
dispatch step — AC5 — is covered by the manual UAT steps in the issue):

  AC1 — installer accepts CLAUDE_CODE_OAUTH_TOKEN via --claude-token (and the
        $CLAUDE_CODE_OAUTH_TOKEN env var); interactive prompt uses no-echo input
  AC2 — provided token lands in the plist EnvironmentVariables block and is
        never echoed to stdout/stderr or hard-coded in version control
  AC3 — an omitted token produces a named warning: missing CLAUDE_CODE_OAUTH_TOKEN
        breaks agent dispatch; missing GH_TOKEN breaks gh API calls
  AC4 — comments / doc explain how to obtain each token (claude setup-token for
        CLAUDE_CODE_OAUTH_TOKEN; gh auth token for GH_TOKEN)
  AC6 — the plist file is written with 600 (owner read/write only) permissions
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_launchd.sh"

sys.path.insert(0, str(REPO_ROOT))

_FAKE_CLAUDE_TOKEN = "sk-ant-oat01-TEST0000claude0000token0000value00000000"
_FAKE_GH_TOKEN = "ghp_TEST0000gh0000token0000value0000000000"


# ── helpers ───────────────────────────────────────────────────────────────────


def _clean_env(**overrides):
    """A copy of os.environ with both token env vars stripped, plus overrides."""
    env = dict(os.environ)
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        env.pop(key, None)
    env.update(overrides)
    return env


def _run(*args, env=None):
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env
    )
    return proc


def _render_plist(env=None, **params):
    """Invoke the installer in --print-plist mode; return (proc, parsed_dict).

    --print-plist renders to stdout and exits 0 without touching launchctl or
    any file, so it asserts on exactly the plist that installs.
    """
    args = ["--print-plist"]
    for key, val in params.items():
        args += [f"--{key.replace('_', '-')}", str(val)]
    proc = _run(*args, env=env if env is not None else _clean_env())
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    return proc, plistlib.loads(proc.stdout.encode("utf-8"))


# ── AC1: CLAUDE_CODE_OAUTH_TOKEN accepted via flag and env var ─────────────────


def test_claude_token_arg_set_in_plist_environment_block():
    """AC1: --claude-token writes CLAUDE_CODE_OAUTH_TOKEN into EnvironmentVariables."""
    _, data = _render_plist(claude_token=_FAKE_CLAUDE_TOKEN)
    assert data["EnvironmentVariables"]["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN


def test_claude_token_key_inside_environment_variables():
    """AC1: the <key>CLAUDE_CODE_OAUTH_TOKEN</key> entry sits inside the
    EnvironmentVariables dict, not elsewhere in the plist."""
    proc, _ = _render_plist(claude_token=_FAKE_CLAUDE_TOKEN)
    out = proc.stdout
    env_start = out.index("<key>EnvironmentVariables</key>")
    assert out.index("<key>CLAUDE_CODE_OAUTH_TOKEN</key>") > env_start


def test_claude_token_read_from_env_var():
    """AC1: when --claude-token is omitted, the installer falls back to
    $CLAUDE_CODE_OAUTH_TOKEN."""
    env = _clean_env(CLAUDE_CODE_OAUTH_TOKEN=_FAKE_CLAUDE_TOKEN)
    _, data = _render_plist(env=env)
    assert data["EnvironmentVariables"]["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN


def test_explicit_claude_arg_overrides_env_var():
    """AC1: an explicit --claude-token wins over an ambient env token."""
    env = _clean_env(CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-AMBIENT-should-not-win")
    _, data = _render_plist(env=env, claude_token=_FAKE_CLAUDE_TOKEN)
    assert data["EnvironmentVariables"]["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN


def test_interactive_prompt_hides_input():
    """AC1: the interactive token prompt reads with no echo (read -s)."""
    text = SCRIPT.read_text()
    # `read -s` (or -rs) suppresses terminal echo of the typed token.
    assert "read -rs" in text or "read -s" in text


# ── AC2: token in plist, never printed, never hard-coded ──────────────────────


def test_both_tokens_reach_plist_from_flags():
    """AC2: a single pair of flag inputs populates both keys in the plist."""
    _, data = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    env_block = data["EnvironmentVariables"]
    assert env_block["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN
    assert env_block["GH_TOKEN"] == _FAKE_GH_TOKEN


def test_no_claude_token_means_no_key_in_plist():
    """AC2: with no claude token supplied, the plist carries no
    CLAUDE_CODE_OAUTH_TOKEN key — and still parses as a valid plist."""
    proc, data = _render_plist(env=_clean_env())
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in proc.stdout
    assert data["Label"] == "com.commander.dashboard"


def test_installer_script_has_no_hardcoded_token():
    """AC2: the installer script contains no literal token value."""
    text = SCRIPT.read_text()
    assert "sk-ant-oat" not in text
    assert "ghp_" not in text
    assert "github_pat_" not in text


def test_token_values_never_echoed():
    """AC2: no status echo prints a raw token *value* — only set/not-set state.
    Guards against a `echo "...$CLAUDE_TOKEN_VALUE"` leak into the logs."""
    text = SCRIPT.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("echo"):
            assert "CLAUDE_TOKEN_VALUE" not in stripped, f"token value echoed: {line!r}"
            assert "GH_TOKEN_VALUE" not in stripped, f"token value echoed: {line!r}"


# ── AC3: named warnings when a token is omitted ───────────────────────────────


def test_missing_claude_token_warns_about_dispatch():
    """AC3: omitting the claude token prints a named warning that names
    CLAUDE_CODE_OAUTH_TOKEN and says agent dispatch breaks."""
    env = _clean_env(GH_TOKEN=_FAKE_GH_TOKEN)  # gh present, claude absent
    proc, _ = _render_plist(env=env)
    err = proc.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" in err
    assert "dispatch" in err.lower()


def test_missing_gh_token_warns_about_gh_api():
    """AC3: omitting the gh token prints a named warning that names GH_TOKEN
    and says gh API calls break."""
    env = _clean_env(CLAUDE_CODE_OAUTH_TOKEN=_FAKE_CLAUDE_TOKEN)  # claude present
    proc, _ = _render_plist(env=env)
    err = proc.stderr
    assert "GH_TOKEN" in err
    assert "gh" in err.lower()


def test_both_tokens_present_no_token_warning():
    """AC3: when both tokens are supplied, no missing-token warning appears."""
    proc, _ = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    err = proc.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in err
    # GH_TOKEN may appear in unrelated text; assert no WARNING line names it.
    for line in err.splitlines():
        if "WARNING" in line:
            raise AssertionError(f"unexpected token warning: {line!r}")


# ── AC4: docs/comments explain how to obtain each token ───────────────────────


def test_script_documents_how_to_obtain_tokens():
    """AC4: the installer explains the commands that mint each token."""
    text = SCRIPT.read_text()
    assert "claude setup-token" in text
    assert "gh auth token" in text


# ── AC6: plist written with 600 permissions ───────────────────────────────────


def test_plist_chmod_is_600():
    """AC6: the installed plist is locked to owner read/write (600), never 644."""
    text = SCRIPT.read_text()
    assert 'chmod 600 "$PLIST_DEST"' in text
    assert 'chmod 644 "$PLIST_DEST"' not in text

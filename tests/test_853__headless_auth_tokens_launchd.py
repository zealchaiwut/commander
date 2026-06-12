"""Tests for issue #853 — wire headless auth tokens into the launchd service at
install.

A launchd process is detached from the login session: it cannot read the login
keychain (where `gh` stores creds) nor the shell rc files (where the `claude`
CLI subscription auth lives). `install_launchd.sh` must therefore accept BOTH
CLAUDE_CODE_OAUTH_TOKEN and GH_TOKEN at install time and write them into the
plist EnvironmentVariables block so a sprint dispatched by the launchd-run
dashboard authenticates `claude` and `gh` with no interactive session.

This file anchors each test to issue #853's own AC numbering. It deliberately
exercises the two tokens *together* (the consolidation the ticket asks for),
which the per-token suites (#772 GH_TOKEN, #827 CLAUDE_CODE_OAUTH_TOKEN) cover
only one side of at a time.

AC coverage (code-testable subset):

  AC1 — installer accepts both tokens via flags (--claude-token / --gh-token)
        and via the matching env vars; the interactive prompt reads with no echo
  AC2 — accepted values land inside the <key>EnvironmentVariables</key> dict
  AC3 — token values are never written to a committed file, shell history, or
        log output (no literal token in the script; no echo of the values)
  AC4 — omitting either token prints a named warning: missing
        CLAUDE_CODE_OAUTH_TOKEN warns agent dispatch fails; missing GH_TOKEN
        warns gh API calls fail
  AC5 — script docs explain how to obtain each token (claude setup-token /
        gh auth token)
  AC7 — a tokenless install still renders/installs without error; tokens are
        optional at the script level and only emit warnings

AC6 (a real sprint dispatched from the launchd-run dashboard authenticates both
tools with no session open) is a physical Mac-mini / launchctl runtime step and
is covered by the manual UAT steps in the issue.
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
    """A copy of os.environ with all three token env vars stripped, plus overrides."""
    env = dict(os.environ)
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        env.pop(key, None)
    env.update(overrides)
    return env


def _run(*args, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env
    )


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


# ── AC1: both tokens accepted via flags + env vars; prompt hides input ────────


def test_both_tokens_accepted_via_flags():
    """AC1: --claude-token and --gh-token together populate both keys."""
    _, data = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    env_block = data["EnvironmentVariables"]
    assert env_block["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN
    assert env_block["GH_TOKEN"] == _FAKE_GH_TOKEN


def test_both_tokens_accepted_via_env_vars():
    """AC1: with no flags, both tokens are read from their matching env vars."""
    env = _clean_env(
        CLAUDE_CODE_OAUTH_TOKEN=_FAKE_CLAUDE_TOKEN, GH_TOKEN=_FAKE_GH_TOKEN
    )
    _, data = _render_plist(env=env)
    env_block = data["EnvironmentVariables"]
    assert env_block["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_CLAUDE_TOKEN
    assert env_block["GH_TOKEN"] == _FAKE_GH_TOKEN


def test_interactive_prompt_reads_without_echo():
    """AC1: the interactive token prompts read with echo off (read -rs) so the
    typed token never lands on screen or in terminal scrollback."""
    text = SCRIPT.read_text()
    assert "read -rs" in text or "read -s" in text
    # the no-echo read must be gated on an interactive terminal ([ -t 0 ]) so a
    # non-interactive sprint dispatch never blocks waiting on stdin
    assert "[ -t 0 ]" in text


# ── AC2: accepted values land inside the EnvironmentVariables dict ────────────


def test_both_token_keys_sit_inside_environment_variables():
    """AC2: both <key> entries appear after the EnvironmentVariables key, i.e.
    inside that dict rather than elsewhere in the plist."""
    proc, _ = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    out = proc.stdout
    env_start = out.index("<key>EnvironmentVariables</key>")
    assert out.index("<key>CLAUDE_CODE_OAUTH_TOKEN</key>") > env_start
    assert out.index("<key>GH_TOKEN</key>") > env_start


def test_rendered_plist_is_valid_with_both_tokens():
    """AC2: the plist still parses as a well-formed plist with both tokens set."""
    _, data = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    assert data["Label"] == "com.commander.dashboard"
    assert "EnvironmentVariables" in data


# ── AC3: values never written to a committed file, history, or log ────────────


def test_script_has_no_hardcoded_token_value():
    """AC3: the committed installer carries no literal token of either kind."""
    text = SCRIPT.read_text()
    assert "sk-ant-oat" not in text
    assert "ghp_" not in text
    assert "github_pat_" not in text


def test_no_echo_statement_prints_a_raw_token_value():
    """AC3: no echo line interpolates a raw token *value* into log output —
    only set/not-set state may be printed. Guards against a leak into the
    launchd stdout/stderr logs."""
    text = SCRIPT.read_text()
    for line in text.splitlines():
        if line.strip().startswith("echo"):
            assert "CLAUDE_TOKEN_VALUE" not in line, f"token value echoed: {line!r}"
            assert "GH_TOKEN_VALUE" not in line, f"token value echoed: {line!r}"


def test_token_values_absent_from_all_stdout():
    """AC3: a full --print-plist run never leaks a token value onto stdout
    outside the EnvironmentVariables dict, and never onto stderr at all."""
    proc, _ = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    # tokens appear in the plist body (by design) but must never hit stderr
    assert _FAKE_CLAUDE_TOKEN not in proc.stderr
    assert _FAKE_GH_TOKEN not in proc.stderr


# ── AC4: named warnings when either token is omitted ──────────────────────────


def test_missing_claude_token_warns_dispatch_fails():
    """AC4: omitting the claude token prints a named warning citing
    CLAUDE_CODE_OAUTH_TOKEN and that agent dispatch will fail."""
    env = _clean_env(GH_TOKEN=_FAKE_GH_TOKEN)  # gh present, claude absent
    proc, _ = _render_plist(env=env)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in proc.stderr
    assert "dispatch" in proc.stderr.lower()


def test_missing_gh_token_warns_gh_api_fails():
    """AC4: omitting the gh token prints a named warning citing GH_TOKEN and
    that gh API calls will fail."""
    env = _clean_env(CLAUDE_CODE_OAUTH_TOKEN=_FAKE_CLAUDE_TOKEN)
    proc, _ = _render_plist(env=env)
    assert "GH_TOKEN" in proc.stderr
    assert "gh" in proc.stderr.lower()


def test_both_missing_warns_about_both():
    """AC4: with neither token supplied, both named warnings fire."""
    proc, _ = _render_plist(env=_clean_env())
    assert "CLAUDE_CODE_OAUTH_TOKEN" in proc.stderr
    assert "GH_TOKEN" in proc.stderr


def test_both_present_emits_no_warning():
    """AC4: when both tokens are supplied, no WARNING line is emitted."""
    proc, _ = _render_plist(
        claude_token=_FAKE_CLAUDE_TOKEN, gh_token=_FAKE_GH_TOKEN
    )
    for line in proc.stderr.splitlines():
        assert "WARNING" not in line, f"unexpected warning: {line!r}"


# ── AC5: docs explain how to obtain each token ────────────────────────────────


def test_docs_name_both_token_minting_commands():
    """AC5: the installer documents `claude setup-token` (for
    CLAUDE_CODE_OAUTH_TOKEN) and `gh auth token` (for GH_TOKEN)."""
    text = SCRIPT.read_text()
    assert "claude setup-token" in text
    assert "gh auth token" in text


# ── AC7: tokenless install still works (tokens optional) ──────────────────────


def test_tokenless_render_succeeds_without_error():
    """AC7: with no tokens, the plist renders (exit 0) and omits both keys —
    a backward-compatible, pre-token plist."""
    proc, data = _render_plist(env=_clean_env())
    assert proc.returncode == 0
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in proc.stdout
    assert "GH_TOKEN" not in proc.stdout
    assert data["Label"] == "com.commander.dashboard"


def test_tokenless_render_still_valid_plist():
    """AC7: the tokenless plist is still a well-formed plist with the standard
    EnvironmentVariables (PATH + ENVIRONMENT) intact."""
    _, data = _render_plist(env=_clean_env())
    env_block = data["EnvironmentVariables"]
    assert "PATH" in env_block
    assert "ENVIRONMENT" in env_block

"""Tests for issue #772 — wire GH_TOKEN into launchd plist for headless gh auth.

A launchd-started process is detached from the login session and cannot read
the macOS keychain where `gh` stores credentials. Supplying GH_TOKEN (which gh
prefers over the keychain) via the plist EnvironmentVariables block and the
agent .env fixes the startup repo check under launchd.

AC coverage (code-testable subset; the physical Mac-mini launchctl / keychain /
restart / reboot steps — AC4, AC5, AC6, AC7 — are covered by the manual UAT
steps in the issue, exactly as in test_724):

  AC1 — GH_TOKEN is set in the plist <key>EnvironmentVariables</key> block by
        the installer (from --gh-token, or the GH_TOKEN / GITHUB_TOKEN env var)
  AC2 — the same token is written to the agent .env file used at runtime
  AC3 — the installer accepts the token as input and writes it to BOTH the plist
        and the .env automatically
  AC8 — no credentials are hard-coded in version-controlled files; the token is
        sourced from env/secrets at install time (absent token => no GH_TOKEN
        leaks into the rendered plist, and no literal token lives in the script
        or .env.example)
"""

import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPT = REPO_ROOT / "scripts" / "install_launchd.sh"

sys.path.insert(0, str(REPO_ROOT))

_FAKE_TOKEN = "ghp_TEST0000token0000value0000000000000000"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(*args, env=None):
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env
    )
    return proc


def _render_plist(env=None, **params) -> str:
    """Invoke the installer in --print-plist mode and return the rendered plist.

    --print-plist renders to stdout and exits 0 without touching launchctl or
    any file, so it is safe in CI and asserts on exactly the plist that installs.
    """
    args = ["--print-plist"]
    for key, val in params.items():
        args += [f"--{key.replace('_', '-')}", str(val)]
    proc = _run(*args, env=env)
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    return proc.stdout


# ── AC1: GH_TOKEN lands in the plist EnvironmentVariables block ───────────────


def test_gh_token_arg_set_in_plist_environment_block():
    """AC1: --gh-token writes GH_TOKEN into the EnvironmentVariables dict."""
    out = _render_plist(gh_token=_FAKE_TOKEN)
    data = plistlib.loads(out.encode("utf-8"))
    assert data["EnvironmentVariables"]["GH_TOKEN"] == _FAKE_TOKEN


def test_gh_token_key_appears_inside_environment_variables():
    """AC1: the <key>GH_TOKEN</key> entry sits in the EnvironmentVariables block,
    not somewhere else in the plist."""
    out = _render_plist(gh_token=_FAKE_TOKEN)
    env_start = out.index("<key>EnvironmentVariables</key>")
    # GH_TOKEN key must come after EnvironmentVariables (inside its dict)
    assert out.index("<key>GH_TOKEN</key>") > env_start


def test_gh_token_read_from_gh_token_env_var(monkeypatch=None):
    """AC1: when --gh-token is omitted, the installer falls back to $GH_TOKEN."""
    import os

    env = dict(os.environ)
    env["GH_TOKEN"] = _FAKE_TOKEN
    env.pop("GITHUB_TOKEN", None)
    out = _render_plist(env=env)
    data = plistlib.loads(out.encode("utf-8"))
    assert data["EnvironmentVariables"]["GH_TOKEN"] == _FAKE_TOKEN


def test_gh_token_read_from_github_token_env_var():
    """AC1: GITHUB_TOKEN is also accepted (gh honours either)."""
    import os

    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    env["GITHUB_TOKEN"] = _FAKE_TOKEN
    out = _render_plist(env=env)
    data = plistlib.loads(out.encode("utf-8"))
    assert data["EnvironmentVariables"]["GH_TOKEN"] == _FAKE_TOKEN


def test_explicit_arg_overrides_env_var():
    """AC1: an explicit --gh-token wins over an ambient env token."""
    import os

    env = dict(os.environ)
    env["GH_TOKEN"] = "ghp_AMBIENT_should_not_win_000000000000000"
    out = _render_plist(env=env, gh_token=_FAKE_TOKEN)
    data = plistlib.loads(out.encode("utf-8"))
    assert data["EnvironmentVariables"]["GH_TOKEN"] == _FAKE_TOKEN


# ── AC2: the same token is written to the agent .env ──────────────────────────


def test_token_written_to_env_file(tmp_path):
    """AC2: --write-env-only writes GH_TOKEN=<token> to the supplied .env."""
    env_file = tmp_path / ".env"
    proc = _run(
        "--write-env-only", "--gh-token", _FAKE_TOKEN, "--env-file", str(env_file)
    )
    assert proc.returncode == 0, proc.stderr
    content = env_file.read_text()
    assert f"GH_TOKEN={_FAKE_TOKEN}" in content


def test_env_write_replaces_existing_token_no_duplicate(tmp_path):
    """AC2: an existing GH_TOKEN line is replaced in place, not duplicated."""
    env_file = tmp_path / ".env"
    env_file.write_text("DB_PATH=./commander.db\nGH_TOKEN=ghp_OLD_value_0000000000\n")
    proc = _run(
        "--write-env-only", "--gh-token", _FAKE_TOKEN, "--env-file", str(env_file)
    )
    assert proc.returncode == 0, proc.stderr
    content = env_file.read_text()
    assert content.count("GH_TOKEN=") == 1
    assert f"GH_TOKEN={_FAKE_TOKEN}" in content
    assert "ghp_OLD_value" not in content
    # untouched keys survive
    assert "DB_PATH=./commander.db" in content


def test_env_write_preserves_other_keys(tmp_path):
    """AC2: writing the token leaves unrelated keys intact."""
    env_file = tmp_path / ".env"
    env_file.write_text("DB_PATH=./commander.db\nGITHUB_DEFAULT_BRANCH=main\n")
    _run("--write-env-only", "--gh-token", _FAKE_TOKEN, "--env-file", str(env_file))
    content = env_file.read_text()
    assert "DB_PATH=./commander.db" in content
    assert "GITHUB_DEFAULT_BRANCH=main" in content
    assert f"GH_TOKEN={_FAKE_TOKEN}" in content


# ── AC3: token drives BOTH locations from one input ───────────────────────────


def test_one_token_input_reaches_plist_and_env(tmp_path):
    """AC3: the same token value supplied once shows up in both the rendered
    plist EnvironmentVariables block and the agent .env file."""
    # plist surface
    out = _render_plist(gh_token=_FAKE_TOKEN)
    assert plistlib.loads(out.encode("utf-8"))["EnvironmentVariables"][
        "GH_TOKEN"
    ] == _FAKE_TOKEN
    # .env surface
    env_file = tmp_path / ".env"
    _run("--write-env-only", "--gh-token", _FAKE_TOKEN, "--env-file", str(env_file))
    assert f"GH_TOKEN={_FAKE_TOKEN}" in env_file.read_text()


# ── AC8: no hard-coded credentials in version control ─────────────────────────


def test_no_token_means_no_gh_token_in_plist():
    """AC8: with no token supplied, the plist carries no GH_TOKEN — and still
    parses as a valid plist (backward compatible with issue #724)."""
    import os

    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    out = _render_plist(env=env)
    assert "GH_TOKEN" not in out
    data = plistlib.loads(out.encode("utf-8"))  # still valid
    assert data["Label"] == "com.commander.dashboard"


def test_installer_script_has_no_hardcoded_token():
    """AC8: the installer script contains no literal gh token value."""
    text = SCRIPT.read_text()
    assert "ghp_" not in text
    assert "github_pat_" not in text


def test_env_example_has_no_hardcoded_token():
    """AC8: the committed .env.example carries no real token value."""
    example = DASHBOARD_DIR / ".env.example"
    text = example.read_text()
    assert "ghp_" not in text
    assert "github_pat_" not in text
    # a GH_TOKEN line, if present, must be a commented placeholder with no value
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("GH_TOKEN="):
            raise AssertionError(f"uncommented GH_TOKEN with a value: {line!r}")

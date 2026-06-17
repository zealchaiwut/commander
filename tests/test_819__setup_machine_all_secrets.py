"""Tests for issue #819 — setup_machine.sh prompts for all secret keys in .env.example.

AC coverage:
  AC1 — setup_env prompts for every key matching _TOKEN/_KEY/_SECRET/_PASSWORD
         found in .env.example, not just GH_TOKEN
  AC2 — each discovered secret key uses prompt_secret (value never echoed to
         stdout or stderr)
  AC3 — key with a non-placeholder value in existing .env is skipped; key with
         a placeholder value in existing .env is still prompted (idempotent re-run)
  AC4 — non-secret keys (no sensitive suffix) are not prompted; they are
         copied verbatim from .env.example
  AC5 — after setup_env completes, .env contains no unreplaced <...> placeholders
         for any secret key defined in .env.example
  AC6 — the key name is printed before each prompt so the user knows which key
         is being requested
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup_machine.sh"

# A synthetic .env.example with two secret keys and two non-secret keys.
# GH_TOKEN is commented out with no value (empty placeholder).
# ANTHROPIC_API_KEY uses an angle-bracket placeholder.
_EXAMPLE_TWO_SECRETS = """\
# Non-secret config
DB_PATH=./commander.db
PORT=8000

# Secret keys (never commit real values)
# GH_TOKEN=
ANTHROPIC_API_KEY=<your-anthropic-api-key>
"""


def _run(args, env=None, input_text=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=full_env,
        input=input_text,
    )


def _make_stub(path: Path, exit_code: int = 0):
    body = f"#!/usr/bin/env bash\nexit {exit_code}\n"
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _dry_run_env(tmp_path, env_example: str, env_content: str | None = None):
    """Return (env_dict, dash_path) for dry-run tests.

    Uses SETUP_MACHINE_DRY_RUN=1, so no side-effecting commands run.
    setup_env's prompt_secret calls print '[env] would prompt for KEY'.
    """
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(env_example)
    if env_content is not None:
        (dash / ".env").write_text(env_content)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_DRY_RUN": "1",
    }
    return env, dash


def _live_env(tmp_path, env_example: str, env_content: str | None = None):
    """Return (env_dict, dash_path) for non-dry-run tests.

    Pre-creates prd/ and uat/venv/bin/ under PROJECT_DIR and stubs
    code-review-graph so install_agent_skills.sh succeeds without network.
    SETUP_MACHINE_SKIP_PIP and SETUP_MACHINE_SKIP_NPM skip slow installs.
    """
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(env_example)
    if env_content is not None:
        (dash / ".env").write_text(env_content)

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")

    proj = tmp_path / "commander"
    (proj / "prd").mkdir(parents=True)
    crg_dir = proj / "uat" / "venv" / "bin"
    crg_dir.mkdir(parents=True)
    _make_stub(crg_dir / "code-review-graph")

    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(proj),
        "SETUP_MACHINE_SKIP_PIP": "1",
        "SETUP_MACHINE_SKIP_NPM": "1",
    }
    return env, dash


# ── AC1 ───────────────────────────────────────────────────────────────────────


def test_setup_env_prompts_all_secret_keys(tmp_path):
    """AC1: setup_env prompts for every secret key in .env.example, not just GH_TOKEN."""
    env, _ = _dry_run_env(tmp_path, _EXAMPLE_TWO_SECRETS)
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "would prompt for GH_TOKEN" in out, "GH_TOKEN not prompted"
    assert "would prompt for ANTHROPIC_API_KEY" in out, "ANTHROPIC_API_KEY not prompted"


def test_setup_env_prompts_keys_with_password_and_secret_suffix(tmp_path):
    """AC1: keys ending in _PASSWORD and _SECRET are also detected."""
    example = (
        "DB_PASSWORD=<your-db-password>\n"
        "SIGNING_SECRET=<your-signing-secret>\n"
        "NORMAL_VAR=value\n"
    )
    env, _ = _dry_run_env(tmp_path, example)
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "would prompt for DB_PASSWORD" in out
    assert "would prompt for SIGNING_SECRET" in out
    assert "would prompt for NORMAL_VAR" not in out


# ── AC2 ───────────────────────────────────────────────────────────────────────


def test_setup_env_secrets_never_echoed(tmp_path):
    """AC2: each secret key is read via prompt_secret; value never appears in output."""
    env, dash = _live_env(tmp_path, _EXAMPLE_TWO_SECRETS)
    secret_gh = "ghp_SUPERSECRET_gh_token_abc123"
    secret_ant = "sk-ant_SUPERSECRET_api_key_xyz"
    proc = _run(
        ["--setup-only"], env=env,
        input_text=f"{secret_gh}\n{secret_ant}\n",
    )
    assert proc.returncode == 0, proc.stderr
    env_text = (dash / ".env").read_text()
    assert f"GH_TOKEN={secret_gh}" in env_text
    assert f"ANTHROPIC_API_KEY={secret_ant}" in env_text
    for secret in (secret_gh, secret_ant):
        assert secret not in proc.stdout, f"secret leaked to stdout: {secret}"
        assert secret not in proc.stderr, f"secret leaked to stderr: {secret}"


# ── AC3 ───────────────────────────────────────────────────────────────────────


def test_setup_env_skips_keys_already_set(tmp_path):
    """AC3: key with a real (non-placeholder) value in .env is NOT re-prompted."""
    existing = "GH_TOKEN=already-set-real-token\n"
    env, _ = _dry_run_env(tmp_path, _EXAMPLE_TWO_SECRETS, env_content=existing)
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "would prompt for GH_TOKEN" not in out, "GH_TOKEN already set; must not re-prompt"
    assert "would prompt for ANTHROPIC_API_KEY" in out, "ANTHROPIC_API_KEY still a placeholder"


def test_setup_env_prompts_placeholder_in_existing_env(tmp_path):
    """AC3: key with a placeholder value in existing .env IS still prompted."""
    existing = "GH_TOKEN=<your-gh-token-here>\nANTHROPIC_API_KEY=<your-api-key>\n"
    env, _ = _dry_run_env(tmp_path, _EXAMPLE_TWO_SECRETS, env_content=existing)
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "would prompt for GH_TOKEN" in out, "GH_TOKEN has placeholder; must prompt"
    assert "would prompt for ANTHROPIC_API_KEY" in out, "ANTHROPIC_API_KEY has placeholder; must prompt"


# ── AC4 ───────────────────────────────────────────────────────────────────────


def test_setup_env_no_prompt_for_non_secret_keys(tmp_path):
    """AC4: non-secret keys (no _TOKEN/_KEY/_SECRET/_PASSWORD suffix) are not prompted."""
    example = "DB_PATH=./commander.db\nPORT=8000\nGH_TOKEN=<placeholder>\n"
    env, _ = _dry_run_env(tmp_path, example)
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "would prompt for DB_PATH" not in out
    assert "would prompt for PORT" not in out
    assert "would prompt for GH_TOKEN" in out


def test_setup_env_copies_non_secret_keys_verbatim(tmp_path):
    """AC4: non-secret keys appear in .env unchanged from .env.example."""
    env, dash = _live_env(tmp_path, _EXAMPLE_TWO_SECRETS)
    proc = _run(["--setup-only"], env=env, input_text="tok\nkey\n")
    assert proc.returncode == 0, proc.stderr
    env_text = (dash / ".env").read_text()
    assert "DB_PATH=./commander.db" in env_text
    assert "PORT=8000" in env_text


# ── AC5 ───────────────────────────────────────────────────────────────────────


def test_setup_env_no_placeholders_after_setup(tmp_path):
    """AC5: after setup_env, .env has no <...> placeholders for any secret key."""
    env, dash = _live_env(tmp_path, _EXAMPLE_TWO_SECRETS)
    proc = _run(["--setup-only"], env=env, input_text="real-gh-token\nreal-api-key\n")
    assert proc.returncode == 0, proc.stderr
    env_text = (dash / ".env").read_text()
    for line in env_text.splitlines():
        if "=" not in line:
            continue
        raw_key, _, val = line.partition("=")
        key = raw_key.lstrip("#").strip()
        if key in ("GH_TOKEN", "ANTHROPIC_API_KEY"):
            assert not (val.startswith("<") and val.endswith(">")), (
                f"{key} still has placeholder value: {val}"
            )


# ── AC6 ───────────────────────────────────────────────────────────────────────


def test_setup_env_prints_key_name_as_prompt_label(tmp_path):
    """AC6: the key name appears in the output before each prompt so the user
    knows which secret is being requested."""
    env, dash = _live_env(tmp_path, _EXAMPLE_TWO_SECRETS)
    proc = _run(["--setup-only"], env=env, input_text="val1\nval2\n")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "GH_TOKEN" in out, "GH_TOKEN label missing from prompt output"
    assert "ANTHROPIC_API_KEY" in out, "ANTHROPIC_API_KEY label missing from prompt output"

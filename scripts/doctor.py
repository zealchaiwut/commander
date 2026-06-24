#!/usr/bin/env python3
"""doctor.py — Pre-sprint host validation for Commander (issue #828, #854).

Before a host runs its first sprint there is no fast way to confirm that all
required tools and configuration are in place. This script checks every
prerequisite with a named ``[PASS]`` / ``[FAIL]`` line and prints the exact
remediation for each failure, so operators catch misconfiguration in seconds
rather than discovering it through a cascade of CRASH tickets during a live
sprint.

Install-time vs. dispatch-time (issue #789)
-------------------------------------------
This is the **install-time** doctor: it validates that the *host* is correctly
provisioned (tools on PATH, auth, git identity, venv, writable DB, launchd
plist environment) once, before any sprint runs. It complements — and does not
duplicate — the **dispatch-time** pre-dispatch doctor in
``services/sprint_manager/sprint_manager.py`` (issue #789), which runs a cheap
auth probe before *each* coder/tester dispatch. Install-time = "is this machine
set up?"; dispatch-time = "is auth still live right now?". Keep the two
concerns separate.

Usage::

    python scripts/doctor.py          # human-readable PASS/FAIL report
    python scripts/doctor.py --json   # machine-readable JSON report

Exit code is ``0`` only when every check passes; non-zero (``1``) with a
summary of all failures when any check fails.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-subprocess timeout. Every probe is a cheap "is it reachable / authed"
# call, never a full agent run, so the whole report stays well under 10 s.
DEFAULT_TIMEOUT = 8

# Default installed launchd plist for the commander dashboard service. The
# plist PATH / token checks are skipped when this file is absent (the service
# is not installed on this host).
DEFAULT_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / "com.commander.dashboard.plist"
)

# Import names (not pip names) that must resolve inside the venv. Kept focused
# on the core runtime deps so a missing install is caught without importing
# heavy optional packages.
REQUIRED_IMPORTS = ["fastapi", "uvicorn", "dotenv", "httpx", "yaml", "pytest"]

# Plist EnvironmentVariables keys that satisfy each headless-token requirement.
GH_TOKEN_KEYS = ["GH_TOKEN", "GITHUB_TOKEN"]
CLAUDE_TOKEN_KEYS = ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]


# ── Result type ──────────────────────────────────────────────────────────────

# ok=True  → check passed (or was not applicable on this host and was skipped)
# fix      → the exact command / instruction to resolve a FAIL (empty on PASS)
# detail   → optional extra context shown after the status line
CheckResult = namedtuple("CheckResult", ["name", "ok", "fix", "detail"])


def _result(name: str, ok: bool, fix: str = "", detail: str = "") -> CheckResult:
    return CheckResult(name=name, ok=ok, fix=fix, detail=detail)


def _run(cmd, timeout: int = DEFAULT_TIMEOUT):
    """Run a subprocess and return the CompletedProcess (captured, text)."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ── Individual checks ────────────────────────────────────────────────────────

def check_claude_cli(*, which=shutil.which, runner=_run) -> CheckResult:
    """`claude` CLI is on the service PATH and answers a cheap probe.

    Uses ``claude --version`` — a fast probe that confirms the binary is
    reachable and runnable, never a full agent run (issue #789 separation).
    """
    name = "claude CLI reachable"
    path = which("claude")
    if not path:
        return _result(
            name,
            False,
            fix=(
                "Install Claude Code and ensure its directory is on the service "
                "PATH. If the dashboard runs under launchd, add the directory "
                "containing `claude` to the plist PATH by reinstalling: "
                "`bash scripts/install_launchd.sh` (it resolves `claude` "
                "dynamically)."
            ),
        )
    try:
        proc = runner(["claude", "--version"])
    except FileNotFoundError:
        return _result(
            name,
            False,
            fix="Install Claude Code and put `claude` on the service PATH.",
        )
    except subprocess.TimeoutExpired:
        return _result(
            name,
            False,
            fix=f"`claude --version` timed out (>{DEFAULT_TIMEOUT}s); check the install.",
        )
    if proc.returncode != 0:
        return _result(
            name,
            False,
            fix=(
                "`claude --version` returned non-zero. Re-authenticate Claude "
                "Code (`claude` then `/login`) and verify the install."
            ),
            detail=(proc.stderr or "").strip(),
        )
    return _result(name, True, detail=f"{path} ({(proc.stdout or '').strip()})")


def check_gh_cli(*, which=shutil.which, runner=_run) -> CheckResult:
    """`gh` is on the service PATH and authenticates to GitHub."""
    name = "gh authenticated"
    path = which("gh")
    if not path:
        return _result(
            name,
            False,
            fix=(
                "Install the GitHub CLI from https://cli.github.com and ensure "
                "its directory is on the service PATH (reinstall launchd with "
                "`bash scripts/install_launchd.sh` to add it to the plist PATH)."
            ),
        )
    try:
        proc = runner(["gh", "auth", "status"])
    except FileNotFoundError:
        return _result(
            name, False, fix="Install the GitHub CLI: https://cli.github.com"
        )
    except subprocess.TimeoutExpired:
        return _result(
            name,
            False,
            fix=f"`gh auth status` timed out (>{DEFAULT_TIMEOUT}s); check network/auth.",
        )
    if proc.returncode != 0:
        return _result(
            name,
            False,
            fix=(
                "Run `gh auth login` (or set GH_TOKEN in the launchd plist for "
                "headless auth) to authenticate the GitHub CLI."
            ),
            detail=((proc.stdout or "") + (proc.stderr or "")).strip(),
        )
    return _result(name, True, detail=path)


def check_git_identity(*, runner=_run) -> CheckResult:
    """Git identity (`user.name` and `user.email`) is configured."""
    name = "git identity configured"
    missing = []
    values = {}
    for key in ("user.name", "user.email"):
        try:
            proc = runner(["git", "config", "--get", key])
        except FileNotFoundError:
            return _result(
                name, False, fix="Install git and configure your identity."
            )
        except subprocess.TimeoutExpired:
            return _result(name, False, fix="`git config` timed out; check git.")
        value = (proc.stdout or "").strip()
        if proc.returncode != 0 or not value:
            missing.append(key)
        else:
            values[key] = value
    if missing:
        fixes = "; ".join(
            f'git config --global {k} "<your {k.split(".")[1]}>"' for k in missing
        )
        return _result(
            name,
            False,
            fix=f"Set the missing git identity value(s): {fixes}",
            detail=f"missing: {', '.join(missing)}",
        )
    return _result(
        name, True, detail=f"{values['user.name']} <{values['user.email']}>"
    )


def check_venv_packages(
    *, packages=REQUIRED_IMPORTS, finder=importlib.util.find_spec, venv_dir=None
) -> CheckResult:
    """Python venv is present and all required packages are importable."""
    name = "venv and packages importable"
    venv_dir = Path(venv_dir) if venv_dir is not None else REPO_ROOT / "venv"
    if not venv_dir.exists():
        return _result(
            name,
            False,
            fix=(
                f"Create the venv: `~/.local/bin/python3.12 -m venv {venv_dir}` "
                f"then `{venv_dir}/bin/pip install -r requirements.txt`."
            ),
        )
    missing = []
    for pkg in packages:
        try:
            if finder(pkg) is None:
                missing.append(pkg)
        except (ImportError, ValueError, ModuleNotFoundError):
            missing.append(pkg)
    if missing:
        return _result(
            name,
            False,
            fix=(
                f"Install missing package(s) into the venv: "
                f"`{venv_dir}/bin/pip install -r requirements.txt` "
                f"(missing import name(s): {', '.join(missing)})."
            ),
            detail=f"missing: {', '.join(missing)}",
        )
    return _result(name, True, detail=f"{venv_dir} — {len(packages)} core packages")


def check_db_path_writable(*, db_path=None) -> CheckResult:
    """`DB_PATH` is set and writable."""
    name = "DB_PATH writable"
    raw = db_path if db_path is not None else os.environ.get("DB_PATH", "")
    raw = (raw or "").strip()
    if not raw:
        return _result(
            name,
            False,
            fix=(
                "Set DB_PATH in apps/dashboard/.env (e.g. DB_PATH=./commander.db) "
                "and ensure the launchd plist environment exposes it."
            ),
        )
    path = Path(raw)
    target = path if path.exists() else path.parent
    try:
        # Append-open touches the file (or fails) without truncating existing data.
        with open(path, "a"):
            pass
    except OSError as exc:
        return _result(
            name,
            False,
            fix=(
                f"Make DB_PATH writable: ensure `{target}` exists and is writable "
                f"(e.g. `chmod u+w {target}` / pick a writable DB_PATH)."
            ),
            detail=str(exc),
        )
    return _result(name, True, detail=str(path))


def _parse_plist_env(plist_path: Path) -> dict:
    """Return the EnvironmentVariables dict from a launchd plist (or {})."""
    import plistlib

    with open(plist_path, "rb") as fh:
        data = plistlib.load(fh)
    env = data.get("EnvironmentVariables", {})
    return env if isinstance(env, dict) else {}


def check_plist_path(*, plist_path=None, which=shutil.which) -> CheckResult:
    """The launchd plist PATH includes the dirs containing `claude` and `gh`.

    Skipped (PASS) when the service is not installed on this host — the plist
    PATH only matters once the dashboard runs unattended under launchd.
    """
    name = "launchd plist PATH includes tool dirs"
    plist = Path(plist_path) if plist_path is not None else DEFAULT_PLIST
    if not plist.exists():
        return _result(
            name, True, detail="launchd service not installed — skipped"
        )
    env = _parse_plist_env(plist)
    plist_path_value = env.get("PATH", "")
    plist_dirs = [d for d in plist_path_value.split(os.pathsep) if d]

    missing = []
    for tool in ("claude", "gh"):
        resolved = which(tool)
        if not resolved:
            # Can't determine the dir to require if the tool isn't reachable
            # here; the dedicated claude/gh checks already report that FAIL.
            continue
        # Use the directory the binary is *found in* on PATH (the symlink's own
        # parent), NOT the symlink target — this matches how install_launchd.sh
        # builds the plist PATH (`dirname` + `pwd`, which does not resolve the
        # symlink target). Resolving here would demand the wrong dir.
        tool_dir = os.path.dirname(os.path.abspath(resolved))
        if tool_dir not in plist_dirs:
            missing.append((tool, tool_dir))
    if missing:
        dirs = "; ".join(f"{tool}: {d}" for tool, d in missing)
        return _result(
            name,
            False,
            fix=(
                "Add the tool director(ies) to the launchd plist PATH by "
                "reinstalling the service: `bash scripts/install_launchd.sh` "
                f"(it resolves and includes these dirs). Missing → {dirs}"
            ),
            detail=f"plist PATH: {plist_path_value}",
        )
    return _result(name, True, detail=str(plist))


def check_headless_tokens(*, plist_path=None) -> CheckResult:
    """When the service is installed, headless `claude`/`gh` tokens are present.

    Skipped (PASS) when the service is not installed — headless tokens only
    matter for the unattended launchd runner.
    """
    name = "headless auth tokens in plist"
    plist = Path(plist_path) if plist_path is not None else DEFAULT_PLIST
    if not plist.exists():
        return _result(
            name, True, detail="launchd service not installed — skipped"
        )
    env = _parse_plist_env(plist)
    has_gh = any(env.get(k) for k in GH_TOKEN_KEYS)
    has_claude = any(env.get(k) for k in CLAUDE_TOKEN_KEYS)
    if has_gh and has_claude:
        return _result(name, True, detail=str(plist))

    missing = []
    if not has_gh:
        missing.append("gh (set GH_TOKEN)")
    if not has_claude:
        missing.append("claude (set CLAUDE_CODE_OAUTH_TOKEN)")
    return _result(
        name,
        False,
        fix=(
            "Add headless token(s) to the launchd plist EnvironmentVariables "
            "and reinstall: `bash scripts/install_launchd.sh --gh-token <token>` "
            "for gh; add CLAUDE_CODE_OAUTH_TOKEN for claude. Missing: "
            + ", ".join(missing)
        ),
    )


def _plist_working_dir(plist_path: Path) -> str:
    """Return the launchd plist WorkingDirectory (or '')."""
    import plistlib

    try:
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
    except OSError:
        return ""
    wd = data.get("WorkingDirectory", "")
    return wd if isinstance(wd, str) else ""


def _read_env_token(env_file: Path) -> str:
    """Return the GH_TOKEN value from an agent .env (or '')."""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _resolve_headless_gh_token(plist_path: Path) -> tuple:
    """Return (token, source) the launchd dashboard would use for headless gh.

    Mirrors the runtime resolution order: a plist EnvironmentVariables token is
    set at exec by launchd and wins; otherwise the running clone's
    apps/dashboard/.env is loaded via dotenv at startup. ('', '') when neither
    carries a token.
    """
    env = _parse_plist_env(plist_path)
    for k in GH_TOKEN_KEYS:
        v = (env.get(k) or "").strip()
        if v:
            return v, f"plist {k}"
    wd = _plist_working_dir(plist_path)
    if wd:
        # WorkingDirectory is usually the apps/dashboard dir itself (so .env sits
        # beside it); fall back to the clone-root layout for older installs.
        for env_file in (Path(wd) / ".env", Path(wd) / "apps" / "dashboard" / ".env"):
            tok = _read_env_token(env_file)
            if tok:
                return tok, str(env_file)
    return "", ""


def _gh_token_is_valid(token: str) -> bool:
    """True when `gh api user` succeeds with this token (validity probe)."""
    env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}
    try:
        proc = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
            env=env,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_headless_gh_token_valid(
    *, plist_path=None, validator=_gh_token_is_valid
) -> CheckResult:
    """The stored headless GH_TOKEN actually authenticates (not stale/revoked).

    ``check_headless_tokens`` proves a token is *present*; this proves it still
    *works*. The recurring "gh CLI token gone again" failure is a present-but-
    revoked `gho_` device token that drifted from the keychain — caught here.

    Skipped (PASS) when the service is not installed, or when no headless token
    is configured (that absence is owned by ``check_headless_tokens``).
    """
    name = "headless GH_TOKEN valid"
    plist = Path(plist_path) if plist_path is not None else DEFAULT_PLIST
    if not plist.exists():
        return _result(name, True, detail="launchd service not installed — skipped")
    token, source = _resolve_headless_gh_token(plist)
    if not token:
        return _result(
            name,
            True,
            detail="no headless GH_TOKEN configured — see 'headless auth tokens in plist'",
        )
    if validator(token):
        return _result(name, True, detail=f"valid — source: {source}")
    return _result(
        name,
        False,
        fix=(
            "Stored headless GH_TOKEN is invalid/revoked (`gh api user` failed). "
            "Mint a long-lived PAT and reinstall: "
            "`bash scripts/install_launchd.sh --gh-token <PAT>`, then restart the "
            "launchd dashboard."
        ),
        detail=f"source: {source}",
    )


# ── Orchestration ────────────────────────────────────────────────────────────

# Checks run in the exact order specified by the acceptance criteria.
CHECKS = [
    check_claude_cli,
    check_gh_cli,
    check_git_identity,
    check_venv_packages,
    check_db_path_writable,
    check_plist_path,
    check_headless_tokens,
    check_headless_gh_token_valid,
]


def run_all_checks() -> list:
    """Run every check in AC order and return the list of CheckResults.

    Each check is isolated: an exception in one check is converted to a FAIL so
    the remaining checks still run (UAT step 2 — all other checks still run).
    """
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # defensive: never let one check abort the rest
            results.append(
                _result(
                    getattr(check, "__name__", "check"),
                    False,
                    fix=f"Unexpected error running this check: {exc}",
                )
            )
    return results


def format_result(result: CheckResult) -> list:
    """Render one CheckResult as the lines to print.

    PASS → ``[PASS] <name>``. FAIL → ``[FAIL] <name>`` followed by an indented
    ``↳ fix:`` line carrying the exact remediation (AC: every FAIL is followed
    by its fix).
    """
    status = "PASS" if result.ok else "FAIL"
    lines = [f"[{status}] {result.name}"]
    if result.ok:
        if result.detail:
            lines.append(f"       {result.detail}")
    else:
        if result.detail:
            lines.append(f"       {result.detail}")
        lines.append(f"       ↳ fix: {result.fix}")
    return lines


def to_dict(result: CheckResult) -> dict:
    """JSON-serialisable form of a CheckResult (used by the dashboard)."""
    return {
        "name": result.name,
        "ok": result.ok,
        "fix": result.fix,
        "detail": result.detail,
    }


def run_doctor() -> dict:
    """Run all checks and return a structured report (dashboard entry point)."""
    results = run_all_checks()
    failures = [r for r in results if not r.ok]
    return {
        "ok": not failures,
        "exit_code": 0 if not failures else 1,
        "checks": [to_dict(r) for r in results],
        "failures": [to_dict(r) for r in failures],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate this host is ready to run a Commander sprint."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report instead of the text report.",
    )
    args = parser.parse_args(argv)

    results = run_all_checks()
    failures = [r for r in results if not r.ok]

    if args.json:
        print(json.dumps(run_doctor_from(results), indent=2))
        return 0 if not failures else 1

    print("Commander host doctor — pre-sprint validation (issue #828/#854)")
    print("=" * 58)
    for result in results:
        for line in format_result(result):
            print(line)

    print("=" * 58)
    if failures:
        print(f"{len(failures)} check(s) FAILED — fix the following before running a sprint:")
        for result in failures:
            print(f"  - {result.name}")
            print(f"      ↳ {result.fix}")
        return 1
    print(f"All {len(results)} checks passed — host is ready to run a sprint.")
    return 0


def run_doctor_from(results) -> dict:
    """Build the structured report from an already-computed result list."""
    failures = [r for r in results if not r.ok]
    return {
        "ok": not failures,
        "exit_code": 0 if not failures else 1,
        "checks": [to_dict(r) for r in results],
        "failures": [to_dict(r) for r in failures],
    }


if __name__ == "__main__":
    sys.exit(main())

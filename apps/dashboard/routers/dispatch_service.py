"""Shared sprint-dispatch subprocess helpers (extracted from server.py, issue #795).

A single home for the subprocess-environment plumbing used when dispatching
``sprint_manager`` runs. Both the extracted ``routers`` modules and the
``sprint_manager``-facing run/finish endpoints that remain in ``server.py``
import from here, so the env-building logic is defined exactly once (no
duplication — AC3).

This module is intentionally dependency-free with respect to ``server.py`` —
it only reads the process environment — so it is safe to import at any time
(no circular-import hazard with the monolith).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _resolve_gh_token() -> str:
    """Return a GitHub token for the sprint subprocess, or "".

    The dashboard is typically started by launchd, whose spawned processes (and
    their grandchildren — server → spawn → sprint_manager → gh) often CANNOT read
    the macOS login Keychain where `gh` stores its auth. The result is a silent
    `gh issue list` that returns nothing, so a sprint reports "No dispatchable
    issues" even though the tickets exist. Extracting the token here (in the
    server, which can reach the Keychain — it syncs the issue mirror) and passing
    it to the subprocess as GH_TOKEN makes the subprocess's gh use the token
    directly, with no Keychain dependency.

    Honors an already-set GH_TOKEN/GITHUB_TOKEN (e.g. from .env or the plist).
    Best-effort: any failure returns "" and the subprocess falls back to whatever
    auth it can find.
    """
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def build_sprint_subprocess_env() -> dict:
    """Build the subprocess environment for sprint_manager.

    Strips ANTHROPIC_API_KEY and resolves DB_PATH to an absolute path so that
    sprint_manager (which runs from the coder clone CWD) writes to the same
    database that the server reads from, regardless of relative path differences.
    Also injects GH_TOKEN so the subprocess's `gh` works without the macOS
    Keychain (which launchd-spawned children can't reach) — see _resolve_gh_token.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    db_val = env.get("DB_PATH", "")
    if db_val and not os.path.isabs(db_val):
        env["DB_PATH"] = str(Path(db_val).resolve())
    token = _resolve_gh_token()
    if token:
        env["GH_TOKEN"] = token
    return env

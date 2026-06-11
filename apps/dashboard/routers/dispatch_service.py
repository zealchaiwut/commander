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
from pathlib import Path


def build_sprint_subprocess_env() -> dict:
    """Build the subprocess environment for sprint_manager.

    Strips ANTHROPIC_API_KEY and resolves DB_PATH to an absolute path so that
    sprint_manager (which runs from the coder clone CWD) writes to the same
    database that the server reads from, regardless of relative path differences.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    db_val = env.get("DB_PATH", "")
    if db_val and not os.path.isabs(db_val):
        env["DB_PATH"] = str(Path(db_val).resolve())
    return env

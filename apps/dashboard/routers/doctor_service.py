"""Service logic for the doctor router (issue #828).

Thin adapter over ``scripts/doctor.py``: imports the install-time host doctor
and returns its structured report so the dashboard renders the exact same
checks the CLI prints. Keeping a single source of truth means the button and
the command never drift apart.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path

# scripts/ lives at the repo root, two levels above apps/dashboard/routers/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))


def run_doctor() -> dict:
    """Run all host checks and return the structured report.

    Shape: ``{"ok": bool, "exit_code": int, "checks": [...], "failures": [...]}``.
    """
    import doctor  # noqa: PLC0415 — late import so the path insert above applies

    return doctor.run_doctor()

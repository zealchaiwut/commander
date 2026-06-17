"""Deploy promotion endpoint — promote develop to master via PR.

Routes in this module:
  POST /api/deploy/promote
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent  # apps/dashboard/
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent  # repo root
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Script root is the repo root (apps/dashboard -> apps -> repo root)
_PROMOTE_SCRIPT_ROOT = _REPO_ROOT

router = APIRouter()


# ── Request body ──────────────────────────────────────────────────────────────

class PromoteBody(BaseModel):
    draft: bool = True


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/api/deploy/promote")
def promote_to_master(body: PromoteBody):
    """Shell out to scripts/promote_to_master.py and return the PR URL.

    Returns: {"pr_url": "<url>"}
    Exit codes from the script: 0 = success, 1 = precondition, 2 = GitHub API failure.
    """
    script_path = _PROMOTE_SCRIPT_ROOT / "scripts" / "promote_to_master.py"
    if not script_path.exists():
        raise HTTPException(500, detail="promote_to_master.py not found")

    cmd = [sys.executable, str(script_path)]
    if body.draft:
        cmd.append("--draft")
    else:
        cmd.append("--ready")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_PROMOTE_SCRIPT_ROOT))

    if result.returncode == 0:
        pr_url = result.stdout.strip()
        return {"pr_url": pr_url}
    elif result.returncode == 1:
        err = result.stderr.strip() or result.stdout.strip() or "Precondition failed"
        raise HTTPException(400, detail=err)
    else:
        err = result.stderr.strip() or result.stdout.strip() or "GitHub API failure"
        raise HTTPException(502, detail=err)

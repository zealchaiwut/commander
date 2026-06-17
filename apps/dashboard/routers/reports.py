"""Daily report generation endpoint.

Routes in this module:
  POST /api/reports/daily
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent  # apps/dashboard/
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent  # repo root
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

router = APIRouter()


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/api/reports/daily")
async def generate_daily_report(request: Request):
    """Trigger daily summary report generation.

    Optional JSON body: {"date": "YYYY-MM-DD"} (default: today).
    Writes .commander/reports/YYYY-MM-DD.md and returns the file path.
    """
    target_date_str: Optional[str] = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            target_date_str = body.get("date")
    except Exception:
        pass

    cmd = [sys.executable, str(_REPO_ROOT / "scripts" / "generate_daily_report.py")]
    if target_date_str:
        cmd.extend(["--date", target_date_str])

    env = os.environ.copy()
    if "DB_PATH" not in env or not env["DB_PATH"].strip():
        db_candidate = _DASHBOARD_ROOT / "commander.db"
        if db_candidate.exists():
            env["DB_PATH"] = str(db_candidate)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, detail="Report generation timed out after 30 s")

    if result.returncode != 0:
        raise HTTPException(
            500,
            detail=f"Report generation failed: {result.stderr.strip() or result.stdout.strip()}",
        )

    out_line = result.stdout.strip()
    report_path = out_line.removeprefix("Report written to ").strip()
    return {"ok": True, "path": report_path, "message": out_line}

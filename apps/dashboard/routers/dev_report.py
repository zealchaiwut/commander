"""GET /api/dev-report endpoint (issue #1960).

Serves the stored dev_report artifact from brief_artifacts, optionally
regenerating inline when ?force=1 is passed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import dev_report_service as _svc

router = APIRouter(tags=["dev-report"])


@router.get("/api/dev-report")
def get_dev_report(
    date: Optional[str] = None,
    force: bool = False,
):
    """Return the stored dev_report artifact for a given date.

    - ``date`` (YYYY-MM-DD): defaults to Bangkok today when omitted.
    - ``force=1``: regenerate inline, persist, then return.
    - No artifact + no force → 404 with {\"error\": \"...\"}.
    """
    d = date or _svc._bkk_today()

    if force:
        stored = _svc.assemble_and_store(d)
        return stored["payload"]

    stored = _svc.get_dev_report_artifact(d)
    if stored is None or stored["payload"] is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No dev report artifact found for {d}"},
        )
    return stored["payload"]

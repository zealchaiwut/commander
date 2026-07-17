"""Shared dev-report assembly logic (issue #1960).

Wraps build_contract() from scripts/export_hermes_report.py so both the CLI
script and the GET /api/dev-report endpoint share one assembly path.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_BKK = ZoneInfo("Asia/Bangkok")
_SCOPE = "dev_report"
_PROJECT = ""

# Make the scripts directory importable for build_contract + _UNSET.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from export_hermes_report import build_contract  # noqa: E402


def _db():
    """Deferred db import so tests can patch DB_PATH before it resolves."""
    import db  # noqa: PLC0415
    return db


def _bkk_today() -> str:
    """Return today's date in Bangkok timezone (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).astimezone(_BKK).date().isoformat()


def _now_for_date(date_str: str) -> datetime:
    """Return a UTC datetime whose Bangkok date matches date_str."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    bkk_noon = d.replace(hour=12, tzinfo=_BKK)
    return bkk_noon.astimezone(timezone.utc)


def assemble_dev_report(date: str, db_path: str | None = None) -> dict:
    """Assemble the dev report payload for the given Bangkok date.

    Returns the full contract dict (``_new_state`` stripped out).
    """
    db_module = _db()
    resolved_db_path = db_path or str(db_module.DB_PATH)
    now = _now_for_date(date)
    contract: dict = build_contract(
        resolved_db_path,
        now=now,
        projects_list=[],
        price_map=None,
    )
    contract.pop("_new_state", None)
    return contract


def get_dev_report_artifact(date: str) -> dict | None:
    """Return the stored artifact row for date, or None if not stored.

    The returned dict has keys ``payload`` (decoded dict) and ``generated_at``.
    """
    db = _db()
    return db.get_brief_artifact(_SCOPE, _PROJECT, date)


def assemble_and_store(date: str, db_path: str | None = None) -> dict:
    """Assemble the report for date, persist it, and return the stored row."""
    contract = assemble_dev_report(date, db_path=db_path)
    db = _db()
    generated_at = db.set_brief_artifact(_SCOPE, _PROJECT, date, contract)
    return {"payload": contract, "generated_at": generated_at}

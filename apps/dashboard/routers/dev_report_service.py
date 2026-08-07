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
_STATE_SCOPE = "dev_report_state"
_PROJECT = ""
_STATE_DATE = "latest"

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


def _load_prev_state(db_module) -> dict:
    """Load the previous run's blocked-issue state from brief_artifacts."""
    row = db_module.get_brief_artifact(_STATE_SCOPE, _PROJECT, _STATE_DATE)
    if row and isinstance(row.get("payload"), dict):
        return row["payload"]
    return {}


def _save_new_state(db_module, state: dict) -> None:
    """Persist the current run's blocked-issue state for next run's fixed-detection."""
    db_module.set_brief_artifact(_STATE_SCOPE, _PROJECT, _STATE_DATE, state)


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
        projects_list=None,
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
    db_module = _db()
    resolved_db_path = db_path or str(db_module.DB_PATH)
    now = _now_for_date(date)

    prev_state = _load_prev_state(db_module)

    contract: dict = build_contract(
        resolved_db_path,
        now=now,
        projects_list=None,
        price_map=None,
        prev_state=prev_state,
    )
    new_state = contract.pop("_new_state", {})
    _save_new_state(db_module, new_state)

    generated_at = db_module.set_brief_artifact(_SCOPE, _PROJECT, date, contract)
    return {"payload": contract, "generated_at": generated_at}

"""Audit sprints for terminal state drift — issue #2167.

Scans all tracked sprints for mismatches between the stored DB state and what
the current _any_failed classification rule would derive from the sprint's own
ticket outcomes (issues_json).

This catches the class of bug where a sprint was classified ready_to_merge by
an older version of the terminal-state logic but should be needs_rework under
the current rule — invisible to the DB-vs-GitHub reconcile check because both
sides agreed on the same wrong value.

Usage:
    python3 scripts/audit_sprint_terminal_state_drift.py [--db PATH] [--project OWNER/REPO] [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

from dotenv import load_dotenv
load_dotenv(_DASHBOARD_ROOT / ".env", override=False)

import db  # noqa: E402
from routers.sprint_reconcile_service import (  # noqa: E402
    _derive_terminal_state_from_issues_json,
)

_TERMINAL_STATES = frozenset({
    "ready_to_merge", "needs_rework", "completed",
    "cancelled", "failed",
})


def _canonical(state: str) -> str:
    return db.canonical_lifecycle(state or "")


def audit(project_filter: str = "", apply: bool = False) -> list[dict]:
    """Return list of drifted sprint rows (and optionally correct them)."""
    rows = db.list_sprints_lifecycle()
    drifted: list[dict] = []

    for row in rows:
        label = row.get("label") or ""
        project = row.get("project") or ""
        if not label:
            continue
        if project_filter and project != project_filter:
            continue

        canonical = _canonical(row.get("state") or "")
        # Only check terminal states that can be misclassified
        if canonical not in ("ready_to_merge", "needs_rework"):
            continue

        issues_json = row.get("issues_json") or "[]"
        derived = _derive_terminal_state_from_issues_json(issues_json)
        if derived is None:
            continue  # no ticket data to check

        if derived == canonical:
            continue  # agrees — no drift

        if canonical == "needs_rework" and derived == "ready_to_merge":
            # Never auto-upgrade needs_rework -> ready_to_merge from issues_json
            # alone (issue #2194) — matches the invariant in
            # sprint_reconcile_service._outcome_reconcile_row: only the live
            # GitHub signal (_github_reconcile_row) may confirm a sprint is
            # actually mergeable, since issues_json/agent_status/failure_reason
            # doesn't capture every legitimate needs_rework cause (gate
            # failures, contamination, manual overrides, dead-lettered tickets).
            continue

        entry = {
            "label": label,
            "project": project,
            "stored_state": canonical,
            "derived_state": derived,
            "end_reason": row.get("end_reason") or "",
        }
        drifted.append(entry)

        if apply:
            try:
                result = db.transition_sprint_state(
                    label,
                    derived,
                    actor="reconcile",
                    end_reason=(row.get("end_reason") or "outcome-reclassification"),
                    project=project,
                )
                entry["applied"] = bool(getattr(result, "accepted", False))
            except Exception as exc:
                entry["applied"] = False
                entry["error"] = str(exc)

    return drifted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="", help="Path to dashboard.db (overrides DB_PATH env)")
    parser.add_argument("--project", default="", help="Filter to one project (owner/repo)")
    parser.add_argument("--apply", action="store_true",
                        help="Correct drifted rows via DB transition (idempotent)")
    args = parser.parse_args()

    if args.db:
        db.DB_PATH = Path(args.db)

    drifted = audit(project_filter=args.project, apply=args.apply)

    if not drifted:
        print("No terminal state drift found.")
        return

    verb = "Corrected" if args.apply else "Found"
    print(f"{verb} {len(drifted)} drifted sprint(s):\n")
    for entry in drifted:
        applied_tag = ""
        if args.apply:
            applied_tag = " [applied]" if entry.get("applied") else " [FAILED]"
            if entry.get("error"):
                applied_tag += f" — {entry['error']}"
        print(
            f"  {entry['label']} ({entry['project']}): "
            f"{entry['stored_state']} → {entry['derived_state']}"
            f" (end_reason={entry['end_reason']!r})"
            f"{applied_tag}"
        )

    if not args.apply:
        print(
            f"\nRe-run with --apply to correct these {len(drifted)} row(s)."
        )


if __name__ == "__main__":
    main()

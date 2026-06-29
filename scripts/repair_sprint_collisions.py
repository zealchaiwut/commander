#!/usr/bin/env python3
"""Surgical repair for sprint-66 cross-project collision (issue #1465).

Background
----------
The sprints table uses ``label TEXT PRIMARY KEY``, so two projects sharing a
sprint label compete for the same row.  When perf-coach wrote sprint-66 via
``ON CONFLICT(label) DO UPDATE``, commander's sprint-66 row was clobbered.
The orphan-running-row sweep later transitioned the stale perf-coach row to
``needs_rework``, but commander's row was never restored.

Audit manifest (hardcoded — sprint-66 incident only)
-----------------------------------------------------
  ASSERT_ABSENT  zealchaiwut/perf-coach  sprint-66
    Assert no stale *running* row survives for perf-coach's sprint-66.
    (The orphan sweep should have cleared it; this is a guard assertion.)

  RECREATE  zealchaiwut/commander  sprint-66
    Restore commander's base row from plan.json → state.json → agent_runs,
    without touching perf-coach's row.

Usage
-----
    python3 scripts/repair_sprint_collisions.py --dry-run
    python3 scripts/repair_sprint_collisions.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402

_LABEL = "sprint-66"
_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"

_MANIFEST = [
    {
        "label": _LABEL,
        "project": _PERF_COACH,
        "action": "ASSERT_ABSENT",
        "reason": (
            "Stale 'running' ghost from single-key overwrite; "
            "must not be in 'running' state after orphan sweep"
        ),
    },
    {
        "label": _LABEL,
        "project": _COMMANDER,
        "action": "RECREATE",
        "reason": (
            "Row clobbered by perf-coach's sprint-66 via ON CONFLICT(label) "
            "overwrite; restore from plan.json → state.json → agent_runs"
        ),
    },
]


# ── result keys (used by tests) ───────────────────────────────────────────────

_KEY_PERF_COACH = "perf_coach_sprint_66"
_KEY_COMMANDER = "commander_sprint_66"


# ── schema detection ──────────────────────────────────────────────────────────

def _has_composite_sprint_key(conn) -> bool:
    """True if the sprints table uses composite PRIMARY KEY (label, project)."""
    rows = conn.execute("PRAGMA table_info(sprints)").fetchall()
    pk_cols = [r[1] for r in rows if r[5] > 0]
    return "project" in pk_cols


# ── state resolution: plan.json → state.json → agent_runs → children → default

def _resolve_state_from_plan_json(label: str, commander_dir: Path) -> str | None:
    """Read lifecycle state from {label}-plan.json, or None if absent/invalid."""
    path = commander_dir / "sprints" / f"{label}-plan.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        raw = data.get("state") or data.get("lifecycle_state")
        if raw:
            return _db.canonical_lifecycle(str(raw))
    except Exception:
        pass
    return None


def _resolve_state_from_state_json(label: str, commander_dir: Path) -> str | None:
    """Read lifecycle state from {label}-state.json, or None if absent/invalid."""
    path = commander_dir / "sprints" / f"{label}-state.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        raw = (
            data.get("lifecycle_state")
            or data.get("state")
            or (data.get("reconciliation") or {}).get("lifecycle_state")
        )
        if raw:
            return _db.canonical_lifecycle(str(raw))
    except Exception:
        pass
    return None


def _resolve_state_from_agent_runs(label: str, project: str, conn) -> str | None:
    """Infer state from agent_runs rows for this sprint label + project.

    If any run finished, the sprint ended; infer needs_rework (it spawned
    children, so it didn't complete cleanly).
    """
    try:
        rows = conn.execute(
            "SELECT outcome, finished_at FROM agent_runs "
            "WHERE sprint_label = ? AND (project = ? OR project IS NULL) "
            "ORDER BY id DESC LIMIT 5",
            (label, project),
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    # Any finished run means the sprint had activity; default to needs_rework
    if any(r[1] for r in rows):
        return "needs_rework"
    return None


def _resolve_state_from_children(label: str, conn) -> str | None:
    """If child sprints exist (e.g. sprint-66.1), the parent is needs_rework."""
    try:
        row = conn.execute(
            "SELECT label FROM sprints WHERE parent_label = ? LIMIT 1",
            (label,),
        ).fetchone()
        if row:
            return "needs_rework"
    except Exception:
        pass
    return None


def _resolve_state(label: str, project: str, commander_dir: Path | None, conn) -> str:
    """Return the best lifecycle state for the to-be-recreated row.

    Priority: plan.json → state.json → agent_runs → children → 'needs_rework'.
    States that would be invalid in a recreated row (e.g. 'running') are
    remapped to 'needs_rework'.
    """
    _INVALID_FOR_RECREATE = {"running", "draft", "planned", "unknown"}

    if commander_dir:
        state = _resolve_state_from_plan_json(label, commander_dir)
        if state and state not in _INVALID_FOR_RECREATE:
            return state

        state = _resolve_state_from_state_json(label, commander_dir)
        if state and state not in _INVALID_FOR_RECREATE:
            return state

    state = _resolve_state_from_agent_runs(label, project, conn)
    if state:
        return state

    state = _resolve_state_from_children(label, conn)
    if state:
        return state

    return "needs_rework"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_sprint_row_unscoped(conn, label: str):
    """Return the sprints row for label (unscoped by project)."""
    try:
        return conn.execute(
            "SELECT * FROM sprints WHERE label = ?", (label,)
        ).fetchone()
    except Exception:
        return None


def _get_sprint_row_scoped(conn, label: str, project: str):
    """Return the sprints row for this exact (label, project), or None.

    Scoped lookup for the ASSERT_ABSENT guard (issue #1481): under the composite
    (label, project) schema two projects can share a sprint label, so an
    unscoped ``WHERE label = ?`` may return the wrong project's row and miss a
    real running ghost. Scoping to (label, project) targets the correct row.
    """
    try:
        return conn.execute(
            "SELECT * FROM sprints WHERE label = ? AND project = ? LIMIT 1",
            (label, project),
        ).fetchone()
    except Exception:
        return None


def _commander_row_exists(conn, label: str, project: str) -> bool:
    """True if a row with this exact (label, project) exists."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sprints WHERE label = ? AND project = ?",
            (label, project),
        ).fetchone()
        if row:
            return True
        # Fallback for single-key schema: check the unscoped row
        row = _get_sprint_row_unscoped(conn, label)
        if row and (row["project"] or "").strip() == project:
            return True
    except Exception:
        pass
    return False


def _upsert_commander_row(conn, label: str, project: str, state: str) -> None:
    """Insert (or update) commander's sprint row.

    Handles both single-key (label TEXT PRIMARY KEY) and composite-key
    (PRIMARY KEY (label, project)) schemas.
    """
    from datetime import datetime, timezone
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if _has_composite_sprint_key(conn):
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, parent_label)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(label, project) DO UPDATE SET
                state      = excluded.state,
                created_at = COALESCE(sprints.created_at, excluded.created_at)
            """,
            (label, project, state, created_at),
        )
    else:
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, parent_label)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(label) DO UPDATE SET
                project     = excluded.project,
                state       = excluded.state,
                created_at  = COALESCE(sprints.created_at, excluded.created_at),
                parent_label = NULL
            """,
            (label, project, state, created_at),
        )
    conn.commit()


# ── public API ────────────────────────────────────────────────────────────────

def dry_run(manifest: list | None = None) -> None:
    """Print audit manifest with intended actions. No DB writes."""
    manifest = manifest if manifest is not None else _MANIFEST
    print("DRY RUN — no database writes will occur.\n")
    for entry in manifest:
        label = entry["label"]
        project = entry["project"]
        action = entry["action"]
        reason = entry["reason"]
        print(f"  {action:<15} {label} / {project}")
        print(f"    Reason: {reason}")
        print()


def apply(manifest: list | None = None, commander_dir: Path | None = None) -> dict:
    """Apply the audit manifest repairs.

    Processes ASSERT_ABSENT entries before RECREATE entries so the
    pre-repair state of the DB is captured accurately.

    Returns a result dict keyed by ``_KEY_PERF_COACH`` and ``_KEY_COMMANDER``.
    """
    manifest = manifest if manifest is not None else _MANIFEST
    results: dict = {}

    with _db.get_conn() as conn:
        _db._create_sprint_lifecycle_tables(conn)

        # ── ASSERT_ABSENT phase ───────────────────────────────────────────────
        for entry in manifest:
            if entry["action"] != "ASSERT_ABSENT":
                continue
            label = entry["label"]
            project = entry["project"]

            row = _get_sprint_row_scoped(conn, label, project)
            running = row is not None and row["state"] == "running"

            key = (
                _KEY_PERF_COACH
                if project == _PERF_COACH
                else f"{project.split('/')[-1].replace('-', '_')}_{label.replace('-', '_')}"
            )
            results[key] = {
                "action": "ASSERT_ABSENT",
                "assert_absent_passed": not running,
                "running_row_found": running,
            }

            if running:
                print(
                    f"  ⚠ ASSERT_ABSENT {label} / {project}"
                    " — FAILED: stale running row found (orphan sweep not yet run?)"
                )
            else:
                print(
                    f"  ✓ ASSERT_ABSENT {label} / {project}"
                    " — PASSED: no stale running ghost row"
                )

        # ── RECREATE phase ────────────────────────────────────────────────────
        for entry in manifest:
            if entry["action"] != "RECREATE":
                continue
            label = entry["label"]
            project = entry["project"]

            key = (
                _KEY_COMMANDER
                if project == _COMMANDER
                else f"{project.split('/')[-1].replace('-', '_')}_{label.replace('-', '_')}"
            )

            if _commander_row_exists(conn, label, project):
                results[key] = {
                    "action": "RECREATE",
                    "created": False,
                    "already_existed": True,
                }
                print(
                    f"  ✓ RECREATE {label} / {project}"
                    " — already exists (idempotent no-op)"
                )
                continue

            state = _resolve_state(label, project, commander_dir, conn)
            _upsert_commander_row(conn, label, project, state)

            results[key] = {
                "action": "RECREATE",
                "created": True,
                "state": state,
            }
            print(f"  ✓ RECREATE {label} / {project} — created (state={state})")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest with intended actions; make no DB writes.",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Apply repairs: assert perf-coach ghost absent, recreate commander row.",
    )
    parser.add_argument(
        "--commander-dir",
        type=Path,
        default=None,
        help=(
            "Path to the .commander directory containing sprints/. "
            "Defaults to auto-discovery via commander_paths."
        ),
    )
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return 0

    # Resolve commander dir for plan.json / state.json look-up
    commander_dir = args.commander_dir
    if commander_dir is None:
        try:
            from services.sprint_manager.commander_paths import discover_commander_dir
            commander_dir = discover_commander_dir()
        except Exception:
            commander_dir = REPO_ROOT / ".commander"

    print("Applying sprint-66 collision repairs…\n")
    result = apply(commander_dir=commander_dir)

    perf = result.get(_KEY_PERF_COACH, {})
    cmdr = result.get(_KEY_COMMANDER, {})

    if not perf.get("assert_absent_passed", True):
        print(
            "\nWARNING: perf-coach/sprint-66 still has a stale running row."
            " The RECREATE step overwrote it — re-run `--apply` to confirm idempotency."
        )

    created = cmdr.get("created", False)
    existed = cmdr.get("already_existed", False)
    if created:
        print(f"\nSummary: 1 row created ({_COMMANDER}/{_LABEL}), 1 assertion evaluated.")
    elif existed:
        print(f"\nSummary: 0 rows created (already repaired), 1 assertion evaluated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

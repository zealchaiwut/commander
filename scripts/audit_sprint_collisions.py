#!/usr/bin/env python3
"""Read-only audit: detect sprint label collisions across projects.

Cross-references sprints, sprint_history, agent_runs, and per-clone
plan.json / state.json to surface every label claimed by more than one
project. Identifies the survivor (current sprints row owner) and all
losing projects.

Output
------
- Markdown table printed to stdout
- JSON manifest written to .commander/runtime/sprint-collisions.json

Usage
-----
    python3 scripts/audit_sprint_collisions.py
    python3 scripts/audit_sprint_collisions.py --db /path/to/dashboard.db
    python3 scripts/audit_sprint_collisions.py --runtime-dir /path/to/.commander/runtime
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── path helpers ──────────────────────────────────────────────────────────────

def _default_db_path() -> Path:
    raw = os.environ.get("DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return REPO_ROOT / "dashboard.db"


def _default_runtime_dir() -> Path:
    try:
        from services.sprint_manager.commander_paths import discover_commander_dir
        return discover_commander_dir() / "runtime"
    except Exception:
        return REPO_ROOT / ".commander" / "runtime"


# ── data collection ───────────────────────────────────────────────────────────

def _collect_label_projects(conn) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return (label→{projects}, label→survivor_project).

    survivor_project is the project currently holding the sprints table row.
    label→projects includes ALL projects from sprints, sprint_history, agent_runs.
    """
    label_projects: dict[str, set[str]] = defaultdict(set)
    survivors: dict[str, str] = {}

    # sprints table — current survivor
    try:
        for row in conn.execute(
            "SELECT label, project FROM sprints WHERE label IS NOT NULL"
        ):
            label, project = row[0], (row[1] or "").strip()
            if label and project:
                label_projects[label].add(project)
                survivors[label] = project
    except Exception:
        pass

    # sprint_history — historical per-project records
    try:
        for row in conn.execute(
            "SELECT label, project FROM sprint_history "
            "WHERE label IS NOT NULL AND project IS NOT NULL AND project != ''"
        ):
            label, project = row[0], row[1].strip()
            if label and project:
                label_projects[label].add(project)
    except Exception:
        pass

    # agent_runs — per-project sprint activity (project column added via ALTER TABLE)
    try:
        for row in conn.execute(
            "SELECT sprint_label, project FROM agent_runs "
            "WHERE sprint_label IS NOT NULL AND project IS NOT NULL AND project != ''"
        ):
            label, project = row[0], row[1].strip()
            if label and project:
                label_projects[label].add(project)
    except Exception:
        pass

    return label_projects, survivors


def _scan_plan_state_files(projects_root: Path) -> dict[str, set[str]]:
    """Scan per-clone plan.json and state.json for (label, project) pairs.

    Walks immediate subdirectories of projects_root looking for
    .commander/sprints/*-plan.json and *-state.json. Each file's parent
    directory name is treated as the project slug and matched against
    projects_root to build an owner/slug string.
    """
    extra: dict[str, set[str]] = defaultdict(set)
    if not projects_root.is_dir():
        return extra

    for clone_dir in projects_root.iterdir():
        if not clone_dir.is_dir():
            continue
        sprints_dir = clone_dir / ".commander" / "sprints"
        if not sprints_dir.is_dir():
            continue
        for f in sprints_dir.iterdir():
            if not (f.name.endswith("-plan.json") or f.name.endswith("-state.json")):
                continue
            try:
                data = json.loads(f.read_text())
                project = (data.get("project") or "").strip()
                label = (data.get("label") or "").strip()
                if not label:
                    # Derive label from filename
                    if f.name.endswith("-plan.json"):
                        label = f.name[: -len("-plan.json")]
                    else:
                        label = f.name[: -len("-state.json")]
                if label and project:
                    extra[label].add(project)
            except Exception:
                pass
    return extra


# ── collision detection ───────────────────────────────────────────────────────

def _build_collisions(
    conn,
    extra: dict[str, set[str]] | None = None,
) -> list[dict]:
    """Build the collision manifest from all data sources."""
    label_projects, survivors = _collect_label_projects(conn)

    if extra:
        for label, projects in extra.items():
            label_projects[label].update(projects)

    collisions: list[dict] = []
    for label in sorted(label_projects):
        projects = label_projects[label]
        if len(projects) < 2:
            continue
        survivor = survivors.get(label, "")
        lost = sorted(p for p in projects if p != survivor)
        if not lost:
            continue
        collisions.append({
            "label": label,
            "survivor": survivor,
            "lost": lost,
        })

    return collisions


# ── output ────────────────────────────────────────────────────────────────────

def _print_markdown_table(collisions: list[dict]) -> None:
    """Print a Markdown table of collisions to stdout."""
    if not collisions:
        print("No sprint label collisions detected.")
        return

    print("| Label | Survivor | Lost |")
    print("|-------|----------|------|")
    for entry in collisions:
        lost_str = ", ".join(entry["lost"])
        print(f"| {entry['label']} | {entry['survivor']} | {lost_str} |")


def _write_manifest(collisions: list[dict], runtime_dir: Path) -> Path:
    """Write sprint-collisions.json to runtime_dir and return the path."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runtime_dir / "sprint-collisions.json"
    manifest_path.write_text(json.dumps(collisions, indent=2), encoding="utf-8")
    return manifest_path


# ── public API ────────────────────────────────────────────────────────────────

def run_audit(
    db_path: Path | None = None,
    runtime_dir: Path | None = None,
    projects_root: Path | None = None,
) -> list[dict]:
    """Run the collision audit and return the manifest entries.

    Side effects:
    - Prints a Markdown table to stdout.
    - Writes sprint-collisions.json to runtime_dir.

    No DB writes are performed.
    """
    import sqlite3

    resolved_db = Path(db_path) if db_path else _default_db_path()
    resolved_runtime = Path(runtime_dir) if runtime_dir else _default_runtime_dir()

    extra: dict[str, set[str]] = {}
    if projects_root and Path(projects_root).is_dir():
        extra = _scan_plan_state_files(Path(projects_root))

    conn = sqlite3.connect(resolved_db)
    conn.row_factory = sqlite3.Row
    try:
        collisions = _build_collisions(conn, extra or None)
    finally:
        conn.close()

    _print_markdown_table(collisions)
    _write_manifest(collisions, resolved_runtime)
    return collisions


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to the SQLite database (default: $DB_PATH or dashboard.db).",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write sprint-collisions.json "
            "(default: .commander/runtime/ discovered from cwd)."
        ),
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=None,
        help="Root directory containing per-clone subdirectories to scan for plan.json/state.json.",
    )
    args = parser.parse_args()

    collisions = run_audit(
        db_path=args.db,
        runtime_dir=args.runtime_dir,
        projects_root=args.projects_root,
    )

    runtime_dir = args.runtime_dir or _default_runtime_dir()
    manifest_path = runtime_dir / "sprint-collisions.json"
    print(f"\nManifest written to: {manifest_path}")
    print(f"Collisions found: {len(collisions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

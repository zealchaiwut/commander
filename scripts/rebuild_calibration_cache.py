#!/usr/bin/env python3
"""Rebuild calibration cache for a Commander project (issue #1332).

Clears the stale calibration_cache.json and rescans all sprint-*-state.json
files under .commander/sprints/ and .commander/sprints/archive/, producing
a fresh cache from the full sprint history.

Usage:
    python3 scripts/rebuild_calibration_cache.py --project <slug>
    python3 scripts/rebuild_calibration_cache.py --project <slug> --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _configured_minutes(project_root: Path) -> dict[str, int]:
    """Load per-size configured minutes from project settings (falls back to defaults)."""
    defaults = {"S": 5, "M": 15, "L": 30, "XL": 60}
    try:
        from settings_schema import APP_CONFIG_KEY, build_effective_response  # type: ignore[import]
        import settings_repo as _sr  # type: ignore[import]
        import projects as _proj  # type: ignore[import]
        slug = project_root.name
        all_projects = _proj.load_projects()
        repo = next(
            (p["repo"] for p in all_projects if p["repo"].split("/")[-1] == slug),
            None,
        )
        stored = _sr.get_setting(APP_CONFIG_KEY, project=repo) if repo else {}
        eff = build_effective_response(stored or {})
        return {
            "S": eff["estimation_s_minutes"],
            "M": eff["estimation_m_minutes"],
            "L": eff["estimation_l_minutes"],
            "XL": eff["estimation_xl_minutes"],
        }
    except Exception:
        return defaults


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the calibration cache for a Commander project."
    )
    parser.add_argument("--project", required=True, metavar="SLUG",
                        help="Project slug (e.g. commander)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print per-tier counts without writing to disk")
    args = parser.parse_args()

    projects_base = Path.home() / "dev"
    project_root = projects_base / args.project
    if not project_root.is_dir():
        print(f"Error: project root not found: {project_root}", file=sys.stderr)
        return 1

    configured_minutes = _configured_minutes(project_root)

    from maintenance_service import rebuild_calibration_cache  # type: ignore[import]
    summary = rebuild_calibration_cache(
        project_root, configured_minutes, dry_run=args.dry_run
    )

    tier_str = ", ".join(f"{sz}: {summary['by_size'][sz]}" for sz in ("S", "M", "L", "XL"))
    print(tier_str)
    print(f"Total: {summary['total']} tickets{'  (dry run)' if args.dry_run else ' processed'}")
    if args.dry_run:
        print("0 files written (dry run)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

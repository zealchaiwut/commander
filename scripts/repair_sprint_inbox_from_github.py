#!/usr/bin/env python3
"""Verify commander/perf-coach sprint lifecycle rows against GitHub and repair DB drift.

Use when History inbox shows stale needs_rework / ready_to_merge rows whose sprint
branches are already merged into develop, or when cross-project rows leaked into
the wrong project's feed.

Examples:
  DB_PATH=./apps/dashboard/commander.db python3 scripts/repair_sprint_inbox_from_github.py \\
    --project zealchaiwut/commander --dry-run

  DB_PATH=./apps/dashboard/commander.db python3 scripts/repair_sprint_inbox_from_github.py \\
    --project zealchaiwut/commander --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import db  # noqa: E402


_TERMINAL = frozenset({"needs_rework", "ready_to_merge", "failed"})


def _branch_merged_into_develop(project: str, label: str) -> tuple[bool, str]:
    """Return (merged, detail) using the same helper as bulk-complete / reconcile."""
    try:
        import server as srv  # noqa: PLC0415
    except Exception as exc:
        return False, f"server import failed: {exc}"
    branch = srv._sprint_branch_name(label)
    try:
        has_unmerged = srv._branch_has_unmerged_commits(project, branch, "develop")
    except Exception as exc:
        return False, f"compare failed: {exc}"
    if has_unmerged:
        return False, f"{branch} still has commits not in develop"
    return True, f"{branch} fully contained in develop"


def _has_rework(project: str, label: str) -> tuple[bool, str]:
    try:
        import server as srv  # noqa: PLC0415
        if srv._has_rework_tickets(label, project):
            return True, "rework labels on GitHub"
        return False, "no rework tickets"
    except Exception as exc:
        return False, f"rework check skipped: {exc}"


def _transition(label: str, project: str, to_state: str, actor: str, end_reason: str) -> tuple[bool, str]:
    res = db.transition_sprint_state(
        label, to_state, actor=actor, end_reason=end_reason, project=project,
    )
    if res.accepted:
        return True, "ok"
    return False, res.reason or "rejected"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="owner/repo, e.g. zealchaiwut/commander")
    parser.add_argument("--dry-run", action="store_true", help="Report only (default)")
    parser.add_argument("--apply", action="store_true", help="Write accepted transitions to DB")
    parser.add_argument("--limit", type=int, default=200, help="Max sprints to scan")
    args = parser.parse_args()
    if args.apply and args.dry_run:
        parser.error("Use either --dry-run or --apply, not both")
    apply = args.apply

    db.init_db()
    project = args.project.strip()
    rows = [
        r for r in db.list_sprints_lifecycle()
        if (r.get("project") or "").strip() == project
        and (r.get("state") or "") in _TERMINAL
    ]
    rows.sort(key=lambda r: r.get("label") or "")
    rows = rows[: max(1, args.limit)]

    print(f"Scanning {len(rows)} terminal row(s) for {project}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}\n")

    updated = 0
    for row in rows:
        label = row.get("label") or ""
        state = row.get("state") or ""
        merged, merge_detail = _branch_merged_into_develop(project, label)
        rework, rework_detail = _has_rework(project, label)

        action = "keep"
        reason = ""
        if state == "ready_to_merge" and merged and not rework:
            action = "complete"
            reason = f"{merge_detail}; {rework_detail}"
        elif state == "needs_rework" and merged and not rework:
            action = "complete"
            reason = f"{merge_detail}; {rework_detail} (needs_rework but shipped)"
        elif state == "failed" and merged and not rework:
            action = "complete"
            reason = f"{merge_detail}; {rework_detail} (failed but shipped)"

        print(f"{label:14} {state:16} -> {action:8}  {reason or merge_detail}")

        if action != "complete":
            continue

        actor = "manager" if state == "ready_to_merge" else "reconcile"
        if apply:
            ok, msg = _transition(
                label, project, "completed", actor,
                end_reason="repair-github-verified",
            )
            if ok:
                updated += 1
                print(f"  ✓ marked completed ({actor})")
            else:
                print(f"  ✗ {msg}")

    print(f"\n{'Updated' if apply else 'Would update'}: {updated} sprint(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

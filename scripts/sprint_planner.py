#!/usr/bin/env python3
"""Sprint Planner — conversational CLI to plan a sprint.

Usage:
    sprint-plan                   # interactive: auto-suggest next sprint number
    sprint-plan --sprint 3        # target a specific sprint number
    sprint-plan --dry-run         # propose plan, print it, apply nothing

Add to ~/.commander.zsh:
    function sprint-plan() { python3 ~/commander/scripts/sprint_planner.py "$@"; }
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# ── path setup ────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MANAGER_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(SPRINT_MANAGER_DIR))
sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(DASHBOARD_DIR / ".env")
import github_client  # noqa: E402
from sizing import SIZE_TO_MINUTES  # noqa: E402

# Retro helpers (issue #2026 AC-3) — surfaces last N committed retros at plan
# time so past outcomes feed future planning.  Import lazily if unavailable.
try:
    from retro import RECENT_RETROS_N, load_recent_retros  # type: ignore[import]
    _RETRO_AVAILABLE = True
except ImportError:  # pragma: no cover — retro module not on path
    _RETRO_AVAILABLE = False
    RECENT_RETROS_N = 3  # fallback default so the argparse default is numeric


# ── size estimation ───────────────────────────────────────────────────────────

SIZES = ["S", "M", "L", "XL"]
SIZE_IDX = {s: i for i, s in enumerate(SIZES)}

SIZE_MINUTES = SIZE_TO_MINUTES


def _estimate_size(issue: dict) -> str:
    """Estimate issue size from body content and labels.

    Sizing table:
    | AC + UAT | File mentions | Base size |
    |----------|---------------|-----------|
    | <= 3     | <= 1          | S         |
    | 4-7      | 2-3           | M         |
    | 8-12     | 4-6           | L         |
    | > 12     | > 6           | XL        |

    Label modifier: bug -> subtract 1 level; enhancement -> add 1 level
    """
    body = issue.get("body") or ""
    labels = {lbl["name"] for lbl in issue.get("labels", [])}

    # Count AC items (- [ ] lines under ## Acceptance Criteria)
    ac_count = 0
    in_ac = False
    for line in body.splitlines():
        if re.match(r"^#+\s+Acceptance Criteria", line, re.IGNORECASE):
            in_ac = True
            continue
        if in_ac and re.match(r"^#+\s+", line):
            in_ac = False
        if in_ac and re.match(r"^\s*-\s+\[[ x]\]", line):
            ac_count += 1

    # Count UAT steps (numbered lines under ## UAT Test Steps)
    uat_count = 0
    in_uat = False
    for line in body.splitlines():
        if re.match(r"^#+\s+UAT Test Steps", line, re.IGNORECASE):
            in_uat = True
            continue
        if in_uat and re.match(r"^#+\s+", line):
            in_uat = False
        if in_uat and re.match(r"^\s*\d+\.\s+", line):
            uat_count += 1

    # Count distinct file/path tokens (words containing a dot with an extension)
    file_pattern = re.compile(r"\b[\w_-]+\.(?:py|js|ts|html|css|sh|json|md|yaml|yml|txt|env)\b")
    file_mentions = len(set(file_pattern.findall(body)))

    total = ac_count + uat_count

    # Determine base size from table — each column maps independently, take min
    if total <= 3:
        size_by_total = "S"
    elif total <= 7:
        size_by_total = "M"
    elif total <= 12:
        size_by_total = "L"
    else:
        size_by_total = "XL"

    if file_mentions <= 1:
        size_by_files = "S"
    elif file_mentions <= 3:
        size_by_files = "M"
    elif file_mentions <= 6:
        size_by_files = "L"
    else:
        size_by_files = "XL"

    # Take the smaller (more conservative) of the two estimates
    base = SIZES[min(SIZE_IDX[size_by_total], SIZE_IDX[size_by_files])]

    # Apply label modifiers
    idx = SIZE_IDX[base]
    if "bug" in labels:
        idx = max(0, idx - 1)
    if "enhancement" in labels:
        idx = min(len(SIZES) - 1, idx + 1)

    return SIZES[idx]


def _sort_key(issue: dict) -> tuple:
    """Sorting key: bug issues first, then by size asc, then by issue number asc."""
    labels = {lbl["name"] for lbl in issue.get("labels", [])}
    is_bug = 0 if "bug" in labels else 1
    size_idx = SIZE_IDX.get(issue.get("_size", "M"), 1)
    return (is_bug, size_idx, issue.get("number", 0))


# ── display helpers ───────────────────────────────────────────────────────────

def _truncate(text: str, width: int = 45) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def _print_plan_table(plan: list[dict]) -> None:
    """Print the plan as a formatted table."""
    if not plan:
        sys.stdout.write(str("  (empty plan)") + "\n")
        return
    sys.stdout.write("\n")
    sys.stdout.write(str(f"  {'#':<4} {'Issue':<8} {'Size':<6} {'Order':<7} {'Title'}") + "\n")
    sys.stdout.write(str(f"  {'-'*4} {'-'*8} {'-'*6} {'-'*7} {'-'*45}") + "\n")
    for i, issue in enumerate(plan, 1):
        num = f"#{issue['number']}"
        size = issue.get("_size", "?")
        title = _truncate(issue.get("title", ""), 45)
        sys.stdout.write(str(f"  {i:<4} {num:<8} {size:<6} {i:<7} {title}") + "\n")
    sys.stdout.write("\n")

    # Footer: size summary + time estimate
    size_counts: dict[str, int] = {}
    total_minutes = 0
    for issue in plan:
        s = issue.get("_size", "M")
        size_counts[s] = size_counts.get(s, 0) + 1
        total_minutes += SIZE_MINUTES.get(s, 15)

    parts = [f"{count}{s}" for s, count in sorted(size_counts.items(), key=lambda x: SIZE_IDX[x[0]])]
    sprint_num = plan[0].get("_sprint_num", "?") if plan else "?"
    sys.stdout.write(str(f"  Sprint {sprint_num} size: {' + '.join(parts)} · est {total_minutes}min") + "\n")
    sys.stdout.write("\n")


# ── input helpers ─────────────────────────────────────────────────────────────

def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write(str("\nAborted.") + "\n")
        sys.exit(0)


# ── main conversation ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conversational CLI sprint planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sprint", type=int, default=None,
                        help="Target sprint number (default: auto-suggest)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only — do not apply any labels")
    parser.add_argument("--repo", default=None, help="owner/repo override")
    parser.add_argument(
        "--recent-retros",
        type=int,
        default=RECENT_RETROS_N,
        metavar="N",
        help=(
            f"Surface the last N committed retros from docs/changelog/uat/ "
            f"before planning (default: {RECENT_RETROS_N}; 0 to suppress)"
        ),
    )
    args = parser.parse_args()

    repo_name = args.repo
    dry_run = args.dry_run

    sys.stdout.write("\n")
    sys.stdout.write(str("═" * 60) + "\n")
    sys.stdout.write(str("  Sprint Planner") + "\n")
    sys.stdout.write(str("═" * 60) + "\n")

    # ── Step 0: Surface recent retros (issue #2026 AC-3) ──────────
    if _RETRO_AVAILABLE and args.recent_retros > 0:
        _docs_uat = REPO_ROOT / "docs" / "changelog" / "uat"
        _retros = load_recent_retros(_docs_uat, n=args.recent_retros)
        if _retros:
            sys.stdout.write(str(f"\nRecent Retros (last {len(_retros)})") + "\n")
            sys.stdout.write(str("─" * 60) + "\n")
            for _fname, _content in _retros:
                sys.stdout.write(str(f"\n--- {_fname} ---") + "\n")
                # Surface Key Learnings section if present; else first 20 lines
                _kl_match = re.search(
                    r"## Key Learnings\n(.*?)(?=\n## |\Z)",
                    _content,
                    re.DOTALL,
                )
                if _kl_match:
                    sys.stdout.write(_kl_match.group(0).strip() + "\n")
                else:
                    _preview = "\n".join(_content.splitlines()[:20])
                    sys.stdout.write(_preview + "\n")
            sys.stdout.write(str("─" * 60) + "\n")

    # ── Step 1: Load issues ────────────────────────────────────────
    sys.stdout.write(str("\nFetching open issues…") + "\n")
    try:
        all_issues = github_client.list_open_issues_with_body(repo_name=repo_name, limit=200)
        existing_sprints = github_client.list_sprints(repo_name=repo_name)
    except Exception as e:
        sys.stdout.write(str(f"\nError: {e}") + "\n")
        sys.exit(1)

    # Categorise issues
    sprint_re = re.compile(r"^sprint-(\d+)$")

    def get_sprint_num(issue: dict) -> Optional[int]:
        for lbl in issue.get("labels", []):
            m = sprint_re.match(lbl["name"])
            if m:
                return int(m.group(1))
        return None

    # ── Step 2: Confirm sprint number ─────────────────────────────
    if args.sprint is not None:
        sprint_num = args.sprint
    else:
        suggested = (max(existing_sprints) + 1) if existing_sprints else 1
        sys.stdout.write(str(f"\nExisting sprints: {existing_sprints or 'none'}") + "\n")
        ans = _prompt(f"Target sprint number [{suggested}]: ")
        if ans == "":
            sprint_num = suggested
        else:
            try:
                sprint_num = int(ans)
            except ValueError:
                sys.stdout.write(str("Invalid sprint number. Using suggested.") + "\n")
                sprint_num = suggested

    sys.stdout.write(str(f"\nPlanning for: sprint-{sprint_num}") + "\n")

    # Classify issues relative to the target sprint
    already_assigned: list[dict] = []
    candidates: list[dict] = []
    other_sprint: list[dict] = []

    for issue in all_issues:
        sn = get_sprint_num(issue)
        if sn == sprint_num:
            already_assigned.append(issue)
        elif sn is None:
            candidates.append(issue)
        else:
            other_sprint.append(issue)

    # Add size estimate to each candidate
    for issue in candidates:
        issue["_size"] = _estimate_size(issue)
    for issue in already_assigned:
        issue["_size"] = _estimate_size(issue)

    # ── Step 3: Select issues ──────────────────────────────────────
    if already_assigned:
        sys.stdout.write(str(f"\nAlready in sprint-{sprint_num} ({len(already_assigned)}):") + "\n")
        for iss in sorted(already_assigned, key=lambda x: x["number"]):
            sys.stdout.write(str(f"  #{iss['number']:<5} [{iss['_size']}]  {_truncate(iss['title'])}") + "\n")

    sys.stdout.write(str(f"\nCandidates (no sprint label) — {len(candidates)} issues:") + "\n")
    if not candidates:
        sys.stdout.write(str("  (none)") + "\n")
    else:
        for i, iss in enumerate(sorted(candidates, key=lambda x: x["number"]), 1):
            sys.stdout.write(str(f"  {i:>2}. #{iss['number']:<5} [{iss['_size']}]  {_truncate(iss['title'])}") + "\n")

    # Build sorted candidate list for selection by index
    numbered_candidates = sorted(candidates, key=lambda x: x["number"])

    if dry_run:
        # In dry-run, select all candidates + already assigned
        selected = list(already_assigned) + list(candidates)
        sys.stdout.write(str("\n[dry-run] Selecting all candidates.") + "\n")
    else:
        sys.stdout.write("\n")
        sys.stdout.write(str("Select issues to add to the plan:") + "\n")
        sys.stdout.write(str("  1,2,3  — pick by number | all — include all | small — S issues only | skip — keep existing") + "\n")
        raw = _prompt("Your selection: ")

        selected = list(already_assigned)  # start with already-assigned

        if raw.lower() == "skip":
            sys.stdout.write(str("Keeping existing sprint contents.") + "\n")
        elif raw.lower() == "all":
            selected = selected + list(candidates)
        elif raw.lower() in ("small", "small ones"):
            selected = selected + [c for c in candidates if c.get("_size") == "S"]
        else:
            # Parse "1,2,3" style
            indices = []
            for part in re.split(r"[,\s]+", raw):
                part = part.strip()
                if part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < len(numbered_candidates):
                        indices.append(idx)
                    else:
                        sys.stdout.write(str(f"  Warning: index {part} out of range, skipping.") + "\n")
            for idx in indices:
                iss = numbered_candidates[idx]
                if iss not in selected:
                    selected.append(iss)

    # Annotate with sprint num for display
    for iss in selected:
        iss["_sprint_num"] = sprint_num

    # Sort: bugs first, then by size asc, then by issue number asc
    selected.sort(key=_sort_key)

    # ── Step 4: Propose plan ───────────────────────────────────────
    sys.stdout.write(str("\nProposed plan:") + "\n")
    _print_plan_table(selected)

    if dry_run:
        sys.stdout.write(str("[dry-run] No labels will be applied.") + "\n")
        sys.stdout.write("\n")
        sys.exit(0)

    # ── Step 5: Modify and confirm ─────────────────────────────────
    while True:
        sys.stdout.write(str("Commands: remove #N | add #N | swap #A #B | confirm | quit") + "\n")
        cmd = _prompt("> ").strip()

        if not cmd:
            continue

        # remove #N
        m = re.match(r"^remove\s+#?(\d+)$", cmd, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            before = len(selected)
            selected = [iss for iss in selected if iss["number"] != num]
            if len(selected) < before:
                sys.stdout.write(str(f"  Removed #{num} from plan.") + "\n")
            else:
                sys.stdout.write(str(f"  #{num} not in plan.") + "\n")
            _print_plan_table(selected)
            continue

        # add #N
        m = re.match(r"^add\s+#?(\d+)$", cmd, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if any(iss["number"] == num for iss in selected):
                sys.stdout.write(str(f"  #{num} already in plan.") + "\n")
            else:
                # Find in all_issues
                found = next((iss for iss in all_issues if iss["number"] == num), None)
                if found is None:
                    # Try fetching directly
                    try:
                        found = github_client.get_issue(num, repo_name=repo_name)
                        found["_size"] = _estimate_size(found)
                        found["_sprint_num"] = sprint_num
                    except Exception:
                        sys.stdout.write(str(f"  Could not find issue #{num}.") + "\n")
                        continue
                else:
                    found["_size"] = found.get("_size") or _estimate_size(found)
                    found["_sprint_num"] = sprint_num
                selected.append(found)
                selected.sort(key=_sort_key)
                sys.stdout.write(str(f"  Added #{num} to plan.") + "\n")
            _print_plan_table(selected)
            continue

        # swap #A #B
        m = re.match(r"^swap\s+#?(\d+)\s+#?(\d+)$", cmd, re.IGNORECASE)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            ia = next((i for i, iss in enumerate(selected) if iss["number"] == a), None)
            ib = next((i for i, iss in enumerate(selected) if iss["number"] == b), None)
            if ia is None or ib is None:
                sys.stdout.write(str("  One or both issues not found in plan.") + "\n")
            else:
                selected[ia], selected[ib] = selected[ib], selected[ia]
                sys.stdout.write(str(f"  Swapped #{a} and #{b}.") + "\n")
            _print_plan_table(selected)
            continue

        if cmd.lower() == "confirm":
            break

        if cmd.lower() in ("quit", "q", "exit"):
            sys.stdout.write(str("Exited without applying labels.") + "\n")
            sys.exit(0)

        sys.stdout.write(str("  Unrecognised command. Use: remove #N | add #N | swap #A #B | confirm | quit") + "\n")

    # ── Step 6: Apply ─────────────────────────────────────────────
    if not selected:
        sys.stdout.write(str("Nothing to apply.") + "\n")
        sys.exit(0)

    sys.stdout.write(str(f"\nApplying sprint-{sprint_num} label to {len(selected)} issues…") + "\n")

    # Ensure the sprint label exists (create if absent)
    if not dry_run:
        try:
            github_client.ensure_sprint_label(sprint_num, repo_name=repo_name)
        except Exception as e:
            sys.stdout.write(str(f"Warning: could not ensure label: {e}") + "\n")

    sprint_re_any = re.compile(r"^sprint-\d+$")

    for issue in selected:
        num = issue["number"]
        current_labels = {lbl["name"] for lbl in issue.get("labels", [])}
        other_sprint_labels = [lbl for lbl in current_labels if sprint_re_any.match(lbl) and lbl != f"sprint-{sprint_num}"]
        target_label = f"sprint-{sprint_num}"

        try:
            github_client.update_labels(
                num,
                add=[target_label],
                remove=other_sprint_labels,
                repo_name=repo_name,
            )
            sys.stdout.write(str(f"  ✅ #{num} labelled sprint-{sprint_num}") + "\n")
        except Exception as e:
            sys.stdout.write(str(f"  ❌ #{num} failed: {e}") + "\n")

    sys.stdout.write(str(f"\nDone. Run `sprint-run sprint-{sprint_num}` to start the sprint.") + "\n")
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

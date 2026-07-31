"""Sprint retro helpers — issue #2026.

AC-1 (derive_key_learnings): populate ## Key Learnings from real sprint data
      instead of the permanent LEARNINGS_STUB.
AC-2 (write_retro_to_docs):  write a distilled retro to docs/changelog/uat/
      so it is committed and feeds the planning loop.
AC-3 (load_recent_retros):   read the last N committed retros for the sprint
      planner so past outcomes inform future planning.

None of these functions commit to git — they only write files.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python < 3.9 fallback
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

_BKK_TZ = ZoneInfo("Asia/Bangkok")

# Default number of past retros the sprint planner surfaces.  Keep as a named
# constant so callers and tests do not hardcode the magic number (CLAUDE.md §AC-3).
RECENT_RETROS_N: int = 3

# Pattern for retro filenames produced by write_retro_to_docs().
# Format: YYYY-MM-DD-sprint-<anything>.md
_RETRO_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-sprint-.+\.md$")

# Files that are always skipped when listing retros.
_SKIP_NAMES: frozenset[str] = frozenset({"_template.md", "README.md"})


# ── date helpers ──────────────────────────────────────────────────────────────


def _bkk_date(iso_ts: Optional[str] = None) -> str:
    """Return YYYY-MM-DD in Bangkok time from an ISO 8601 timestamp or today."""
    if iso_ts:
        try:
            dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            return dt.astimezone(_BKK_TZ).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    return datetime.now(tz=_BKK_TZ).strftime("%Y-%m-%d")


# ── mis-sizing helpers ────────────────────────────────────────────────────────


def _load_mis_sizing_flags(
    commander_dir: Optional[Path], sprint_label: str
) -> list[dict]:
    """Load mis-sizing flags for a sprint; return [] if absent or unreadable.

    The flags file lives at .commander/mis-sizing-flags-<sprint_label>.json.
    Each entry has at minimum: issue_number, current_estimate,
    historical_avg_actual_size, status ('pending' | 'approved' | 'dismissed' |
    'reestimated').
    """
    if commander_dir is None:
        return []
    path = commander_dir / f"mis-sizing-flags-{sprint_label}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("flags", [])
    except (json.JSONDecodeError, OSError):
        return []


# ── AC-1: derive Key Learnings ────────────────────────────────────────────────


def derive_key_learnings(
    state: object,  # SprintState — avoided import to prevent circular dep
    commander_dir: Optional[Path] = None,
) -> str:
    """Derive Key Learnings bullets from real sprint data.

    Graceful degradation: a missing data source contributes nothing.  The
    function never crashes and never returns the old LEARNINGS_STUB placeholder.

    Data sources (each omitted silently when absent):
    - What-Didn't-Ship: skipped issues with failure category
    - Failure categories per ticket (RETRY_EXHAUSTED / CRASH / …)
    - Tester rejections (TESTER_REJECTED category)
    - Mis-sizing flags from .commander/mis-sizing-flags-<label>.json

    Returns a non-empty string — at minimum a "no failures" line.
    """
    issues = getattr(state, "issues", [])
    sprint_label = getattr(state, "sprint_label", "")

    skipped = [i for i in issues if getattr(i, "status", "") == "skipped"]

    bullets: list[str] = []

    # --- What didn't ship ---
    if skipped:
        item_list = ", ".join(
            "#{n} ({c})".format(
                n=getattr(i, "number", "?"),
                c=getattr(i, "category", None) or "unknown",
            )
            for i in skipped
        )
        bullets.append(f"- {len(skipped)} ticket(s) did not ship: {item_list}")

    # --- Failure categories ---
    category_counts: dict[str, int] = {}
    for i in skipped:
        cat = getattr(i, "category", None) or "unknown"
        category_counts[cat] = category_counts.get(cat, 0) + 1
    for cat, count in sorted(category_counts.items()):
        bullets.append(f"- Failure category `{cat}`: {count} ticket(s)")

    # --- Tester rejections ---
    tester_rejected = [
        i for i in issues if getattr(i, "category", None) == "TESTER_REJECTED"
    ]
    if tester_rejected:
        nums = ", ".join(f"#{getattr(i, 'number', '?')}" for i in tester_rejected)
        bullets.append(
            f"- Tester rejected {len(tester_rejected)} ticket(s): {nums}"
        )

    # --- Mis-sizing flags ---
    all_flags = _load_mis_sizing_flags(commander_dir, sprint_label)
    pending_flags = [f for f in all_flags if f.get("status", "pending") == "pending"]
    if pending_flags:
        flag_items = ", ".join(
            "#{n} ({est} estimated → {act} actual)".format(
                n=f.get("issue_number", "?"),
                est=f.get("current_estimate", "?"),
                act=f.get("historical_avg_actual_size", "?"),
            )
            for f in pending_flags
        )
        bullets.append(
            f"- {len(pending_flags)} mis-sizing flag(s) pending review: {flag_items}"
        )

    if not bullets:
        return "- No failures, rejections, or mis-sizing flags recorded for this sprint."

    return "\n".join(bullets)


# ── AC-2: write distilled retro to docs/ ─────────────────────────────────────


def _retro_filename(sprint_label: str, date_str: str) -> str:
    """Return the canonical retro filename for a sprint label and date."""
    return f"{date_str}-{sprint_label}.md"


def write_retro_to_docs(
    state: object,  # SprintState
    docs_uat_dir: Path,
    commander_dir: Optional[Path] = None,
    date_str: Optional[str] = None,
) -> Path:
    """Write a distilled retro to docs/changelog/uat/.

    Naming convention matches the existing directory entries:
        YYYY-MM-DD-sprint-N.md

    Idempotent: if the file already exists, return the existing path without
    rewriting (AC re-run safety).

    Returns the Path of the written (or already-existing) retro file.
    """
    sprint_label = getattr(state, "sprint_label", "unknown")
    start_ts: Optional[str] = getattr(state, "start_timestamp", None)
    issues = getattr(state, "issues", [])

    effective_date = date_str or _bkk_date(start_ts)
    filename = _retro_filename(sprint_label, effective_date)
    retro_path = docs_uat_dir / filename

    # Idempotency guard
    if retro_path.exists():
        return retro_path

    completed = [i for i in issues if getattr(i, "status", "") == "done"]
    skipped = [i for i in issues if getattr(i, "status", "") == "skipped"]
    learnings = derive_key_learnings(state, commander_dir)

    lines: list[str] = [
        f"# UAT Changelog — {sprint_label} ({effective_date})",
        "",
        f"**Sprint:** {sprint_label}",
        f"**Date:** {effective_date}",
        "",
        "---",
        "",
        "## What Shipped",
        "",
    ]
    if completed:
        for i in completed:
            num = getattr(i, "number", "?")
            title = getattr(i, "title", "")
            lines.append(f"- #{num} {title}")
    else:
        lines.append("- Nothing shipped this sprint.")
    lines.append("")

    lines += [
        "## What Didn't Ship",
        "",
    ]
    if skipped:
        for i in skipped:
            num = getattr(i, "number", "?")
            title = getattr(i, "title", "")
            cat = getattr(i, "category", None) or "unknown"
            reason = getattr(i, "skip_reason", None) or ""
            suffix = f": {reason}" if reason else ""
            lines.append(f"- #{num} {title} — `{cat}`{suffix}")
    else:
        lines.append("- All issues shipped.")
    lines.append("")

    lines += [
        "## Key Learnings",
        "",
        learnings,
        "",
        "---",
        "",
        f"_Auto-generated by sprint-manager. Sprint: {sprint_label}._",
        "",
    ]

    docs_uat_dir.mkdir(parents=True, exist_ok=True)
    retro_path.write_text("\n".join(lines), encoding="utf-8")
    return retro_path


# ── AC-3: load recent retros for sprint planner ───────────────────────────────


def load_recent_retros(
    docs_uat_dir: Path,
    n: int = RECENT_RETROS_N,
) -> list[tuple[str, str]]:
    """Load the last N committed retro files from docs/changelog/uat/.

    Returns a list of (filename, content) tuples sorted chronologically
    (oldest first, newest last).  Files are sorted by filename — since names
    are date-prefixed (YYYY-MM-DD-…), lexicographic order is chronological.

    Only files matching the pattern YYYY-MM-DD-sprint-*.md are included;
    _template.md, README.md, and any non-matching file are skipped.

    Returns [] when the directory is absent, empty, or n == 0.
    """
    if not docs_uat_dir.exists() or n <= 0:
        return []

    matched: list[Path] = []
    for path in docs_uat_dir.iterdir():
        if path.name in _SKIP_NAMES:
            continue
        if path.suffix != ".md":
            continue
        if _RETRO_FILENAME_RE.match(path.name):
            matched.append(path)

    matched.sort(key=lambda p: p.name)  # lexicographic = chronological
    recent = matched[-n:]

    result: list[tuple[str, str]] = []
    for path in recent:
        try:
            content = path.read_text(encoding="utf-8")
            result.append((path.name, content))
        except OSError:
            pass
    return result

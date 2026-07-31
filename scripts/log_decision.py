#!/usr/bin/env python3
"""Append a dated ADR entry to docs/decisions/ and update the index.

Naming: YYYY-MM-DD-N-<slug>.md  (BKK dates, N = sequence within the day)

Usage:
  python3 scripts/log_decision.py \\
    --slug   "delete-planned-state" \\
    --context  "planned exists in the enum but nothing writes it." \\
    --options  "A delete it; B leave as-is" \\
    --decision "A — delete planned state" \\
    --consequences "Removed from _SPRINT_STATES and edges." \\
    --status decided \\
    --implemented-by "#1686"

All five content flags are optional — omit any to leave the section blank.
--root overrides the repository root (for testing).

Prints the path of the new ADR file on success.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Bangkok timezone (BKK dates per project convention)
_BKK_TZ = ZoneInfo("Asia/Bangkok")

_DECISIONS_SUBDIR = Path("docs") / "decisions"
_INDEX_NAME = "README.md"
_TEMPLATE_NAME = "TEMPLATE.md"
_VALID_STATUSES = ("proposed", "decided", "superseded", "open")


# ── date helper ──────────────────────────────────────────────────────────────


def _bkk_date() -> str:
    """Return today's date in Bangkok time as YYYY-MM-DD."""
    return datetime.now(tz=_BKK_TZ).strftime("%Y-%m-%d")


# ── sequence number ──────────────────────────────────────────────────────────


def _next_sequence(decisions_dir: Path, date_str: str) -> int:
    """Return the next available sequence number N for date_str.

    Scans existing files whose name starts with date_str-<digits>- and picks
    the highest N already used, then returns N+1.  Returns 1 if no entries
    exist yet for that date.
    """
    prefix = f"{date_str}-"
    pattern = re.compile(rf"^{re.escape(date_str)}-(\d+)-")
    highest = 0
    for path in decisions_dir.iterdir():
        if path.name == _INDEX_NAME or path.name == _TEMPLATE_NAME:
            continue
        m = pattern.match(path.name)
        if m:
            n = int(m.group(1))
            if n > highest:
                highest = n
    return highest + 1


# ── slug sanitiser ───────────────────────────────────────────────────────────


def _sanitise_slug(raw: str) -> str:
    """Convert arbitrary text to a safe kebab-case slug."""
    slug = raw.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


# ── file content ─────────────────────────────────────────────────────────────


def _build_adr(
    filename_stem: str,
    *,
    status: str,
    context: str,
    options: str,
    decision: str,
    consequences: str,
    implemented_by: str,
) -> str:
    """Return the full Markdown content for a new ADR file."""
    lines = [
        f"# {filename_stem}",
        "",
        f"> Status: {status}",
        "",
        "## Context",
        "",
        context or "<!-- describe the situation and problem -->",
        "",
        "## Options",
        "",
        options or "<!-- list options considered -->",
        "",
        "## Decision",
        "",
        decision or "<!-- which option was chosen and why -->",
        "",
        "## Consequences",
        "",
        consequences or "<!-- what changes; follow-up work -->",
        "",
        "## Implemented-by (#N)",
        "",
        implemented_by or "<!-- GitHub issue number(s) -->",
        "",
    ]
    return "\n".join(lines)


# ── index update ─────────────────────────────────────────────────────────────


def _update_index(decisions_dir: Path, filename: str, slug: str, date_str: str) -> None:
    """Add a line for the new ADR to the index (README.md).

    Strategy:
    - Locate the date heading `## YYYY-MM-DD` for today.
      If it does not exist, append it with a blank line separator.
    - Insert the new bullet immediately after the date heading.
    - If the entry is already present (idempotent re-run), skip silently.
    """
    index_path = decisions_dir / _INDEX_NAME
    stem = filename[: -len(".md")] if filename.endswith(".md") else filename
    link_line = f"- [{stem}]({filename}) — {slug}"

    if not index_path.exists():
        _create_index(decisions_dir)

    content = index_path.read_text(encoding="utf-8")

    # Idempotency: skip if already listed
    if f"]({filename})" in content:
        return

    date_heading = f"## {date_str}"

    if date_heading in content:
        # Insert after the heading line
        new_content = content.replace(
            date_heading + "\n",
            date_heading + "\n" + link_line + "\n",
            1,
        )
    else:
        # Append a new date section at the end
        separator = "\n" if content.endswith("\n") else "\n\n"
        new_content = content + separator + date_heading + "\n" + link_line + "\n"

    index_path.write_text(new_content, encoding="utf-8")


def _create_index(decisions_dir: Path) -> None:
    """Create a fresh README.md index when none exists."""
    content = """\
# Decisions

Architecture Decision Records (ADRs) for Commander. One file per decision;
naming `YYYY-MM-DD-N-<slug>.md` (BKK dates, N = sequence within the day so
entries file in order — mirrors `docs/bulk-create/`).

Use `scripts/log_decision.py` or `/decide` to append a new entry in one call.
The template is in `TEMPLATE.md`.

---
"""
    (decisions_dir / _INDEX_NAME).write_text(content, encoding="utf-8")


# ── main ─────────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Return the git repository root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit("Could not determine git repo root")
    return Path(result.stdout.strip())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Append a dated ADR entry to docs/decisions/",
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Short kebab-case label for the decision (will be sanitised)",
    )
    parser.add_argument(
        "--status",
        default="decided",
        choices=_VALID_STATUSES,
        help="Status of the decision (default: decided)",
    )
    parser.add_argument("--context", default="", help="Context section text")
    parser.add_argument("--options", default="", help="Options section text")
    parser.add_argument("--decision", default="", help="Decision section text")
    parser.add_argument("--consequences", default="", help="Consequences section text")
    parser.add_argument(
        "--implemented-by",
        default="",
        dest="implemented_by",
        help="Implemented-by section (e.g. '#1686')",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Override date (YYYY-MM-DD); defaults to today BKK time",
    )
    parser.add_argument(
        "--root",
        default="",
        help="Override repository root path (for testing)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else _repo_root()
    decisions_dir = root / _DECISIONS_SUBDIR
    decisions_dir.mkdir(parents=True, exist_ok=True)

    date_str = args.date if args.date else _bkk_date()
    slug = _sanitise_slug(args.slug)
    seq = _next_sequence(decisions_dir, date_str)

    filename = f"{date_str}-{seq}-{slug}.md"
    stem = filename[: -len(".md")]

    content = _build_adr(
        stem,
        status=args.status,
        context=args.context,
        options=args.options,
        decision=args.decision,
        consequences=args.consequences,
        implemented_by=args.implemented_by,
    )

    adr_path = decisions_dir / filename
    adr_path.write_text(content, encoding="utf-8")

    _update_index(decisions_dir, filename, slug, date_str)

    sys.stdout.write(str(adr_path) + "\n")


if __name__ == "__main__":
    main()

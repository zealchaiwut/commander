"""Service logic for changelog index endpoint (issue #1860).

Public API:
  resolve_clone_root(slug) → Path      raises 404 if unknown project
  list_changelog(clone_root, env, limit) → list[dict]

Each entry has: {env, date, slug, path, title, excerpt}
  env     — "uat" or "prd"
  date    — "YYYY-MM-DD" parsed from filename, or null for malformed names
  slug    — remainder after date prefix, or full stem for malformed
  path    — repo-relative path, e.g. docs/changelog/uat/YYYY-MM-DD-slug.md
  title   — first heading text, stripped of leading #; empty string if none
  excerpt — first non-empty, non-heading paragraph, empty string if none
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# Same resolve_clone_root logic as docs_service — one source of truth.
from routers.docs_service import resolve_clone_root  # noqa: F401

_CHANGELOG_SUBDIR = Path("docs") / "changelog"
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
_ENVS = ("uat", "prd")


def _parse_markdown(text: str) -> tuple[str, str]:
    """Return (title, excerpt) extracted from markdown text.

    title  — first heading text, stripped of leading hashes and spaces.
    excerpt — first non-empty, non-heading paragraph.
    """
    lines = text.splitlines()
    title = ""
    excerpt_lines: list[str] = []
    in_para = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if not title:
                title = stripped.lstrip("#").strip()
            # End scan once we've seen a heading and started collecting
            if in_para:
                break
            continue
        if not stripped:
            if in_para:
                # Blank line ends the paragraph
                break
            continue
        # Non-empty, non-heading line
        in_para = True
        excerpt_lines.append(stripped)

    excerpt = " ".join(excerpt_lines)
    return title, excerpt


def _parse_entry(file: Path, env: str, clone_root: Path) -> dict:
    stem = file.stem  # e.g. "2026-05-26-sprint-6"
    m = _DATE_RE.match(stem)
    if m:
        date: Optional[str] = m.group(1)
        slug = m.group(2)
    else:
        date = None
        slug = stem

    rel_path = str(file.relative_to(clone_root))
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        text = ""

    title, excerpt = _parse_markdown(text)
    return {
        "env": env,
        "date": date,
        "slug": slug,
        "path": rel_path,
        "title": title,
        "excerpt": excerpt,
    }


def list_changelog(
    clone_root: Path,
    env: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Return changelog entries newest-first.

    env   — if given, restrict to that environment ("uat" or "prd").
    limit — if given, cap the result list to that many entries.
    """
    envs_to_scan = [env] if env in _ENVS else list(_ENVS)

    entries: list[dict] = []
    for e in envs_to_scan:
        env_dir = clone_root / _CHANGELOG_SUBDIR / e
        if not env_dir.is_dir():
            continue
        for f in sorted(env_dir.glob("*.md")):
            if not f.is_file() or f.is_symlink():
                continue
            if f.name.startswith("_"):
                continue  # skip _template.md and similar
            entries.append(_parse_entry(f, e, clone_root))

    # Sort: dated entries newest-first, undated entries at the end
    dated = sorted(
        (e for e in entries if e["date"] is not None),
        key=lambda e: e["date"],  # type: ignore[arg-type]
        reverse=True,
    )
    undated = [e for e in entries if e["date"] is None]
    result = dated + undated

    if limit is not None and limit > 0:
        result = result[:limit]

    return result

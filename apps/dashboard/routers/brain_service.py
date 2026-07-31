"""Brain search service — SQLite FTS5 index over docs/** (issue #2028).

Indexes four content sources:
  docs/             — all .md files recursively (source="docs")
  docs/bulk-create/ — planning prompts (source="bulk-create")
  docs/decisions/   — ADR entries (source="decisions")
  docs/changelog/uat/ — committed retros (source="retros")

The index is built in memory on each search call (no background daemon,
no persistent cache file) — cheap for small docs trees.

Public API:
  search(q, docs_root)  → list[dict]   FTS5 keyword search
  get_panels(docs_root) → dict         Panel data for the Brain tab UI
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# ── Repo-root discovery ───────────────────────────────────────────────────────
# brain_service.py lives at apps/dashboard/routers/brain_service.py
# Repo root is four levels up.
_REPO_ROOT = Path(__file__).parents[3]

# ── Ensure services/ is importable ───────────────────────────────────────────
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))  # pragma: no cover


# ── Source constants ──────────────────────────────────────────────────────────
_SOURCE_DOCS = "docs"
_SOURCE_BULK = "bulk-create"
_SOURCE_DECISIONS = "decisions"
_SOURCE_RETROS = "retros"

# Files to skip when collecting docs
_SKIP_NAMES: frozenset[str] = frozenset({"_template.md", "README.md", "TEMPLATE.md"})

# Pattern for retro filenames: YYYY-MM-DD-sprint-*.md
_RETRO_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-sprint-.+\.md$")

# Pattern for ADR filenames: YYYY-MM-DD-N-*.md
_ADR_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+-.+\.md$")

# Decision-needed marker in planning docs
_DECISION_MARKER = "⟶ DECISION"

# Snippet length in characters returned with each hit
_SNIPPET_CHARS = 300


# ── Document collection ───────────────────────────────────────────────────────

def _collect_docs(docs_root: Path) -> list[tuple[str, str, str]]:
    """Collect (path_str, source, content) for all indexed .md files.

    Sources and their discovery logic:
      docs/       — docs_root/docs/**/*.md  (excludes bulk-create/ and decisions/)
      bulk-create — docs_root/docs/bulk-create/**/*.md
      decisions   — docs_root/docs/decisions/**/*.md
      retros      — docs_root/docs/changelog/uat/YYYY-MM-DD-sprint-*.md

    Files in _SKIP_NAMES are always excluded.
    """
    rows: list[tuple[str, str, str]] = []

    docs_dir = docs_root / "docs"
    if not docs_dir.is_dir():
        return rows

    bulk_dir = docs_dir / "bulk-create"
    decisions_dir = docs_dir / "decisions"
    retros_dir = docs_dir / "changelog" / "uat"

    # ── retros ────────────────────────────────────────────────────────────────
    if retros_dir.is_dir():
        for p in sorted(retros_dir.iterdir()):
            if p.name in _SKIP_NAMES or p.suffix != ".md":
                continue
            if _RETRO_FILENAME_RE.match(p.name):
                try:
                    rows.append((
                        str(p.relative_to(docs_root)),
                        _SOURCE_RETROS,
                        p.read_text(encoding="utf-8"),
                    ))
                except OSError:
                    pass

    # ── decisions ─────────────────────────────────────────────────────────────
    if decisions_dir.is_dir():
        for p in sorted(decisions_dir.rglob("*.md")):
            if p.name in _SKIP_NAMES:
                continue
            try:
                rows.append((
                    str(p.relative_to(docs_root)),
                    _SOURCE_DECISIONS,
                    p.read_text(encoding="utf-8"),
                ))
            except OSError:
                pass

    # ── bulk-create ───────────────────────────────────────────────────────────
    if bulk_dir.is_dir():
        for p in sorted(bulk_dir.rglob("*.md")):
            if p.name in _SKIP_NAMES:
                continue
            try:
                rows.append((
                    str(p.relative_to(docs_root)),
                    _SOURCE_BULK,
                    p.read_text(encoding="utf-8"),
                ))
            except OSError:
                pass

    # ── docs (everything else in docs/, excluding the above subdirs) ──────────
    for p in sorted(docs_dir.rglob("*.md")):
        if p.name in _SKIP_NAMES or p.suffix != ".md":
            continue
        # Skip files already covered by more specific sources
        try:
            p.relative_to(bulk_dir)
            continue  # in bulk-create/
        except ValueError:
            pass
        try:
            p.relative_to(decisions_dir)
            continue  # in decisions/
        except ValueError:
            pass
        try:
            p.relative_to(retros_dir)
            if _RETRO_FILENAME_RE.match(p.name):
                continue  # already indexed as retros
        except ValueError:
            pass
        try:
            rows.append((
                str(p.relative_to(docs_root)),
                _SOURCE_DOCS,
                p.read_text(encoding="utf-8"),
            ))
        except OSError:
            pass

    return rows


# ── FTS5 index building ───────────────────────────────────────────────────────

def _build_index(conn: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
    """Create and populate an FTS5 virtual table in the given connection."""
    conn.execute(
        "CREATE VIRTUAL TABLE docs_fts USING fts5(path UNINDEXED, source UNINDEXED, content)"
    )
    conn.executemany(
        "INSERT INTO docs_fts (path, source, content) VALUES (?, ?, ?)",
        rows,
    )


# ── Query sanitization ────────────────────────────────────────────────────────

def _sanitize_q(q: str) -> str | None:
    """Return a safe FTS5 query string, or None when the query is empty/unsearchable.

    FTS5 accepts most operator syntax.  The only protection we apply is to
    reject blank queries (after stripping whitespace) so the caller can return
    an empty result quickly.  Malformed queries (unbalanced quotes, bare AND/OR)
    are caught by the sqlite3.OperationalError guard in search().
    """
    q = q.strip()
    if not q:
        return None
    return q


# ── Public: search ────────────────────────────────────────────────────────────

def search(q: str, docs_root: Path) -> list[dict[str, Any]]:
    """Full-text search over all indexed docs.

    Returns a list of hit dicts, each with:
      {path, source, snippet}

    At most 50 results are returned, ordered by FTS5 relevance rank.
    Malformed queries (unbalanced quotes etc.) return [] rather than raising.
    """
    safe_q = _sanitize_q(q)
    if safe_q is None:
        return []

    rows = _collect_docs(docs_root)
    if not rows:
        return []

    conn = sqlite3.connect(":memory:")
    try:
        _build_index(conn, rows)
        try:
            cur = conn.execute(
                "SELECT path, source, content FROM docs_fts"
                " WHERE docs_fts MATCH ?"
                " ORDER BY rank LIMIT 50",
                (safe_q,),
            )
        except sqlite3.OperationalError:
            # Malformed FTS5 query — return empty result, not a 500
            return []

        hits: list[dict[str, Any]] = []
        for path, source, content in cur.fetchall():
            snippet = _extract_snippet(content, safe_q)
            hits.append({"path": path, "source": source, "snippet": snippet})
        return hits
    finally:
        conn.close()


def _extract_snippet(content: str, q: str) -> str:
    """Return a short context window around the first query term match."""
    # Try to find the first keyword from the query in the content
    # Strip FTS5 operators to get plain words for matching
    words = re.sub(r'["\^*()OR AND NOT+-]', " ", q).split()
    text = content
    pos = -1
    for word in words:
        idx = text.lower().find(word.lower())
        if idx >= 0:
            pos = idx
            break

    if pos < 0:
        # No word found — return the opening of the document
        return (text[:_SNIPPET_CHARS] + "…") if len(text) > _SNIPPET_CHARS else text

    # Context window centred on pos
    start = max(0, pos - _SNIPPET_CHARS // 3)
    end = min(len(text), start + _SNIPPET_CHARS)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


# ── Public: panels ────────────────────────────────────────────────────────────

def get_panels(docs_root: Path) -> dict[str, Any]:
    """Return panel data for the Brain tab's four pre-built panels.

    Panels:
      recent_decisions — last 5 ADR entries (filename + title line)
      open_decisions   — snippets containing the ⟶ DECISION marker
      last_learnings   — ## Key Learnings section from the most recent retro
      backlog_rationale — ADR entries that mention backlog/planned items
    """
    return {
        "recent_decisions": _panel_recent_decisions(docs_root),
        "open_decisions": _panel_open_decisions(docs_root),
        "last_learnings": _panel_last_learnings(docs_root),
        "backlog_rationale": _panel_backlog_rationale(docs_root),
    }


def _panel_recent_decisions(docs_root: Path) -> list[dict[str, str]]:
    """Return the 5 most recent ADR entries with title and path."""
    decisions_dir = docs_root / "docs" / "decisions"
    if not decisions_dir.is_dir():
        return []

    adrs: list[tuple[str, Path]] = []
    for p in decisions_dir.iterdir():
        if p.name in _SKIP_NAMES or p.suffix != ".md":
            continue
        if _ADR_FILENAME_RE.match(p.name):
            adrs.append((p.name, p))

    # Sort descending (newest first)
    adrs.sort(key=lambda t: t[0], reverse=True)
    adrs = adrs[:5]

    result: list[dict[str, str]] = []
    for name, p in adrs:
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        title = _extract_h1(content) or name
        decision = _extract_decision_line(content)
        result.append({
            "path": str(p.relative_to(docs_root)),
            "title": title,
            "decision": decision,
        })
    return result


def _panel_open_decisions(docs_root: Path) -> list[dict[str, str]]:
    """Return snippets from docs that contain the ⟶ DECISION marker."""
    items: list[dict[str, str]] = []
    docs_dir = docs_root / "docs"
    if not docs_dir.is_dir():
        return items

    for p in sorted(docs_dir.rglob("*.md")):
        if p.name in _SKIP_NAMES:
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if _DECISION_MARKER not in content:
            continue
        # Extract the lines containing the marker
        for line in content.splitlines():
            if _DECISION_MARKER in line:
                items.append({
                    "path": str(p.relative_to(docs_root)),
                    "line": line.strip(),
                })
                if len(items) >= 20:
                    return items
    return items


def _panel_last_learnings(docs_root: Path) -> list[str]:
    """Return the ## Key Learnings bullets from the most recent retro."""
    try:
        from services.sprint_manager.retro import load_recent_retros  # noqa: PLC0415
    except ImportError:
        return []

    retros_dir = docs_root / "docs" / "changelog" / "uat"
    pairs = load_recent_retros(retros_dir, n=1)
    if not pairs:
        return []

    _, content = pairs[-1]
    return _extract_key_learnings(content)


def _panel_backlog_rationale(docs_root: Path) -> list[dict[str, str]]:
    """Return ADR entries that mention backlog, planned, or roadmap items."""
    decisions_dir = docs_root / "docs" / "decisions"
    if not decisions_dir.is_dir():
        return []

    keywords = ("backlog", "planned", "roadmap", "future", "todo")
    items: list[dict[str, str]] = []
    for p in sorted(decisions_dir.iterdir()):
        if p.name in _SKIP_NAMES or p.suffix != ".md":
            continue
        try:
            content = p.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        if any(kw in content for kw in keywords):
            try:
                full = p.read_text(encoding="utf-8")
            except OSError:
                continue
            title = _extract_h1(full) or p.name
            items.append({
                "path": str(p.relative_to(docs_root)),
                "title": title,
            })
            if len(items) >= 10:
                break
    return items


# ── Text helpers ──────────────────────────────────────────────────────────────

def _extract_h1(content: str) -> str:
    """Return the first # heading in content, without the # prefix."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _extract_decision_line(content: str) -> str:
    """Return the first line after '## Decision' heading."""
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Decision"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped and not stripped.startswith("#"):
            # Return first non-blank, non-heading line
            return stripped[:200]
    return ""


def _extract_key_learnings(content: str) -> list[str]:
    """Return bullet points from '## Key Learnings' section."""
    in_section = False
    bullets: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Key Learnings"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and (stripped.startswith("- ") or stripped.startswith("* ")):
            bullets.append(stripped[2:].strip())
    return bullets

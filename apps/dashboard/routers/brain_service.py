"""Brain search service — SQLite FTS5 index over docs/** (issue #2028).

Indexes four content sources:
  docs/             — all .md files recursively (source="docs")
  docs/bulk-create/ — planning prompts (source="bulk-create")
  docs/decisions/   — ADR entries (source="decisions")
  docs/changelog/uat/ — committed retros (source="retros")

Content is discovered via ``docs_service.list_docs`` and retrieved via
``docs_service.get_doc`` — the same security-checked reader used by the rest
of the dashboard (path-containment, symlink rejection, UTF-8 validation).
This ensures any future hardening of docs_service applies here automatically
(AC3 reuse requirement).

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


# ── Source classification ─────────────────────────────────────────────────────

def _classify_source(rel_path: str) -> str | None:
    """Return the source category for a relative doc path, or None to skip.

    Paths returned by docs_service.list_docs() are either:
      - Root allowlist files: "README.md", "CHANGELOG.md"  (→ None: skip)
      - Docs tree files:      "docs/<anything>.md"

    Within docs/:
      docs/changelog/uat/YYYY-MM-DD-sprint-*.md → retros
      docs/decisions/**/*.md                    → decisions
      docs/bulk-create/**/*.md                  → bulk-create
      everything else under docs/               → docs
    """
    parts = Path(rel_path).parts

    # Root allowlist (README.md, CHANGELOG.md) — not indexed by brain
    if len(parts) == 1:
        return None

    # All docs_service paths are either root or under docs/
    if parts[0] != "docs":
        return None

    if len(parts) < 2:
        return None

    # Retros: docs/changelog/uat/YYYY-MM-DD-sprint-*.md
    if len(parts) >= 4 and parts[1] == "changelog" and parts[2] == "uat":
        return _SOURCE_RETROS if _RETRO_FILENAME_RE.match(parts[-1]) else None

    # Decisions: docs/decisions/**
    if parts[1] == "decisions":
        return _SOURCE_DECISIONS

    # Bulk-create: docs/bulk-create/**
    if parts[1] == "bulk-create":
        return _SOURCE_BULK

    # Everything else under docs/ (milestones, features, architecture, etc.)
    return _SOURCE_DOCS


# ── Document collection (via docs_service) ────────────────────────────────────

def _collect_docs(docs_root: Path) -> list[tuple[str, str, str]]:
    """Collect (path_str, source, content) for all indexed .md files.

    File discovery uses ``docs_service.list_docs`` (same rules as the docs
    endpoints). Content retrieval uses ``docs_service.get_doc`` which carries
    path-containment, symlink rejection, and UTF-8 validation — the same
    security envelope as the rest of the dashboard (AC3).

    Files in _SKIP_NAMES and files whose source cannot be classified are
    excluded.  Files that raise on get_doc (e.g. a race-condition deletion
    between list and read) are silently skipped.
    """
    from . import docs_service  # noqa: PLC0415 — lazy to match docs_service's own pattern

    try:
        entries = docs_service.list_docs(docs_root)
    except Exception:
        # docs_root missing or unreadable — return empty; search() will return []
        return []

    rows: list[tuple[str, str, str]] = []
    for entry in entries:
        rel_path: str = entry["path"]

        # Skip template / index files before paying the cost of get_doc
        if Path(rel_path).name in _SKIP_NAMES:
            continue

        source = _classify_source(rel_path)
        if source is None:
            continue  # root allowlist or unclassifiable path

        try:
            result = docs_service.get_doc(docs_root, rel_path)
            rows.append((rel_path, source, result["content"]))
        except Exception:
            # HTTPException from security checks, or file gone since listing
            continue

    return rows


# ── FTS5 index building ───────────────────────────────────────────────────────

def _build_index(conn: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
    """Create and populate an FTS5 virtual table in the given connection.

    Raises sqlite3.OperationalError if FTS5 is unavailable or INSERT fails.
    The caller (search()) handles this as an infrastructure error, not a query
    error, and re-raises so it surfaces as a 500 rather than silent [] .
    """
    conn.execute(
        "CREATE VIRTUAL TABLE docs_fts USING fts5(path UNINDEXED, source UNINDEXED, content)"
    )
    conn.executemany(
        "INSERT INTO docs_fts (path, source, content) VALUES (?, ?, ?)",
        rows,
    )


# ── Query sanitization ────────────────────────────────────────────────────────

def _sanitize_q(q: str) -> str | None:
    """Return a sanitized FTS5 query string, or None for empty/blank input.

    Blank queries are rejected early to avoid a cheap-but-pointless FTS5 scan.
    All other sanitization is handled by the OperationalError guard in search()
    — FTS5 raises on unbalanced quotes, bare operators, etc., and that guard
    returns [] rather than propagating the exception.
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

    Error handling:
      - Empty / blank q          → [] (fast path, no index built)
      - No docs found            → []
      - Malformed FTS5 MATCH     → [] (OperationalError from the MATCH call)
      - Index BUILD failure      → re-raised as RuntimeError (surfaces as 500)
        This distinction matters: a bad query silently returns no results
        (correct UX), but a failed index build is an infrastructure problem
        that must not be swallowed and silently return empty results forever.
    """
    safe_q = _sanitize_q(q)
    if safe_q is None:
        return []

    rows = _collect_docs(docs_root)
    if not rows:
        return []

    conn = sqlite3.connect(":memory:")
    try:
        # ── Index build — infrastructure error: surface loudly ────────────────
        try:
            _build_index(conn, rows)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"Brain FTS5 index build failed — FTS5 may be unavailable "
                f"or data is corrupt: {exc}"
            ) from exc

        # ── MATCH query — malformed query: return empty, not 500 ──────────────
        try:
            cur = conn.execute(
                "SELECT path, source, content FROM docs_fts"
                " WHERE docs_fts MATCH ?"
                " ORDER BY rank LIMIT 50",
                (safe_q,),
            )
        except sqlite3.OperationalError:
            # Unbalanced quotes, bare AND/OR/NOT, col:syntax, etc.
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
    words = re.sub(r'["\^*()OR AND NOT+-]', " ", q).split()
    text = content
    pos = -1
    for word in words:
        idx = text.lower().find(word.lower())
        if idx >= 0:
            pos = idx
            break

    if pos < 0:
        return (text[:_SNIPPET_CHARS] + "…") if len(text) > _SNIPPET_CHARS else text

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

    Content retrieval routes through docs_service.get_doc (AC3).
    """
    return {
        "recent_decisions": _panel_recent_decisions(docs_root),
        "open_decisions": _panel_open_decisions(docs_root),
        "last_learnings": _panel_last_learnings(docs_root),
        "backlog_rationale": _panel_backlog_rationale(docs_root),
    }


def _panel_recent_decisions(docs_root: Path) -> list[dict[str, str]]:
    """Return the 5 most recent ADR entries with title and path.

    Discovery via docs_service.list_docs; content via docs_service.get_doc.
    """
    from . import docs_service  # noqa: PLC0415

    try:
        entries = docs_service.list_docs(docs_root)
    except Exception:
        return []

    adrs: list[tuple[str, str]] = []  # (filename, rel_path)
    for entry in entries:
        rel_path = entry["path"]
        parts = Path(rel_path).parts
        if len(parts) < 3 or parts[0] != "docs" or parts[1] != "decisions":
            continue
        name = parts[-1]
        if name in _SKIP_NAMES or not _ADR_FILENAME_RE.match(name):
            continue
        adrs.append((name, rel_path))

    adrs.sort(key=lambda t: t[0], reverse=True)
    adrs = adrs[:5]

    result: list[dict[str, str]] = []
    for name, rel_path in adrs:
        try:
            doc = docs_service.get_doc(docs_root, rel_path)
            content = doc["content"]
        except Exception:
            continue
        title = _extract_h1(content) or name
        decision = _extract_decision_line(content)
        result.append({"path": rel_path, "title": title, "decision": decision})
    return result


def _panel_open_decisions(docs_root: Path) -> list[dict[str, str]]:
    """Return snippets from docs that contain the ⟶ DECISION marker.

    Discovery via docs_service.list_docs; content via docs_service.get_doc.
    """
    from . import docs_service  # noqa: PLC0415

    try:
        entries = docs_service.list_docs(docs_root)
    except Exception:
        return []

    items: list[dict[str, str]] = []
    for entry in sorted(entries, key=lambda e: e["path"]):
        rel_path = entry["path"]
        if Path(rel_path).name in _SKIP_NAMES:
            continue
        try:
            doc = docs_service.get_doc(docs_root, rel_path)
            content = doc["content"]
        except Exception:
            continue
        if _DECISION_MARKER not in content:
            continue
        for line in content.splitlines():
            if _DECISION_MARKER in line:
                items.append({"path": rel_path, "line": line.strip()})
                if len(items) >= 20:
                    return items
    return items


def _panel_last_learnings(docs_root: Path) -> list[str]:
    """Return the ## Key Learnings bullets from the most recent retro.

    Uses load_recent_retros from services/sprint_manager/retro.py (correct
    reuse per task instructions — retro.py owns the "last N retros" logic).
    """
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
    """Return ADR entries that mention backlog, planned, or roadmap items.

    Discovery via docs_service.list_docs; content via docs_service.get_doc.
    """
    from . import docs_service  # noqa: PLC0415

    try:
        entries = docs_service.list_docs(docs_root)
    except Exception:
        return []

    keywords = ("backlog", "planned", "roadmap", "future", "todo")
    items: list[dict[str, str]] = []

    for entry in sorted(entries, key=lambda e: e["path"]):
        rel_path = entry["path"]
        parts = Path(rel_path).parts
        if len(parts) < 3 or parts[0] != "docs" or parts[1] != "decisions":
            continue
        if Path(rel_path).name in _SKIP_NAMES:
            continue
        try:
            doc = docs_service.get_doc(docs_root, rel_path)
            content = doc["content"]
        except Exception:
            continue
        if any(kw in content.lower() for kw in keywords):
            title = _extract_h1(content) or parts[-1]
            items.append({"path": rel_path, "title": title})
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
    """Return the first non-blank body line after '## Decision' heading."""
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Decision"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped and not stripped.startswith("#"):
            return stripped[:200]
    return ""


def _extract_key_learnings(content: str) -> list[str]:
    """Return bullet points from the '## Key Learnings' section."""
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

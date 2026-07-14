"""Service logic for read-only docs endpoints (issue #1859).

Exposes two public functions:
  resolve_clone_root(slug) → Path   raise 404 if unknown project
  list_docs(clone_root)   → list[dict]
  get_doc(clone_root, rel_path) → dict   raise 400/404 on violations
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

_PROJECTS_BASE = Path(os.environ.get("COMMANDER_PROJECTS_BASE", str(Path.home() / "dev")))

_ROOT_ALLOWLIST = {"README.md", "CHANGELOG.md"}
_DOCS_SUBDIR = "docs"


def resolve_clone_root(slug: str) -> Path:
    """Resolve a project slug to the primary clone root on disk.

    Raises HTTPException(404) if the slug does not match any tracked project.
    Supports nested layout (~/dev/<slug>/main/), the prd/uat sub-clone layout
    (~/dev/<slug>/prd/), and flat layout (~/dev/<slug>/ is itself the clone).
    The project root is only used as a fallback when it is a git clone itself —
    a bare container directory (e.g. PRD's ~/dev/commander holding prd/, uat/,
    coder/) would otherwise serve an orphaned stale docs/ tree.
    """
    import projects as _projects  # noqa: PLC0415 — lazy to avoid circular imports
    projs = _projects.load_projects()
    proj = next(
        (p for p in projs if p.get("repo", "").split("/")[-1].lower() == slug.lower()),
        None,
    )
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    project_root = _PROJECTS_BASE / slug
    for sub in ("main", "prd"):
        clone = project_root / sub
        if clone.is_dir() and (clone / ".git").exists():
            return clone
    # Flat layout: the project root itself is the clone (takes precedence over
    # a uat/ sub-clone living inside it, as on the local authoring machine).
    if (project_root / ".git").exists():
        return project_root
    uat_clone = project_root / "uat"
    if uat_clone.is_dir() and (uat_clone / ".git").exists():
        return uat_clone
    raise HTTPException(
        status_code=404,
        detail=f"No git clone found for project '{slug}' under {project_root}",
    )


def list_docs(clone_root: Path) -> list[dict]:
    """Return [{path, size, mtime}] for all allowed .md files in the clone root."""
    results: list[dict] = []

    docs_dir = clone_root / _DOCS_SUBDIR
    if docs_dir.is_dir():
        for p in sorted(docs_dir.rglob("*.md")):
            if p.is_file() and not p.is_symlink():
                rel = p.relative_to(clone_root)
                st = p.stat()
                results.append({
                    "path": str(rel),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })

    for name in sorted(_ROOT_ALLOWLIST):
        f = clone_root / name
        if f.is_file() and not f.is_symlink():
            st = f.stat()
            results.append({
                "path": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })

    return results


def get_doc(clone_root: Path, rel_path: str) -> dict:
    """Validate and read a doc file.

    Security checks (in order):
      1. Reject absolute paths
      2. Reject non-.md extensions
      3. Reject any path segment that is ".."
      4. Resolve to absolute and verify it stays inside clone_root
      5. Verify the resolved path is in the allowed set (docs/** or README/CHANGELOG)
      6. Reject symlinks
      7. Return {path, content}
    """
    # 1. Absolute paths
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")

    # 2. Extension check (catches .env, .py, etc.)
    if not rel_path.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are accessible")

    # 3. Dotdot segments (catches ../../etc/passwd even before resolve)
    segments = rel_path.replace("\\", "/").split("/")
    if ".." in segments:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    # 4. Resolve and verify containment
    resolved_root = clone_root.resolve()
    candidate = (clone_root / rel_path).resolve()
    try:
        rel = candidate.relative_to(resolved_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project root")

    # 5. Allowed-set check: docs/<anything>.md  OR  README.md / CHANGELOG.md
    parts = rel.parts
    in_docs = len(parts) >= 2 and parts[0] == _DOCS_SUBDIR
    is_root_allowlist = len(parts) == 1 and parts[0] in _ROOT_ALLOWLIST
    if not (in_docs or is_root_allowlist):
        raise HTTPException(status_code=400, detail="Path is not in the allowed doc set")

    # 6. No symlinks
    if candidate.is_symlink():
        raise HTTPException(status_code=400, detail="Symlinks are not allowed")

    # 7. File must exist
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        content = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"File contains non-UTF-8 bytes and cannot be decoded: {exc.reason}",
        ) from exc
    return {"path": str(rel), "content": content}

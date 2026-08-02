"""Canonical ``project=`` resolution utility (issue #2064).

Contract
--------
The canonical format for the ``project=`` query parameter across all Commander
API endpoints is **``owner/repo``** — e.g. ``zealchaiwut/commander``.  This
matches what ``/api/home`` publishes in the ``repo`` field and is the natural
identifier used by the GitHub API.

For backwards compatibility, the bare **slug** form (e.g. ``commander``) is
also accepted and normalised centrally here — never per-endpoint.  The
normalisation rule is simple: everything up to and including the last ``/`` is
discarded, so ``zealchaiwut/commander`` and ``commander`` both resolve to the
slug ``commander``.

Resolution failure NEVER silently falls back to a default project.  Passing an
unrecognised ``project=`` value always raises ``HTTPException(404)``.

Public API
----------
resolve_project_path(project: str) -> Path
    Accept ``owner/repo`` or bare slug, validate against the tracked project
    list, and return ``$COMMANDER_PROJECTS_BASE/<slug>``.
    Raises HTTPException(400) for empty input.
    Raises HTTPException(404) for unknown projects.

resolve_project_repo(project: str) -> str
    Accept ``owner/repo`` or bare slug and return the canonical ``owner/repo``
    string from the tracked project list (issue #2136).
    Raises HTTPException(400) for empty input.
    Raises HTTPException(404) for unknown projects.

split_project(project: str) -> tuple[str, str]
    Accept ``owner/repo`` or bare slug and return ``(owner, repo_name)``.
    Thin wrapper over ``resolve_project_repo`` shared by router modules
    (issue #2135 — eliminates the duplicated ``_split_project`` helper).
    Raises HTTPException(400) for empty input.
    Raises HTTPException(404) for unknown projects.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

_PROJECTS_BASE = Path(os.environ.get("COMMANDER_PROJECTS_BASE", str(Path.home() / "dev")))


def _normalise_to_slug(project: str) -> str:
    """Extract the bare slug from an ``owner/repo`` or slug string."""
    stripped = project.strip()
    return stripped.split("/")[-1] if "/" in stripped else stripped


def resolve_project_path(project: str) -> Path:
    """Resolve *project* (``owner/repo`` or bare slug) to its root directory.

    Canonical input format: ``owner/repo`` (e.g. ``zealchaiwut/commander``),
    matching what ``/api/home`` publishes as the ``repo`` field.  The bare
    slug form (e.g. ``commander``) is accepted for backwards compatibility and
    is normalised centrally here, not per-endpoint.

    Returns:
        Path: ``$COMMANDER_PROJECTS_BASE/<slug>``

    Raises:
        HTTPException(400): if *project* is empty or blank.
        HTTPException(404): if *project* does not match any tracked project.
                            Resolution failure NEVER falls back to a default.
    """
    if not project or not project.strip():
        raise HTTPException(status_code=400, detail="project= parameter is required")

    import projects as _projects  # noqa: PLC0415 — lazy to avoid circular imports at load time

    slug = _normalise_to_slug(project)
    projs = _projects.load_projects()
    matched = next(
        (p for p in projs if _normalise_to_slug(p.get("repo", "")).lower() == slug.lower()),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    canonical_slug = _normalise_to_slug(matched.get("repo", slug))
    return _PROJECTS_BASE / canonical_slug


def resolve_project_repo(project: str) -> str:
    """Resolve *project* (``owner/repo`` or bare slug) to canonical ``owner/repo`` string.

    Use this when you need the real GitHub repo path — not just the local directory.
    Bare slugs (e.g. ``commander``) are looked up in the tracked project list and
    returned as ``owner/repo`` (e.g. ``zealchaiwut/commander``), honoring the
    #2064 backward-compatibility contract without the ``slug/slug`` fallback bug.

    Raises:
        HTTPException(400): if *project* is empty or blank.
        HTTPException(404): if *project* does not match any tracked project.
    """
    if not project or not project.strip():
        raise HTTPException(status_code=400, detail="project= parameter is required")

    import projects as _projects  # noqa: PLC0415 — lazy to avoid circular imports at load time

    slug = _normalise_to_slug(project)
    projs = _projects.load_projects()
    matched = next(
        (p for p in projs if _normalise_to_slug(p.get("repo", "")).lower() == slug.lower()),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    return matched["repo"]


def split_project(project: str) -> tuple[str, str]:
    """Resolve *project* (``owner/repo`` or bare slug) to ``(owner, repo_name)``.

    Shared helper for router modules — eliminates the duplicated
    ``_split_project`` one-liner (issue #2135).

    Raises:
        HTTPException(400): if *project* is empty or blank.
        HTTPException(404): if *project* does not match any tracked project.
    """
    canonical = resolve_project_repo(project)
    owner, repo_name = canonical.split("/", 1)
    return owner, repo_name

"""Tests for issue #2136: bare-slug project= on canonical sprint finish routes.

AC verified:
  AC1 - _split_project("commander") resolves to real owner/repo, not ("commander", "commander")
  AC2 - _split_project("owner/repo") still works unchanged
  AC3 - _split_project with unknown slug raises HTTPException(404)
  AC4 - resolve_project_repo is the shared resolver used by both sprint_finish and finish_progress
  AC5 - finish_progress._split_project has the same fix (no slug/slug fallback)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

# Add dashboard root to path
DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))

FAKE_PROJECTS = [{"repo": "zealchaiwut/commander", "name": "Commander"}]


def _patch_projects():
    return patch("projects.load_projects", return_value=FAKE_PROJECTS)


# ── AC1: bare slug resolves to real owner/repo ───────────────────────────────

def test_split_project_bare_slug_sprint_finish():
    """AC1: sprint_finish._split_project('commander') returns ('zealchaiwut', 'commander')."""
    from routers import sprint_finish
    with _patch_projects():
        owner, repo_name = sprint_finish._split_project("commander")
    assert owner == "zealchaiwut"
    assert repo_name == "commander"


def test_split_project_bare_slug_finish_progress():
    """AC5: finish_progress._split_project('commander') also returns ('zealchaiwut', 'commander')."""
    from routers import finish_progress
    with _patch_projects():
        owner, repo_name = finish_progress._split_project("commander")
    assert owner == "zealchaiwut"
    assert repo_name == "commander"


# ── AC2: full owner/repo form still works ────────────────────────────────────

def test_split_project_full_form_sprint_finish():
    """AC2: sprint_finish._split_project('zealchaiwut/commander') still works."""
    from routers import sprint_finish
    with _patch_projects():
        owner, repo_name = sprint_finish._split_project("zealchaiwut/commander")
    assert owner == "zealchaiwut"
    assert repo_name == "commander"


def test_split_project_full_form_finish_progress():
    """AC2: finish_progress._split_project('zealchaiwut/commander') still works."""
    from routers import finish_progress
    with _patch_projects():
        owner, repo_name = finish_progress._split_project("zealchaiwut/commander")
    assert owner == "zealchaiwut"
    assert repo_name == "commander"


# ── AC3: unknown slug raises 404 ─────────────────────────────────────────────

def test_split_project_unknown_raises_404_sprint_finish():
    """AC3: sprint_finish._split_project with unknown slug raises HTTPException(404)."""
    from routers import sprint_finish
    with _patch_projects():
        with pytest.raises(HTTPException) as exc_info:
            sprint_finish._split_project("nonexistent-project")
    assert exc_info.value.status_code == 404


def test_split_project_unknown_raises_404_finish_progress():
    """AC3: finish_progress._split_project with unknown slug raises HTTPException(404)."""
    from routers import finish_progress
    with _patch_projects():
        with pytest.raises(HTTPException) as exc_info:
            finish_progress._split_project("nonexistent-project")
    assert exc_info.value.status_code == 404


# ── AC4: resolve_project_repo is the canonical shared resolver ───────────────

def test_resolve_project_repo_bare_slug():
    """AC4: project_resolver.resolve_project_repo('commander') returns 'zealchaiwut/commander'."""
    import project_resolver
    with _patch_projects():
        result = project_resolver.resolve_project_repo("commander")
    assert result == "zealchaiwut/commander"


def test_resolve_project_repo_full_form():
    """AC4: project_resolver.resolve_project_repo('zealchaiwut/commander') returns 'zealchaiwut/commander'."""
    import project_resolver
    with _patch_projects():
        result = project_resolver.resolve_project_repo("zealchaiwut/commander")
    assert result == "zealchaiwut/commander"


def test_resolve_project_repo_unknown_raises_404():
    """AC4: resolve_project_repo raises HTTPException(404) for unknown project."""
    import project_resolver
    with _patch_projects():
        with pytest.raises(HTTPException) as exc_info:
            project_resolver.resolve_project_repo("nobody-knows-this")
    assert exc_info.value.status_code == 404


# ── Regression: slug/slug form must NOT appear ───────────────────────────────

def test_slug_slug_never_produced_sprint_finish():
    """Regression: _split_project('commander') must not return ('commander', 'commander')."""
    from routers import sprint_finish
    with _patch_projects():
        owner, repo_name = sprint_finish._split_project("commander")
    assert not (owner == "commander" and repo_name == "commander"), (
        "_split_project returned the broken ('commander', 'commander') fallback"
    )


def test_slug_slug_never_produced_finish_progress():
    """Regression: finish_progress._split_project('commander') must not return ('commander', 'commander')."""
    from routers import finish_progress
    with _patch_projects():
        owner, repo_name = finish_progress._split_project("commander")
    assert not (owner == "commander" and repo_name == "commander"), (
        "_split_project returned the broken ('commander', 'commander') fallback"
    )

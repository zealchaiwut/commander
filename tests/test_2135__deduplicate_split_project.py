"""Behavioral tests for issue #2135 — deduplicate _split_project helper.

Acceptance Criteria:
  AC1 — A single `split_project` function lives in project_resolver.py.
  AC2 — Neither sprint_finish.py nor finish_progress.py defines its own
         _split_project; both import from project_resolver.
  AC3 — split_project(owner/repo) returns (owner, repo_name) correctly.
  AC4 — split_project(bare-slug) resolves via project list to (owner, repo_name).
  AC5 — split_project(unknown) raises HTTPException(404).

All tests exercise actual code paths (CLAUDE.md issue #1746 — no source-regex).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")

FAKE_PROJECTS = [
    {"repo": "zealchaiwut/commander", "name": "Commander"},
    {"repo": "zealchaiwut/perf-coach", "name": "Perf Coach"},
]


@pytest.fixture(autouse=True)
def patch_projects(tmp_path):
    with patch("projects.load_projects", return_value=FAKE_PROJECTS):
        yield


# ── AC1: split_project exists in project_resolver ────────────────────────────

class TestSplitProjectExistsInResolver:
    """AC1 — split_project must be a public function in project_resolver."""

    def test_split_project_is_callable(self):
        import project_resolver as pr
        assert callable(getattr(pr, "split_project", None)), (
            "project_resolver must expose a callable split_project"
        )


# ── AC3: canonical owner/repo form ───────────────────────────────────────────

class TestSplitProjectOwnerRepo:
    """AC3 — split_project(owner/repo) returns (owner, repo_name) tuple."""

    def test_canonical_splits_correctly(self):
        import project_resolver as pr
        owner, repo = pr.split_project("zealchaiwut/commander")
        assert owner == "zealchaiwut"
        assert repo == "commander"

    def test_second_project_splits_correctly(self):
        import project_resolver as pr
        owner, repo = pr.split_project("zealchaiwut/perf-coach")
        assert owner == "zealchaiwut"
        assert repo == "perf-coach"


# ── AC4: bare slug resolves via project list ──────────────────────────────────

class TestSplitProjectBareSlug:
    """AC4 — split_project(bare-slug) resolves to real owner via project list."""

    def test_bare_slug_yields_real_owner(self):
        import project_resolver as pr
        owner, repo = pr.split_project("commander")
        assert owner == "zealchaiwut"
        assert repo == "commander"


# ── AC5: unknown project raises 404 ──────────────────────────────────────────

class TestSplitProjectUnknown:
    """AC5 — split_project raises HTTPException(404) for unknown projects."""

    def test_unknown_owner_repo_raises_404(self):
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.split_project("nobody/ghost-project")
        assert exc_info.value.status_code == 404

    def test_unknown_bare_slug_raises_404(self):
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.split_project("ghost-project")
        assert exc_info.value.status_code == 404


# ── AC2: neither router defines its own _split_project ───────────────────────

class TestNoLocalSplitProject:
    """AC2 — both modules import split_project; neither defines _split_project locally."""

    def test_sprint_finish_has_no_local_split_project(self):
        """sprint_finish module must not define its own _split_project function."""
        from routers import sprint_finish
        assert not hasattr(sprint_finish, "_split_project"), (
            "sprint_finish.py still defines _split_project locally — it must import from project_resolver"
        )

    def test_finish_progress_has_no_local_split_project(self):
        """finish_progress module must not define its own _split_project function."""
        from routers import finish_progress
        assert not hasattr(finish_progress, "_split_project"), (
            "finish_progress.py still defines _split_project locally — it must import from project_resolver"
        )

"""Behavioral tests for docs_service.resolve_clone_root layout resolution.

Covers the clone-root fix: a bare container directory (PRD's ~/dev/commander
holding prd/, uat/, coder/) must resolve to the prd/ sub-clone instead of
serving the container's orphaned stale docs/ tree, while the nested (main/)
and flat (root-is-clone) layouts keep their existing behavior.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers import docs_service  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Point docs_service at a tmp projects base with one tracked project."""
    monkeypatch.setattr(docs_service, "_PROJECTS_BASE", tmp_path)

    import projects as projects_module
    monkeypatch.setattr(
        projects_module, "load_projects",
        lambda: [{"repo": "zealchaiwut/commander"}],
    )
    root = tmp_path / "commander"
    root.mkdir()
    return root


def _mk_clone(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


class TestCloneRootLayouts:
    def test_nested_main_layout_wins(self, project):
        main = _mk_clone(project / "main")
        _mk_clone(project / "prd")
        assert docs_service.resolve_clone_root("commander") == main

    def test_container_with_prd_subclone_resolves_to_prd(self, project):
        # PRD layout: root is NOT a clone, holds prd/, uat/, coder/ sub-clones
        prd = _mk_clone(project / "prd")
        _mk_clone(project / "uat")
        (project / "docs").mkdir()  # orphaned stale docs dir must NOT win
        assert docs_service.resolve_clone_root("commander") == prd

    def test_flat_layout_root_is_clone(self, project):
        (project / ".git").mkdir()
        assert docs_service.resolve_clone_root("commander") == project

    def test_flat_root_clone_beats_uat_subclone(self, project):
        # Local authoring layout: root is the main clone AND has uat/ inside it
        (project / ".git").mkdir()
        _mk_clone(project / "uat")
        assert docs_service.resolve_clone_root("commander") == project

    def test_uat_only_container_falls_back_to_uat(self, project):
        uat = _mk_clone(project / "uat")
        assert docs_service.resolve_clone_root("commander") == uat

    def test_container_without_any_clone_404s(self, project):
        (project / "docs").mkdir()
        with pytest.raises(HTTPException) as exc:
            docs_service.resolve_clone_root("commander")
        assert exc.value.status_code == 404

    def test_unknown_project_404s(self, project):
        with pytest.raises(HTTPException) as exc:
            docs_service.resolve_clone_root("nope")
        assert exc.value.status_code == 404

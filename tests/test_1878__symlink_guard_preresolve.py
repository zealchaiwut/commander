"""Tests for issue #1878: symlink guard runs on pre-resolve path (runs against UAT).

AC1: Symlink inside docs/ pointing to file within docs/ is rejected with 400
AC2: Symlink inside docs/ pointing to file outside docs/ is rejected with 400
AC3: Regular .md files under docs/ are served normally (no regression)
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.docs_service as docs_service
from routers.docs import router as docs_router


@pytest.fixture
def client():
    """Create a TestClient against the docs router."""
    app = FastAPI()
    app.include_router(docs_router)
    return TestClient(app)


def _make_test_clone_with_symlinks(tmp_path: Path) -> Path:
    """Create a test clone with symlinks for testing the guard."""
    clone = tmp_path / "test_clone"
    clone.mkdir()
    (clone / ".git").mkdir()

    # Create docs directory with real markdown files
    docs = clone / "docs"
    docs.mkdir()
    (docs / "real_inside.md").write_text("# Real file inside docs\n\nActual content.", encoding="utf-8")

    # Create a markdown file outside docs to test symlink escape
    (clone / "secret_docs.md").write_text("# Secret docs outside\n\nHidden content.", encoding="utf-8")

    # Create a symlink inside docs/ pointing to a file also inside docs/
    # This should now be rejected by the symlink guard (after the fix)
    symlink_inside = docs / "symlink_to_inside.md"
    symlink_inside.symlink_to(docs / "real_inside.md")

    # Create a symlink inside docs/ pointing to a file outside docs/
    # This should be rejected by the symlink guard (and containment check as fallback)
    symlink_outside = docs / "symlink_to_outside.md"
    symlink_outside.symlink_to(clone / "secret_docs.md")

    return clone


def test_1878__symlink_to_file_inside_docs_rejected(tmp_path):
    """AC1: Symlink inside docs/ pointing to file within docs/ is rejected with 400."""
    clone = _make_test_clone_with_symlinks(tmp_path)

    # Debug: verify the symlink exists
    link_path = clone / "docs" / "symlink_to_inside.md"
    assert link_path.is_symlink(), f"Symlink not found at {link_path}"

    # Call get_doc directly instead of through the router
    # to verify the fix is working
    with pytest.raises(HTTPException) as exc_info:
        docs_service.get_doc(clone, "docs/symlink_to_inside.md")

    assert exc_info.value.status_code == 400
    assert "Symlinks are not allowed" in exc_info.value.detail


def test_1878__symlink_to_file_outside_docs_rejected(tmp_path):
    """AC2: Symlink inside docs/ pointing to file outside docs/ is rejected with 400."""
    clone = _make_test_clone_with_symlinks(tmp_path)

    # The symlink exists and points outside docs/ — should be rejected
    # by the containment check (step 5) before the symlink guard (step 6)
    # because the resolved target is not in the allowed set
    with pytest.raises(HTTPException) as exc_info:
        docs_service.get_doc(clone, "docs/symlink_to_outside.md")

    assert exc_info.value.status_code == 400
    # Rejected because the target is not in the allowed doc set
    assert "Path is not in the allowed doc set" in exc_info.value.detail


def test_1878__regular_file_inside_docs_served(tmp_path):
    """AC3: Regular .md files under docs/ are served normally (no regression)."""
    clone = _make_test_clone_with_symlinks(tmp_path)

    # A real file inside docs/ should be served normally
    result = docs_service.get_doc(clone, "docs/real_inside.md")
    assert result["path"] == "docs/real_inside.md"
    assert "Real file inside docs" in result["content"]


def test_1878__resolved_path_is_never_symlink(client, tmp_path, monkeypatch):
    """Verify that resolved paths are never symlinks (the dead-code scenario)."""
    clone = _make_test_clone_with_symlinks(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    # Create a symlink for testing
    docs = clone / "docs"
    test_symlink = docs / "test_symlink.md"
    test_symlink.symlink_to(docs / "real_inside.md")

    # Verify that the resolved path is NOT itself a symlink
    # (Path.resolve() follows all symlinks)
    resolved = test_symlink.resolve()
    assert not resolved.is_symlink(), "Resolved path should not be a symlink (resolve() follows them)"

    # But the pre-resolve path IS a symlink
    assert test_symlink.is_symlink(), "Pre-resolve path should be a symlink"

"""Tests for issue #1879: graceful handling of non-UTF-8 doc files.

AC: When a .md file contains non-UTF-8 bytes, GET /api/projects/{slug}/docs/{path}
    must return a clean HTTP error (422 or 500) with a descriptive detail, not an
    unhandled UnicodeDecodeError traceback masking as an opaque 500.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
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
    app = FastAPI()
    app.include_router(docs_router)
    return TestClient(app)


def _make_clone_with_bad_file(tmp_path: Path) -> Path:
    """Create a minimal clone whose README.md contains invalid UTF-8 bytes."""
    clone = tmp_path / "bad_clone"
    clone.mkdir()
    (clone / ".git").mkdir()
    docs_dir = clone / "docs"
    docs_dir.mkdir()
    # Write a .md file that is NOT valid UTF-8 (0xFF 0xFE are invalid in UTF-8)
    bad_bytes = b"# Title\n\nContent with bad bytes: \xff\xfe\x00"
    (clone / "README.md").write_bytes(bad_bytes)
    return clone


def test_1879__non_utf8_file_returns_clean_http_error(client, tmp_path, monkeypatch):
    """AC: Non-UTF-8 .md file must return a structured HTTP error, not an unhandled 500."""
    clone = _make_clone_with_bad_file(tmp_path)
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/README.md")

    # Must not be a raw 500 from an unhandled exception — it must be a deliberate response
    # with a descriptive detail field (not an empty or framework-generated detail).
    assert r.status_code in (422, 500), (
        f"Expected 422 or 500 for non-UTF-8 file, got {r.status_code}"
    )
    detail = r.json().get("detail", "")
    assert detail, "Response must include a non-empty 'detail' field describing the error"
    # The detail must mention the encoding issue so clients can diagnose it
    assert any(
        keyword in detail.lower()
        for keyword in ("utf-8", "utf8", "unicode", "encoding", "decode")
    ), f"detail does not mention encoding issue: {detail!r}"


def test_1879__valid_utf8_file_still_returns_200(client, tmp_path, monkeypatch):
    """Regression: a valid UTF-8 README.md must still be served as 200 after the fix."""
    clone = tmp_path / "good_clone"
    clone.mkdir()
    (clone / ".git").mkdir()
    (clone / "docs").mkdir()
    (clone / "README.md").write_text("# Good Project\n\nValid UTF-8 content.", encoding="utf-8")
    monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

    r = client.get("/api/projects/test/docs/README.md")

    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "README.md"
    assert "Good Project" in data["content"]

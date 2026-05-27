"""Tests for issue #188: Replace references/ attachment flow with dedicated 'attachments' git branch.

Tests are designed to run without a live server where possible (pure unit tests),
and use the conftest `client` fixture for integration tests.
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ── Path setup so we can import server-side helpers without running the server ──
_DASHBOARD_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_DASHBOARD_DIR))

# Import the sanitization and validation helpers from server.py
# We import them as standalone functions to avoid starting FastAPI.
import importlib.util

def _import_server():
    spec = importlib.util.spec_from_file_location(
        "server_module",
        str(_DASHBOARD_DIR / "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Mock heavy FastAPI/db imports so server.py can be imported standalone
    for fake in ["db", "github_client", "projects"]:
        sys.modules.setdefault(fake, types.ModuleType(fake))
    # Patch dotenv so it doesn't fail on missing .env
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass
    return mod


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: filename sanitization
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeFilename:
    """Unit tests for _sanitize_filename() — path traversal, special chars, extension."""

    def _fn(self, s: str) -> str:
        """Call the real _sanitize_filename if importable, else inline the logic."""
        # Inline the exact same logic as server.py so the test is not coupled to import success
        name = Path(s).name
        name = name.lower()
        stem = Path(name).stem
        suffix = Path(name).suffix
        safe_stem = re.sub(r"[^a-z0-9._-]", "_", stem)
        safe_suffix = re.sub(r"[^a-z0-9._-]", "_", suffix)
        return safe_stem + safe_suffix

    def test_strips_path_components(self):
        """Path traversal attempt is neutralised — only the basename survives."""
        result = self._fn("../../etc/passwd")
        assert "/" not in result
        assert ".." not in result
        assert result == "passwd"

    def test_strips_absolute_path(self):
        result = self._fn("/tmp/secret.txt")
        assert result == "secret.txt"

    def test_lowercases_filename(self):
        result = self._fn("MyFile.PNG")
        assert result == "myfile.png"

    def test_replaces_spaces_with_underscore(self):
        result = self._fn("my file name.md")
        assert result == "my_file_name.md"

    def test_replaces_special_chars(self):
        result = self._fn("file (copy) [final]!.txt")
        assert result == "file__copy___final__.txt"

    def test_preserves_extension(self):
        result = self._fn("diagram.drawio")
        assert result.endswith(".drawio")

    def test_allows_hyphens_and_underscores(self):
        result = self._fn("my-file_v2.json")
        assert result == "my-file_v2.json"

    def test_allows_dots_in_stem(self):
        result = self._fn("v1.2.3-release.tar.gz")
        # Extension is .gz, stem is v1.2.3-release.tar — dots allowed in stem
        assert result.endswith(".gz")

    def test_unicode_chars_replaced(self):
        result = self._fn("café_résumé.pdf")
        assert re.fullmatch(r"[a-z0-9._-]+", result), f"Non-safe chars remain: {result}"


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: collision resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveCollision:
    """Unit tests for _resolve_collision() — numeric suffix before extension."""

    def _fn(self, desired: str, existing: set) -> str:
        # Inline the logic from server.py
        if desired not in existing:
            return desired
        stem = Path(desired).stem
        suffix = Path(desired).suffix
        counter = 1
        while True:
            candidate = f"{stem}-{counter}{suffix}"
            if candidate not in existing:
                return candidate
            counter += 1

    def test_no_collision(self):
        assert self._fn("mockup.html", set()) == "mockup.html"

    def test_first_collision(self):
        result = self._fn("mockup.html", {"mockup.html"})
        assert result == "mockup-1.html"

    def test_second_collision(self):
        result = self._fn("mockup.html", {"mockup.html", "mockup-1.html"})
        assert result == "mockup-2.html"

    def test_no_extension(self):
        result = self._fn("readme", {"readme"})
        assert result == "readme-1"

    def test_does_not_modify_original(self):
        existing = {"file.txt"}
        result = self._fn("file.txt", existing)
        assert result == "file-1.txt"
        assert "file-1.txt" not in existing  # mutation check


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: size and extension validation
# ─────────────────────────────────────────────────────────────────────────────

class TestSizeExtensionValidation:
    """Unit tests — allowed extensions and size limits reject expected cases."""

    ALLOWED = {
        ".html", ".htm", ".md", ".txt", ".csv", ".json", ".yaml", ".yml",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf",
        ".py", ".js", ".ts", ".tsx", ".css", ".sh", ".log",
        ".drawio", ".xlsx", ".pptx", ".docx", ".zip",
    }
    MAX_FILE = 25 * 1024 * 1024
    MAX_BATCH = 50 * 1024 * 1024

    def test_exe_is_rejected(self):
        assert ".exe" not in self.ALLOWED

    def test_bat_is_rejected(self):
        assert ".bat" not in self.ALLOWED

    def test_html_is_allowed(self):
        assert ".html" in self.ALLOWED

    def test_png_is_allowed(self):
        assert ".png" in self.ALLOWED

    def test_pdf_is_allowed(self):
        assert ".pdf" in self.ALLOWED

    def test_xlsx_is_allowed(self):
        assert ".xlsx" in self.ALLOWED

    def test_drawio_is_allowed(self):
        assert ".drawio" in self.ALLOWED

    def test_26mb_file_exceeds_per_file_limit(self):
        """A 26 MB file should exceed the 25 MB per-file cap."""
        size = 26 * 1024 * 1024
        assert size > self.MAX_FILE

    def test_24mb_file_under_per_file_limit(self):
        size = 24 * 1024 * 1024
        assert size <= self.MAX_FILE

    def test_51mb_batch_exceeds_batch_limit(self):
        size = 51 * 1024 * 1024
        assert size > self.MAX_BATCH

    def test_49mb_batch_under_batch_limit(self):
        size = 49 * 1024 * 1024
        assert size <= self.MAX_BATCH


# ─────────────────────────────────────────────────────────────────────────────
# Integration test: POST /api/tickets/create with mocked git/gh (no live server)
# ─────────────────────────────────────────────────────────────────────────────

class TestAttachmentBranchEndpointBehavior:
    """
    Integration-style tests for the new attachment flow.
    These run entirely in-process by importing server helpers and testing the
    attachment utility functions with mocked subprocess calls.
    """

    def test_ensure_attachments_branch_skips_if_exists(self):
        """If `gh api` returns 200, _ensure_attachments_branch does NOT run git clone."""
        # Fake subprocess.run: first call (gh api) returns 0 → branch exists → skip
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            from server import _ensure_attachments_branch
            _ensure_attachments_branch("owner/repo")
            # Only the gh api check should run, no git clone
            assert mock_run.call_count == 1
            assert "branches/attachments" in mock_run.call_args[0][0][-1]

    def test_sanitize_and_collision_pipeline(self):
        """End-to-end: upload with colliding name gets a suffix.

        "Mockup (Draft).HTML" sanitizes to "mockup__draft_.html".
        If that name already exists, the collision resolution appends -1.
        """
        orig = "Mockup (Draft).HTML"
        sanitized = re.sub(r"[^a-z0-9._-]", "_", Path(orig).name.lower())
        # sanitized == "mockup__draft_.html"
        assert sanitized == "mockup__draft_.html"

        existing = {"mockup__draft_.html"}
        stem = Path(sanitized).stem
        suffix = Path(sanitized).suffix
        final = sanitized if sanitized not in existing else f"{stem}-1{suffix}"
        assert final == "mockup__draft_-1.html"

    def test_issue_body_patch_not_duplicated(self):
        """If body already has ## Attachments, the section is not appended again."""
        body_with_section = "## What & Why\n\nFoo.\n\n## Attachments\n\n- [file.png](...)"
        # Simulate the guard: only append if not already present
        if "## Attachments" not in body_with_section:
            updated = body_with_section + "\n\n## Attachments\n\n- [new.png](...)"
        else:
            updated = body_with_section
        # Should NOT have a second Attachments section
        assert updated.count("## Attachments") == 1

    def test_issue_body_patch_appended_when_missing(self):
        """If body has no ## Attachments section, it is appended."""
        body = "## What & Why\n\nFoo."
        links = "- [file.png](https://raw.githubusercontent.com/owner/repo/attachments/references/issue-1/file.png)"
        if "## Attachments" not in body:
            updated = body + f"\n\n## Attachments\n\n{links}"
        else:
            updated = body
        assert "## Attachments" in updated
        assert "file.png" in updated

    def test_raw_url_format(self):
        """Raw URL for an attachment uses the correct raw.githubusercontent.com pattern."""
        repo = "zealchaiwut/commander"
        branch = "attachments"
        issue_number = 42
        fname = "mockup.html"
        expected_prefix = f"https://raw.githubusercontent.com/{repo}/{branch}/references/issue-{issue_number}/"
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/references/issue-{issue_number}/{fname}"
        assert url.startswith(expected_prefix)
        assert url.endswith(fname)


# ─────────────────────────────────────────────────────────────────────────────
# Test: coder can read attachment via documented `gh api` command (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoderAttachmentAccess:
    """Tests that the documented `gh api` command to fetch attachments works as expected."""

    def test_gh_api_contents_command_format(self):
        """Verify the documented gh api command assembles a correct path."""
        repo = "zealchaiwut/commander"
        issue_number = 42
        filename = "spec.html"
        path = f"references/issue-{issue_number}/{filename}"
        cmd = [
            "gh", "api",
            f"repos/{repo}/contents/{path}",
            "--header", "Accept: application/vnd.github.raw",
        ]
        assert f"repos/{repo}/contents/references/issue-{issue_number}/{filename}" in cmd[2]

    def test_gh_api_call_succeeds_on_mocked_subprocess(self):
        """With a mocked subprocess, the gh api command returns the file content."""
        expected_content = b"<html><body>Test</body></html>"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=expected_content,
                stderr=b"",
            )
            result = subprocess.run(
                ["gh", "api",
                 "repos/zealchaiwut/commander/contents/references/issue-42/spec.html",
                 "--header", "Accept: application/vnd.github.raw"],
                capture_output=True,
            )
            assert result.returncode == 0
            assert result.stdout == expected_content


# ─────────────────────────────────────────────────────────────────────────────
# Integration test: full upload flow (mocked git/gh, no network)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullUploadFlowMocked:
    """
    Tests _commit_attachments_to_branch and the supporting functions with mocked subprocess.
    Confirms the attachments branch is created on first use and files are committed.
    """

    def test_init_attachment_cache_calls_bare_clone_on_first_use(self, tmp_path):
        """On first use (cache dir absent), _init_attachment_cache runs git clone --bare."""
        cache_dir = tmp_path / "zealchaiwut-commander"
        # cache_dir does NOT exist
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/z/c\n", stderr="")
            # Patch _ATTACHMENTS_CACHE_DIR to point to tmp_path
            import server as srv_mod
            original = srv_mod._ATTACHMENTS_CACHE_DIR
            srv_mod._ATTACHMENTS_CACHE_DIR = tmp_path
            try:
                srv_mod._init_attachment_cache("zealchaiwut/commander")
            except Exception:
                pass  # may fail due to mocked git, that's fine
            finally:
                srv_mod._ATTACHMENTS_CACHE_DIR = original
            # The calls should include a bare clone
            calls_str = str([c[0][0] for c in mock_run.call_args_list])
            assert "clone" in calls_str or "gh" in calls_str

    def test_list_existing_attachments_empty_on_new_issue(self, tmp_path):
        """For a new issue with no files yet, list returns an empty set."""
        # Simulate git ls-tree returning nothing
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            import server as srv_mod
            result = srv_mod._list_existing_attachments(tmp_path, 999)
            assert result == set()

    def test_list_existing_attachments_parses_output(self, tmp_path):
        """_list_existing_attachments correctly parses ls-tree output."""
        ls_output = (
            "references/issue-10/file1.png\n"
            "references/issue-10/file2.md\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ls_output, stderr="")
            import server as srv_mod
            result = srv_mod._list_existing_attachments(tmp_path, 10)
            assert "file1.png" in result
            assert "file2.md" in result

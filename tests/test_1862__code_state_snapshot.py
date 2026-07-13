"""Tests for issue #1862 — code-state snapshot generation at sprint finish.

AC coverage:
  AC1 — Sprint finish produces docs/architecture/code-state.md with all four
         sections, stamped with sprint label and timestamp
  AC2 — Snapshot generation failure never fails the sprint pipeline
  AC3 — File is committed with the sprint's documenter output (commit invoked)
  AC4 — GET /api/projects/{slug}/docs/docs/architecture/code-state.md serves it
         (via existing sprint-115 docs read API)
  AC5 — Behavioral test: run the generator against a temp worktree, assert all
         four sections present and module map lists routers/ and services/
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
GENERATOR_SCRIPT = REPO_ROOT / "scripts" / "generate_code_state.py"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── helpers ───────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def _setup_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with routers/ and services/ dirs.

    Structure mirrors Commander so the module map includes both 'routers/'
    and 'services/' strings (AC5).
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@x.com")
    _git(repo, "config", "user.name", "Test")

    # Directories the module map must discover
    (repo / "services" / "sprint_manager").mkdir(parents=True)
    (repo / "services" / "sprint_manager" / "sprint_manager.py").write_text("# sm\n")
    (repo / "apps" / "dashboard" / "routers").mkdir(parents=True)
    (repo / "apps" / "dashboard" / "routers" / "__init__.py").write_text("")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "helper.py").write_text("# helper\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("# test\n")
    (repo / "README.md").write_text("# Test\n")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _git_head(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def _run_generator(repo: Path, sprint_label: str = "sprint-test") -> Path:
    """Run generate_code_state.py against a repo; return the output path."""
    head_sha = _git_head(repo)
    result = subprocess.run(
        [
            sys.executable, str(GENERATOR_SCRIPT),
            "--repo-root", str(repo),
            "--sprint-label", sprint_label,
            "--base-sha", head_sha,
            "--head-sha", head_sha,
        ],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"Generator exited {result.returncode}:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return repo / "docs" / "architecture" / "code-state.md"


# ── AC5: behavioral — generator produces correct output ───────────────────────

class TestGeneratorBehavior:
    """AC5 — Run the generator against a temp worktree; assert structural output."""

    def test_all_four_sections_present(self, tmp_path):
        """Generator produces Module Map, Recent Deltas, Hot Files, and metadata."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)

        assert output.exists(), f"Output file not created: {output}"
        content = output.read_text()

        assert "## Module Map" in content, "Missing Module Map section"
        assert "## Recent Deltas" in content, "Missing Recent Deltas section"
        assert "## Hot Files" in content, "Missing Hot Files section"

    def test_module_map_lists_routers_and_services(self, tmp_path):
        """AC5 explicit: module map must mention routers/ and services/."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        content = output.read_text()

        # Locate module map section body
        map_start = content.find("## Module Map")
        assert map_start != -1, "Module Map section not found"

        # Find end of section (next ## heading or EOF)
        rest = content[map_start:]
        next_section = rest.find("\n## ", len("## Module Map"))
        map_body = rest[:next_section] if next_section != -1 else rest

        assert "routers" in map_body, f"'routers' not in Module Map:\n{map_body}"
        assert "services" in map_body, f"'services' not in Module Map:\n{map_body}"

    def test_sprint_label_in_output(self, tmp_path):
        """AC1: output is stamped with sprint label."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo, sprint_label="sprint-999")
        content = output.read_text()
        assert "sprint-999" in content, "Sprint label not found in output"

    def test_timestamp_in_output(self, tmp_path):
        """AC1: output includes a timestamp (ISO 8601 UTC)."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        content = output.read_text()
        # ISO 8601 UTC contains 'T' and 'Z' or '+00:00'
        assert "T" in content and ("Z" in content or "+00:00" in content), (
            f"Timestamp not found in output:\n{content[:300]}"
        )

    def test_output_written_to_docs_architecture(self, tmp_path):
        """Generator writes to docs/architecture/code-state.md by default."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        assert output == repo / "docs" / "architecture" / "code-state.md"
        assert output.is_file()

    def test_generator_idempotent(self, tmp_path):
        """Running the generator twice does not error — overwrites cleanly."""
        repo = _setup_git_repo(tmp_path)
        _run_generator(repo)
        _run_generator(repo)  # must not raise


# ── AC1: four section content ─────────────────────────────────────────────────

class TestSectionContent:
    """AC1 — Each section contains the expected type of content."""

    def test_module_map_has_bullet_entries(self, tmp_path):
        """Module map contains at least one bullet entry."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        content = output.read_text()
        map_start = content.find("## Module Map")
        next_sec = content.find("\n## ", map_start + 1)
        map_body = content[map_start:next_sec] if next_sec != -1 else content[map_start:]
        assert "- " in map_body, "Module Map has no bullet entries"

    def test_recent_deltas_section_present(self, tmp_path):
        """Recent deltas section exists even when base == head (no changes)."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        content = output.read_text()
        assert "## Recent Deltas" in content

    def test_hot_files_section_present(self, tmp_path):
        """Hot files section is generated even for a minimal repo."""
        repo = _setup_git_repo(tmp_path)
        output = _run_generator(repo)
        content = output.read_text()
        assert "## Hot Files" in content


# ── AC2: non-fatal failure ────────────────────────────────────────────────────

class TestNonFatalFailure:
    """AC2 — Snapshot failure never raises; sprint pipeline continues."""

    def test_snapshot_failure_does_not_raise(self, tmp_path):
        """generate_code_state_snapshot must not raise when script fails."""
        from services.sprint_manager.code_state import generate_code_state_snapshot

        # cwd pointing at a non-git directory — script will fail
        bad_cwd = tmp_path / "not_a_repo"
        bad_cwd.mkdir()

        # Must complete without raising
        generate_code_state_snapshot(
            sprint_label="sprint-test",
            sprint_branch="sprint/sprint-test",
            cwd=bad_cwd,
        )

    def test_snapshot_failure_does_not_raise_on_missing_script(self, tmp_path, monkeypatch):
        """generate_code_state_snapshot is non-fatal when generator script is absent."""
        import services.sprint_manager.code_state as cs_mod

        monkeypatch.setattr(cs_mod, "_GENERATOR_SCRIPT", tmp_path / "nonexistent.py")

        bad_cwd = tmp_path / "repo"
        bad_cwd.mkdir()

        # Must not raise
        cs_mod.generate_code_state_snapshot(
            sprint_label="sprint-test",
            sprint_branch="sprint/sprint-test",
            cwd=bad_cwd,
        )

    def test_snapshot_logs_on_failure(self, tmp_path, capsys):
        """Failed snapshot prints a WARNING line."""
        from services.sprint_manager.code_state import generate_code_state_snapshot

        bad_cwd = tmp_path / "not_a_repo"
        bad_cwd.mkdir()

        generate_code_state_snapshot(
            sprint_label="sprint-test",
            sprint_branch="sprint/sprint-test",
            cwd=bad_cwd,
        )
        out = capsys.readouterr().out
        assert "WARNING" in out or "code_state" in out.lower(), (
            f"Expected WARNING in stdout, got:\n{out}"
        )


# ── AC3: file committed ───────────────────────────────────────────────────────

class TestCommitBehavior:
    """AC3 — code-state.md is committed to the sprint branch."""

    def test_commit_code_state_calls_git_add_and_commit(self, tmp_path):
        """_commit_code_state stages and commits the output file."""
        from services.sprint_manager.code_state import _commit_code_state

        repo = _setup_git_repo(tmp_path)

        # Write the file as if the generator ran
        out_path = repo / "docs" / "architecture" / "code-state.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("# Code State\n\n## Module Map\n\n- **`services/`** — sprint mgmt\n")

        # Should stage and commit without error
        committed = _commit_code_state(repo, out_path, "sprint-test")
        assert committed, "Expected _commit_code_state to return True when file changed"

        # Verify via git log
        r = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(repo), capture_output=True, text=True, check=True,
        )
        assert "code-state" in r.stdout.lower() or "sprint-test" in r.stdout, (
            f"Expected commit about code-state in log, got: {r.stdout}"
        )

    def test_commit_code_state_no_commit_when_unchanged(self, tmp_path):
        """_commit_code_state returns False when file has no unstaged changes."""
        from services.sprint_manager.code_state import _commit_code_state

        repo = _setup_git_repo(tmp_path)

        # Write and commit the file first
        out_path = repo / "docs" / "architecture" / "code-state.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("# Code State\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "prior commit")

        # Now call with no additional changes — should not commit again
        committed = _commit_code_state(repo, out_path, "sprint-test")
        assert not committed, "Expected no commit when file is already committed"


# ── AC4: docs API serves the file ────────────────────────────────────────────

class TestDocsApiServesCodeState:
    """AC4 — existing docs read API serves docs/architecture/code-state.md."""

    def test_docs_api_serves_code_state_md(self, tmp_path, monkeypatch):
        """GET /api/projects/{slug}/docs/docs/architecture/code-state.md → 200."""
        import routers.docs_service as docs_service
        from routers.docs import router as docs_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        clone = tmp_path / "myproject"
        arch_dir = clone / "docs" / "architecture"
        arch_dir.mkdir(parents=True)
        (arch_dir / "code-state.md").write_text(
            "# Code State\n\n## Module Map\n\n- **`services/`** — sprint mgmt\n"
        )

        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

        app = FastAPI()
        app.include_router(docs_router)
        client = TestClient(app)

        r = client.get("/api/projects/myproject/docs/docs/architecture/code-state.md")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["path"] == "docs/architecture/code-state.md"
        assert "Module Map" in data["content"]

    def test_docs_listing_includes_code_state(self, tmp_path, monkeypatch):
        """GET /api/projects/{slug}/docs listing includes code-state.md."""
        import routers.docs_service as docs_service
        from routers.docs import router as docs_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        clone = tmp_path / "myproject"
        arch_dir = clone / "docs" / "architecture"
        arch_dir.mkdir(parents=True)
        (arch_dir / "code-state.md").write_text("# Code State\n")

        monkeypatch.setattr(docs_service, "resolve_clone_root", lambda slug: clone)

        app = FastAPI()
        app.include_router(docs_router)
        client = TestClient(app)

        r = client.get("/api/projects/myproject/docs")
        assert r.status_code == 200
        paths = {item["path"] for item in r.json()}
        assert "docs/architecture/code-state.md" in paths, (
            f"code-state.md not in listing: {paths}"
        )

"""Tests for issue #2239: Remove post-sprint auto-dispatched agents.

AC1: post_sprint.py, document_issue.py, and scripts/run_post_sprint.py deleted
AC2: scripts/AGENTS.md index updated so the coverage test stays green
AC3: The documenter agent definition and the /rev and /est slash commands still work standalone
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


class TestAC1FilesDeleted:
    """AC1: The three auto-dispatch modules are removed."""

    def test_post_sprint_py_deleted(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "post_sprint.py"
        assert not path.exists(), (
            "services/sprint_manager/post_sprint.py must be deleted (superseded by manual /rev, /est)"
        )

    def test_document_issue_py_deleted(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "document_issue.py"
        assert not path.exists(), (
            "services/sprint_manager/document_issue.py must be deleted (superseded by manual documenter)"
        )

    def test_run_post_sprint_py_deleted(self):
        path = REPO_ROOT / "scripts" / "run_post_sprint.py"
        assert not path.exists(), (
            "scripts/run_post_sprint.py must be deleted (standalone entry point for removed agents)"
        )


class TestAC2AgentsMdUpdated:
    """AC2: scripts/AGENTS.md no longer indexes the deleted script."""

    def test_run_post_sprint_absent_from_agents_md(self):
        agents_md = REPO_ROOT / "scripts" / "AGENTS.md"
        assert agents_md.exists(), "scripts/AGENTS.md must exist"
        content = agents_md.read_text(encoding="utf-8")
        assert "run_post_sprint.py" not in content, (
            "scripts/AGENTS.md must not reference run_post_sprint.py after deletion"
        )

    def test_coverage_test_still_passes_after_removal(self):
        """Every .py/.sh file under scripts/ must appear in scripts/AGENTS.md."""
        agents_md = REPO_ROOT / "scripts" / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        scripts_dir = REPO_ROOT / "scripts"
        missing = []
        for f in scripts_dir.iterdir():
            if f.is_dir():
                continue
            if f.suffix not in (".py", ".sh"):
                continue
            if f.name not in content:
                missing.append(f.name)
        assert not missing, (
            f"scripts/AGENTS.md is missing entries for: {missing}"
        )


class TestAC3StandaloneToolsWork:
    """AC3: Documenter agent definition and /rev and /est commands are intact."""

    def test_documenter_agent_definition_exists(self):
        path = REPO_ROOT / ".claude" / "agents" / "documenter.md"
        assert path.exists(), (
            ".claude/agents/documenter.md must still exist after post_sprint.py deletion"
        )

    def test_sprint_review_script_exists(self):
        """The /rev command runs sprint_review.py — it must be unaffected."""
        path = REPO_ROOT / "scripts" / "sprint_review.py"
        assert path.exists(), (
            "scripts/sprint_review.py must still exist (backs the /rev command)"
        )

    def test_sprint_estimator_script_exists(self):
        """The /est command runs sprint_estimator.py — it must be unaffected."""
        path = REPO_ROOT / "scripts" / "sprint_estimator.py"
        assert path.exists(), (
            "scripts/sprint_estimator.py must still exist (backs the /est command)"
        )

    def test_sprint_manager_still_importable(self):
        """sprint_manager.py must remain importable after removing post_sprint imports."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-c", "import services.sprint_manager.sprint_manager"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"sprint_manager import failed after post_sprint removal.\n"
            f"stderr: {result.stderr}"
        )

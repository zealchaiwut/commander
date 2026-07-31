"""Tests for issue #2027 — log_decision.py behavioral tests.

Acceptance Criteria covered:
  AC-4a: logging a decision creates a correctly named YYYY-MM-DD-N-<slug>.md
         with all five template sections.
  AC-4b: two decisions on the same date get N=1 then N=2 and neither is
         overwritten.
  AC-4c: the index (README.md) is updated to include the new entry.
  AC-4d: the script creates docs/decisions/ when it does not exist.

All tests run the real script/functions against a tmp_path root — no writes
to the real docs/decisions/ directory.  The git_no_mutation autouse fixture
(copied verbatim from test_2031__false_orphan_sweep.py) asserts that no test
commits to the repository.

BEHAVIORAL REQUIREMENT (CLAUDE.md issue #1746):
  Every test calls the real ``main()`` function (or helpers) with a tmp root
  and asserts observable file output.  No source-text regex checks count as
  AC coverage.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import log_decision  # noqa: E402 — imported after path setup


# ── git-isolation guard (copied from test_2031__false_orphan_sweep.py) ────────


def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Point the script at tmp_path via --root so it never touches the repo."
    )


# ── shared helpers ────────────────────────────────────────────────────────────


_FIXED_DATE = "2026-01-15"
_ALL_SECTIONS = ("## Context", "## Options", "## Decision", "## Consequences", "## Implemented-by (#N)")


def _run(tmp_root: Path, slug: str, date: str = _FIXED_DATE, **kwargs) -> Path:
    """Call log_decision.main() with a tmp root and return the created file path."""
    argv = [
        "--slug", slug,
        "--date", date,
        "--root", str(tmp_root),
    ]
    for key, val in kwargs.items():
        argv += [f"--{key}", val]

    captured = []
    original_write = sys.stdout.write

    def _capture(s):
        captured.append(s)
        return original_write(s)

    # Capture stdout (the path printed by main)
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        log_decision.main(argv)
        output = sys.stdout.getvalue().strip()
    finally:
        sys.stdout = old_stdout

    return Path(output)


# ── AC-4a: correct filename + all five sections ───────────────────────────────


class TestDecisionFileCreated:
    """AC-4a: logging a decision creates YYYY-MM-DD-N-<slug>.md with all sections."""

    def test_filename_matches_naming_convention(self, tmp_path):
        """File is named YYYY-MM-DD-1-<slug>.md on first entry for the date."""
        adr_path = _run(tmp_path, "my-decision", date="2026-01-15")
        assert adr_path.name == "2026-01-15-1-my-decision.md", (
            f"Expected '2026-01-15-1-my-decision.md', got '{adr_path.name}'"
        )

    def test_file_is_in_decisions_dir(self, tmp_path):
        """The created file lives inside docs/decisions/."""
        adr_path = _run(tmp_path, "some-slug", date="2026-01-15")
        assert adr_path.parent == tmp_path / "docs" / "decisions"

    def test_all_five_sections_present(self, tmp_path):
        """All five required ADR template sections appear in the file."""
        adr_path = _run(
            tmp_path, "full-adr",
            date="2026-01-15",
            context="The situation.",
            options="A do X; B do Y.",
            decision="A — do X.",
            consequences="X is now done.",
        )
        content = adr_path.read_text(encoding="utf-8")
        for section in _ALL_SECTIONS:
            assert section in content, (
                f"Section '{section}' missing from ADR.\nContent:\n{content}"
            )

    def test_section_content_is_written(self, tmp_path):
        """Content passed via flags appears in the correct sections."""
        adr_path = _run(
            tmp_path, "with-content",
            date="2026-01-15",
            context="Context text here.",
            decision="We chose option A.",
            consequences="Follow-up: ticket filed.",
        )
        content = adr_path.read_text(encoding="utf-8")
        assert "Context text here." in content
        assert "We chose option A." in content
        assert "Follow-up: ticket filed." in content

    def test_status_line_in_file(self, tmp_path):
        """Status value from --status flag appears in the file header."""
        adr_path = _run(tmp_path, "open-question", date="2026-01-15", status="open")
        content = adr_path.read_text(encoding="utf-8")
        assert "> Status: open" in content, (
            f"Expected '> Status: open' in content.\nContent:\n{content}"
        )

    def test_default_status_is_decided(self, tmp_path):
        """When --status is omitted, status defaults to 'decided'."""
        adr_path = _run(tmp_path, "default-status", date="2026-01-15")
        content = adr_path.read_text(encoding="utf-8")
        assert "> Status: decided" in content


# ── AC-4b: sequence numbers don't collide ────────────────────────────────────


class TestSequenceNumbering:
    """AC-4b: two decisions on same date get N=1 then N=2; neither is overwritten."""

    def test_first_entry_gets_n1(self, tmp_path):
        """First entry for a date is numbered 1."""
        adr_path = _run(tmp_path, "first-entry", date="2026-02-01")
        assert adr_path.name == "2026-02-01-1-first-entry.md"

    def test_second_entry_gets_n2(self, tmp_path):
        """Second entry for the same date is numbered 2."""
        _run(tmp_path, "first-entry", date="2026-02-01")
        adr_path2 = _run(tmp_path, "second-entry", date="2026-02-01")
        assert adr_path2.name == "2026-02-01-2-second-entry.md"

    def test_first_entry_not_overwritten(self, tmp_path):
        """The first file is not overwritten when the second is created."""
        adr_path1 = _run(tmp_path, "original", date="2026-02-01")
        original_content = adr_path1.read_text(encoding="utf-8")
        _run(tmp_path, "another-one", date="2026-02-01")
        after_content = adr_path1.read_text(encoding="utf-8")
        assert original_content == after_content, (
            "First ADR was modified when second was created!"
        )

    def test_three_entries_get_sequential_numbers(self, tmp_path):
        """Three entries on the same date get N=1, N=2, N=3."""
        p1 = _run(tmp_path, "alpha", date="2026-03-10")
        p2 = _run(tmp_path, "beta", date="2026-03-10")
        p3 = _run(tmp_path, "gamma", date="2026-03-10")
        assert p1.name == "2026-03-10-1-alpha.md"
        assert p2.name == "2026-03-10-2-beta.md"
        assert p3.name == "2026-03-10-3-gamma.md"

    def test_different_dates_each_restart_at_n1(self, tmp_path):
        """N resets to 1 for a different date."""
        p1 = _run(tmp_path, "entry-a", date="2026-04-01")
        p2 = _run(tmp_path, "entry-b", date="2026-04-02")
        assert p1.name == "2026-04-01-1-entry-a.md"
        assert p2.name == "2026-04-02-1-entry-b.md"


# ── AC-4c: index updated ──────────────────────────────────────────────────────


class TestIndexUpdated:
    """AC-4c: the README.md index is updated to include the new entry."""

    def test_index_created_if_missing(self, tmp_path):
        """A README.md index is created when none exists."""
        _run(tmp_path, "new-entry", date="2026-05-01")
        index = tmp_path / "docs" / "decisions" / "README.md"
        assert index.exists(), "README.md index was not created."

    def test_new_entry_linked_in_index(self, tmp_path):
        """The new ADR filename appears as a link in the index."""
        _run(tmp_path, "indexed-entry", date="2026-05-01")
        index = (tmp_path / "docs" / "decisions" / "README.md").read_text(encoding="utf-8")
        assert "2026-05-01-1-indexed-entry.md" in index, (
            f"Entry not found in index.\nIndex:\n{index}"
        )

    def test_date_heading_in_index(self, tmp_path):
        """The date heading (## YYYY-MM-DD) appears in the index."""
        _run(tmp_path, "headed-entry", date="2026-05-15")
        index = (tmp_path / "docs" / "decisions" / "README.md").read_text(encoding="utf-8")
        assert "## 2026-05-15" in index, f"Date heading missing.\nIndex:\n{index}"

    def test_two_entries_both_in_index(self, tmp_path):
        """Both entries on the same date appear in the index."""
        _run(tmp_path, "first", date="2026-06-01")
        _run(tmp_path, "second", date="2026-06-01")
        index = (tmp_path / "docs" / "decisions" / "README.md").read_text(encoding="utf-8")
        assert "2026-06-01-1-first.md" in index
        assert "2026-06-01-2-second.md" in index

    def test_index_update_is_idempotent(self, tmp_path):
        """Re-running the script does not duplicate the index entry."""
        decisions_dir = tmp_path / "docs" / "decisions"
        decisions_dir.mkdir(parents=True)
        # Pre-create the ADR so the script does not create a new one,
        # but point the index update at an existing entry.
        _run(tmp_path, "repeated", date="2026-06-10")
        index_before = (decisions_dir / "README.md").read_text(encoding="utf-8")
        count_before = index_before.count("2026-06-10-1-repeated.md")

        # A second run with the same slug on the same date creates N=2 (new file),
        # but the index must not add duplicate lines for the N=1 file.
        _run(tmp_path, "repeated", date="2026-06-10")
        index_after = (decisions_dir / "README.md").read_text(encoding="utf-8")
        count_after = index_after.count("2026-06-10-1-repeated.md")

        assert count_before == count_after == 1, (
            f"Index entry was duplicated: appeared {count_after} times after second run."
        )


# ── AC-4d: creates docs/decisions/ when missing ──────────────────────────────


class TestDirectoryCreation:
    """AC-4d: script creates docs/decisions/ when it does not exist."""

    def test_creates_decisions_directory(self, tmp_path):
        """docs/decisions/ is created when absent."""
        decisions_dir = tmp_path / "docs" / "decisions"
        assert not decisions_dir.exists(), "Pre-condition: directory must not exist"
        _run(tmp_path, "creates-dir", date="2026-07-01")
        assert decisions_dir.exists(), "docs/decisions/ was not created."
        assert decisions_dir.is_dir()

    def test_creates_nested_docs_path(self, tmp_path):
        """docs/ and docs/decisions/ are both created if neither exists."""
        docs_dir = tmp_path / "docs"
        assert not docs_dir.exists()
        _run(tmp_path, "nested-creation", date="2026-07-01")
        assert (tmp_path / "docs" / "decisions").is_dir()

    def test_file_created_in_new_directory(self, tmp_path):
        """The ADR file is placed in the newly created directory."""
        adr_path = _run(tmp_path, "new-dir-file", date="2026-07-01")
        assert adr_path.exists(), f"ADR file was not created: {adr_path}"
        assert adr_path.parent == tmp_path / "docs" / "decisions"


# ── slug sanitisation ─────────────────────────────────────────────────────────


class TestSlugSanitisation:
    """The script sanitises slugs to safe kebab-case."""

    def test_spaces_become_hyphens(self, tmp_path):
        adr_path = _run(tmp_path, "slug with spaces", date="2026-08-01")
        assert "slug-with-spaces" in adr_path.name

    def test_uppercase_lowercased(self, tmp_path):
        adr_path = _run(tmp_path, "My-Decision", date="2026-08-01")
        assert "my-decision" in adr_path.name

    def test_special_chars_removed(self, tmp_path):
        adr_path = _run(tmp_path, "decision (v2)!", date="2026-08-01")
        assert "decision-v2" in adr_path.name

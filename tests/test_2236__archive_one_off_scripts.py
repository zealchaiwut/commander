"""Tests for issue #2236 — archive eight completed one-off migration scripts.

AC1  All eight scripts moved to scripts/archive/; none remain in scripts/
AC2  scripts/AGENTS.md no longer indexes the archived scripts
     (test_2057 coverage gate stays green — verified by that test suite;
     here we check AGENTS.md doesn't still list the removed scripts)
AC3  No remaining production-code caller (outside tests/ and scripts/archive/)
     references any moved script by name
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
ARCHIVE_DIR = SCRIPTS_DIR / "archive"
AGENTS_MD = SCRIPTS_DIR / "AGENTS.md"

MOVED_SCRIPTS = [
    "migrate_repo_structure.py",
    "rollback_repo_structure.sh",
    "migrate_to_separate_dbs.py",
    "repair_sprint_collisions.py",
    "audit_sprint_terminal_state_drift.py",
    "export_to_neon.py",
    "migrate_sprints_to_neon.py",
    "backfill_sprint_project.py",
]


def test_ac1_scripts_present_in_archive():
    """AC1 — every moved script must exist in scripts/archive/."""
    missing = [s for s in MOVED_SCRIPTS if not (ARCHIVE_DIR / s).exists()]
    assert not missing, (
        "These scripts are missing from scripts/archive/:\n  "
        + "\n  ".join(missing)
    )


def test_ac1_scripts_absent_from_scripts_root():
    """AC1 — none of the moved scripts may remain in scripts/ (only archive/)."""
    still_present = [s for s in MOVED_SCRIPTS if (SCRIPTS_DIR / s).exists()]
    assert not still_present, (
        "These scripts still exist in scripts/ (should be in archive/ only):\n  "
        + "\n  ".join(still_present)
    )


def test_ac2_agents_md_does_not_index_moved_scripts():
    """AC2 — scripts/AGENTS.md must not list any of the eight archived scripts as active entries."""
    text = AGENTS_MD.read_text()
    # The scripts should not appear as bullet entries in the main index.
    # Comments/archive notes are acceptable, but active bullet lines are not.
    bullet_lines = [ln for ln in text.splitlines() if ln.strip().startswith("- `")]
    indexed = []
    for script in MOVED_SCRIPTS:
        if any(script in ln for ln in bullet_lines):
            indexed.append(script)
    assert not indexed, (
        "These archived scripts are still listed as active entries"
        " in scripts/AGENTS.md:\n  "
        + "\n  ".join(indexed)
    )


def _non_archive_python_files() -> list[Path]:
    """All .py files outside tests/, venv/, .git/, and scripts/archive/."""
    result = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if not path.is_file():
            continue
        parts = path.parts
        if "tests" in parts or "venv" in parts or ".git" in parts:
            continue
        try:
            path.relative_to(ARCHIVE_DIR)
            continue
        except ValueError:
            pass
        result.append(path)
    return result


def _non_comment_lines(source: str) -> list[str]:
    """Return only non-comment, non-blank source lines."""
    return [
        ln for ln in source.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_ac3_no_import_of_moved_scripts():
    """AC3 — no production code imports any moved script by module name."""
    # Module names derived from script stems (e.g. repair_sprint_collisions)
    moved_stems = {Path(s).stem for s in MOVED_SCRIPTS}
    offenders: list[str] = []
    for path in _non_archive_python_files():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in _non_comment_lines(source):
            for stem in moved_stems:
                if re.search(
                    rf"\bimport\s+{re.escape(stem)}\b"
                    rf"|from\s+\S*\s+import\s+{re.escape(stem)}\b",
                    line,
                ):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: imports {stem}"
                    )
    assert not offenders, (
        "Non-archive code imports archived scripts:\n  "
        + "\n  ".join(offenders)
    )

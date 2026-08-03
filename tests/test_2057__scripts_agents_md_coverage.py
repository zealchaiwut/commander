"""Tests for issue #2057 — scripts/AGENTS.md must index every script.

AC coverage:
  AC1 — scripts/AGENTS.md references every .py and .sh file in scripts/
         (excluding scripts/archive/) with a one-line purpose and primary args
  AC2 — AGENTS.md groups scripts by role (ticket lifecycle / sprint lifecycle /
         project setup / maintenance / reporting)
  AC3 — Scripts that mutate remote state (GitHub, Neon) or local DB are
         marked with a [GH], [Neon], or [DB] tag
  AC4 — This test IS the enforcement gate: it fails if any script in scripts/
         is absent from the index, preventing future drift
  AC5 — AGENTS.md explicitly says archive/ is excluded from the requirement

Enforcement rationale (AC4): a pytest test is the right gate here because it
runs automatically in CI on every push; a --check flag must be remembered and
invoked manually, which is exactly the kind of friction that caused the drift
in the first place.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
AGENTS_MD = SCRIPTS_DIR / "AGENTS.md"

# Files that are not "scripts" and should not be required in the index
_NON_SCRIPT_NAMES = {
    "AGENTS.md",
    "com.commander.daily-report.plist",
}

# Directories whose contents are excluded
_EXCLUDED_DIRS = {"archive", "test_fixtures"}

REQUIRED_GROUPS = [
    "ticket lifecycle",
    "sprint lifecycle",
    "project setup",
    "maintenance",
    "reporting",
]

# Scripts known to mutate remote state or local DB — each must carry a marker
MUTATION_MARKERS = {
    # GitHub-mutating scripts
    "create_ticket.py": "[GH]",
    "update_ticket.py": "[GH]",
    "comment_ticket.py": "[GH]",
    "approve_ticket.py": "[GH]",
    "reject_ticket.py": "[GH]",
    "finish_feature.py": "[GH]",
    "start_feature.py": "[GH]",
    "post_test_report.py": "[GH]",
    "promote_to_master.py": "[GH]",
    "release.py": "[GH]",
    "deduplicate_labels.py": "[GH]",
    "seed_test_issues.py": "[GH]",
    # Neon-mutating scripts
    "export_to_neon.py": "[Neon]",
    "migrate_sprints_to_neon.py": "[Neon]",
    # DB-mutating scripts (local SQLite)
    "resync_issues_mirror.py": "[DB]",
    "backfill_agent_runs_project.py": "[DB]",
    "backfill_sprint_project.py": "[DB]",
    "repair_sprint_collisions.py": "[DB]",
    "repair_sprint_inbox_from_github.py": "[DB]",
    "repair_sprint_lineage.py": "[DB]",
    "migrate_to_separate_dbs.py": "[DB]",
}


def _get_scripts() -> set[str]:
    """Return all .py and .sh filenames in scripts/ not in excluded dirs."""
    names: set[str] = set()
    for path in SCRIPTS_DIR.iterdir():
        if path.is_dir() and path.name in _EXCLUDED_DIRS:
            continue
        if path.is_file() and path.suffix in {".py", ".sh"} and path.name not in _NON_SCRIPT_NAMES:
            names.add(path.name)
    return names


def _agents_md_text() -> str:
    return AGENTS_MD.read_text()


def test_agents_md_exists():
    """AC1 — the file must exist."""
    assert AGENTS_MD.exists(), "scripts/AGENTS.md is missing"


def test_all_scripts_indexed():
    """AC1/AC4 — every .py and .sh script in scripts/ appears in AGENTS.md."""
    text = _agents_md_text()
    missing = []
    for name in sorted(_get_scripts()):
        if name not in text:
            missing.append(name)
    assert not missing, (
        f"These scripts are present in scripts/ but absent from scripts/AGENTS.md "
        f"(add them to the appropriate group):\n  " + "\n  ".join(missing)
    )


def test_grouped_by_role():
    """AC2 — AGENTS.md must have all five role-group headings."""
    text = _agents_md_text().lower()
    missing = [g for g in REQUIRED_GROUPS if g not in text]
    assert not missing, (
        f"scripts/AGENTS.md is missing these role-group headings: {missing}"
    )


def test_mutation_markers_present():
    """AC3 — every known remote/DB-mutating script must have a mutation marker."""
    text = _agents_md_text()
    failures = []
    for script, marker in MUTATION_MARKERS.items():
        # Find the line(s) mentioning this script
        lines = [ln for ln in text.splitlines() if script in ln]
        if not lines:
            failures.append(f"{script}: not indexed at all (should have {marker})")
            continue
        if not any(marker in ln for ln in lines):
            failures.append(f"{script}: indexed but missing {marker} marker")
    assert not failures, "Mutation markers missing:\n  " + "\n  ".join(failures)


def test_archive_explicitly_excluded():
    """AC5 — AGENTS.md must explicitly mention the archive/ exclusion."""
    text = _agents_md_text().lower()
    assert "archive" in text, (
        "scripts/AGENTS.md must explicitly state that scripts/archive/ is excluded "
        "from the index requirement"
    )
